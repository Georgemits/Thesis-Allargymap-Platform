"""
mongo_importer.py — Import Open-Meteo combined CSV files into MongoDB.

Reads *_combined_*.csv files from data_collection/output/ and inserts
documents into the `env_snapshots` collection of the allergymap database.

Document shape (matches backend/app/models/env_snapshot.py):
{
    "city":      str,
    "timestamp": datetime (UTC),
    "location":  {"type": "Point", "coordinates": [lon, lat]},   # GeoJSON
    "weather":   { temperature_2m, relative_humidity_2m, ... },
    "pollen":    { grass_pollen, olive_pollen, ... },
    "air_quality": { pm10, pm2_5, dust, european_aqi, ... },
}

Duplicate handling: upsert on (city, timestamp) — safe to run repeatedly.

Usage (CLI):
    python mongo_importer.py                         # import all CSVs in output/
    python mongo_importer.py --file output/Athens_combined_20260314_120000.csv
    python mongo_importer.py --uri mongodb://localhost:27017/allergymap

Programmatic:
    from mongo_importer import import_dataframe
    import_dataframe(combined_df, mongo_uri="mongodb://localhost:27017/allergymap")
"""

import argparse
import math
import sys
from datetime import timezone
from pathlib import Path

import pandas as pd
from pymongo import MongoClient, UpdateOne
from pymongo.errors import BulkWriteError

# ---------------------------------------------------------------------------
# Column classification
# (must stay in sync with AQI_VARS / WEATHER_VARS in open_meteo_fetcher.py)
# ---------------------------------------------------------------------------

WEATHER_COLS = {
    "temperature_2m",
    "relative_humidity_2m",
    "apparent_temperature",
    "precipitation",
    "wind_speed_10m",
    "wind_direction_10m",
    "pressure_msl",
    "cloud_cover",
    "uv_index",
}

POLLEN_COLS = {
    "alder_pollen",
    "birch_pollen",
    "grass_pollen",
    "mugwort_pollen",
    "olive_pollen",
    "ragweed_pollen",
}

AIR_QUALITY_COLS = {
    "pm10",
    "pm2_5",
    "dust",
    "european_aqi",
    "european_aqi_pm10",
    "european_aqi_pm2_5",
    "european_aqi_ozone",
    "european_aqi_nitrogen_dioxide",
}

OUTPUT_DIR = Path(__file__).parent / "output"


# ---------------------------------------------------------------------------
# .env loader (no external dependency needed for a simple KEY=VALUE file)
# ---------------------------------------------------------------------------

def _load_dotenv(env_path: Path) -> None:
    """Parse a .env file and set variables in os.environ (if not already set)."""
    import os
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def get_mongo_uri() -> str:
    """
    Return MONGO_URI from environment.
    Loads data_collection/.env (then backend/.env as fallback) if present.
    Defaults to mongodb://localhost:27017/allergymap.
    """
    import os
    _load_dotenv(Path(__file__).parent / ".env")
    _load_dotenv(Path(__file__).parent.parent / "backend" / ".env")
    return os.environ.get("MONGO_URI", "mongodb://localhost:27017/allergymap")


# ---------------------------------------------------------------------------
# Row → document conversion
# ---------------------------------------------------------------------------

def _nan_to_none(val):
    """Convert float NaN to None so pymongo stores null instead of crashing."""
    if isinstance(val, float) and math.isnan(val):
        return None
    return val


def _extract_nested(row: pd.Series, col_set: set) -> dict:
    """Return a dict of {col: value} for columns that exist in the row."""
    return {
        col: _nan_to_none(row[col])
        for col in col_set
        if col in row.index
    }


def row_to_document(row: pd.Series) -> dict:
    """Convert a single combined-CSV row to a MongoDB env_snapshot document."""
    ts = pd.to_datetime(row["datetime"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=timezone.utc)

    lat = float(row["latitude"])
    lon = float(row["longitude"])

    return {
        "city":      str(row["location"]),
        "timestamp": ts.to_pydatetime(),
        "location":  {
            "type":        "Point",
            "coordinates": [lon, lat],   # GeoJSON: [longitude, latitude]
        },
        "latitude":  lat,
        "longitude": lon,
        "weather":      _extract_nested(row, WEATHER_COLS),
        "pollen":       _extract_nested(row, POLLEN_COLS),
        "air_quality":  _extract_nested(row, AIR_QUALITY_COLS),
    }


def df_to_documents(df: pd.DataFrame) -> list[dict]:
    """Convert an entire combined DataFrame to a list of documents."""
    return [row_to_document(row) for _, row in df.iterrows()]


# ---------------------------------------------------------------------------
# MongoDB write
# ---------------------------------------------------------------------------

def import_dataframe(df: pd.DataFrame, mongo_uri: str | None = None) -> int:
    """
    Upsert all rows of `df` into the env_snapshots collection.

    Returns the number of documents inserted or modified.
    Uses bulk upserts keyed on (city, timestamp) to avoid duplicates.
    """
    if df.empty:
        print("  [mongo] DataFrame is empty — nothing to import.")
        return 0

    if mongo_uri is None:
        mongo_uri = get_mongo_uri()

    docs = df_to_documents(df)

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    try:
        # Verify connection early with a clear error message
        client.admin.command("ping")
    except Exception as exc:
        print(f"  [mongo] Cannot connect to MongoDB ({mongo_uri}): {exc}", file=sys.stderr)
        client.close()
        raise

    db_name = MongoClient(mongo_uri).get_default_database().name if "?" not in mongo_uri else "allergymap"
    # Extract db name from URI (e.g. mongodb://host:27017/allergymap)
    db_name = mongo_uri.rstrip("/").rsplit("/", 1)[-1].split("?")[0] or "allergymap"
    db = client[db_name]
    collection = db["env_snapshots"]

    operations = [
        UpdateOne(
            filter={"city": doc["city"], "timestamp": doc["timestamp"]},
            update={"$set": doc},
            upsert=True,
        )
        for doc in docs
    ]

    try:
        result = collection.bulk_write(operations, ordered=False)
        affected = result.upserted_count + result.modified_count
        print(f"  [mongo] {result.upserted_count} inserted, {result.modified_count} updated "
              f"({len(docs)} rows processed)")
        return affected
    except BulkWriteError as bwe:
        print(f"  [mongo] Bulk write error: {bwe.details}", file=sys.stderr)
        raise
    finally:
        client.close()


# ---------------------------------------------------------------------------
# CSV file helpers
# ---------------------------------------------------------------------------

def import_csv_file(csv_path: Path, mongo_uri: str | None = None) -> int:
    """Load a single combined CSV file and import it into MongoDB."""
    print(f"\n{'='*60}")
    print(f"Importing: {csv_path.name}")
    df = pd.read_csv(csv_path, parse_dates=["datetime"])
    return import_dataframe(df, mongo_uri)


def import_all_from_output(
    output_dir: Path = OUTPUT_DIR,
    mongo_uri: str | None = None,
) -> int:
    """
    Find all *_combined_*.csv files in output_dir and import them.
    Returns the total number of documents inserted/updated.
    """
    csv_files = sorted(output_dir.glob("*_combined_*.csv"))
    if not csv_files:
        print(f"No *_combined_*.csv files found in {output_dir}")
        return 0

    print(f"Found {len(csv_files)} combined CSV file(s) in {output_dir}")
    total = 0
    for csv_path in csv_files:
        try:
            total += import_csv_file(csv_path, mongo_uri)
        except Exception as exc:
            print(f"  [ERROR] {csv_path.name}: {exc}", file=sys.stderr)

    print(f"\nDone. Total documents inserted/updated: {total}")
    return total


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import Open-Meteo combined CSV files into the allergymap MongoDB.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python mongo_importer.py
  python mongo_importer.py --file output/Athens_combined_20260314_120000.csv
  python mongo_importer.py --output-dir /custom/path --uri mongodb://localhost:27017/allergymap
        """,
    )
    parser.add_argument(
        "--file", type=Path, metavar="CSV_PATH",
        help="Import a single specific combined CSV file (default: all in output/)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=OUTPUT_DIR,
        help=f"Folder to scan for *_combined_*.csv files (default: {OUTPUT_DIR})",
    )
    parser.add_argument(
        "--uri", dest="mongo_uri", metavar="MONGO_URI",
        help="MongoDB connection string (overrides MONGO_URI env var and .env file)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    mongo_uri = args.mongo_uri or get_mongo_uri()

    if args.file:
        if not args.file.exists():
            print(f"Error: file not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        import_csv_file(args.file, mongo_uri)
    else:
        import_all_from_output(args.output_dir, mongo_uri)


if __name__ == "__main__":
    main()

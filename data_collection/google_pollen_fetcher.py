"""
Google Maps Platform Pollen API fetcher for the AllergyMap crowdsensing thesis platform.

Fetches the daily **Universal Pollen Index (UPI, 0-5)** forecast per location via the
`forecast:lookup` endpoint: an overall index per pollen type (grass / tree / weed) plus
a per-plant breakdown (olive, birch, alder, ragweed, ...) with in-season flags and
category labels ("Low", "Moderate", "High", ...).

This is the *primary* pollen source for AllergyMap v2 (see pollen_source.py for the
selection + Open-Meteo fallback logic). It does not provide weather or air-quality
data — those keep coming from Open-Meteo (open_meteo_fetcher.py) regardless of which
pollen source is active.

Requires GOOGLE_POLLEN_API_KEY (env var / backend/.env / data_collection/.env).
Never hard-code the key; see load_api_key() below.

Supports:
  - Forecast mode only : Google's API caps `days` at 5 (today .. today+4). There is no
    historical / archive endpoint — for past dates use Open-Meteo instead.
  - Multi-location      : all Greek cities in GREEK_LOCATIONS (re-exported from
                           open_meteo_fetcher for a single source of truth).

Usage (CLI):
    python google_pollen_fetcher.py                        # forecast, all cities
    python google_pollen_fetcher.py --city Athens
    python google_pollen_fetcher.py --lat 37.98 --lon 23.73 --name MyCity
    python google_pollen_fetcher.py --days 3
    python google_pollen_fetcher.py --list-cities

Output:
    data_collection/output/<name>_googlepollen_<timestamp>.csv   (one row per day)
    data_collection/output/<name>_googlepollen_raw_<timestamp>.json

Scale note (see pollen_source.py docstring for the full comparison):
    Google's UPI is a discrete 0-5 daily category per plant, computed by an undisclosed
    proprietary model. Open-Meteo reports continuous hourly grains/m3 concentrations
    from the CAMS European pollen model. There is no official linear conversion between
    the two, so AllergyMap stores both values side by side instead of converting.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv is a pinned dependency
    load_dotenv = None

from open_meteo_fetcher import GREEK_LOCATIONS

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
POLLEN_API_URL = "https://pollen.googleapis.com/v1/forecast:lookup"
MAX_FORECAST_DAYS = 5  # hard limit enforced by the Google Pollen API

OUTPUT_DIR = Path(__file__).parent / "output"

# Google plant codes (from `plantInfo[].code`) that map 1:1 onto the six species
# Open-Meteo already reports in grains/m3. Anything Google returns that is *not*
# in this map (e.g. OAK, PINE, ASH, JUNIPER, COTTONWOOD, ...) is still kept, just
# under its own lowercase code, so no data is silently dropped.
PLANT_CODE_MAP = {
    "GRAMINALES": "grass",
    "OLIVE": "olive",
    "BIRCH": "birch",
    "ALDER": "alder",
    "RAGWEED": "ragweed",
    "MUGWORT": "mugwort",
}


# ---------------------------------------------------------------------------
# API key loading
# ---------------------------------------------------------------------------

def load_api_key() -> str:
    """
    Return GOOGLE_POLLEN_API_KEY from the environment.

    Loads data_collection/.env first (if present), then backend/.env as a
    fallback, using python-dotenv (`load_dotenv` never overrides a variable
    that is already set, so the first match wins). Returns "" when unset --
    callers are responsible for handling the missing-key case (e.g. falling
    back to Open-Meteo).
    """
    if load_dotenv is not None:
        load_dotenv(Path(__file__).parent / ".env")
        load_dotenv(Path(__file__).parent.parent / "backend" / ".env")
    return os.environ.get("GOOGLE_POLLEN_API_KEY", "")


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_forecast(
    lat: float,
    lon: float,
    api_key: str,
    days: int = MAX_FORECAST_DAYS,
    language_code: str = "en",
) -> dict:
    """
    Call the Pollen API `forecast:lookup` endpoint for one location.

    Raises requests.HTTPError on a non-2xx response (e.g. invalid key, quota
    exceeded) and ValueError if `api_key` is empty.
    """
    if not api_key:
        raise ValueError("GOOGLE_POLLEN_API_KEY is not set")

    days = max(1, min(days, MAX_FORECAST_DAYS))
    resp = requests.get(
        POLLEN_API_URL,
        params={
            "key": api_key,
            "location.longitude": lon,
            "location.latitude": lat,
            "days": days,
            "languageCode": language_code,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Parsing / normalization
# ---------------------------------------------------------------------------

def _index_info(entry: dict) -> dict:
    """Extract {value, category, in_season} from a pollenTypeInfo/plantInfo entry."""
    idx = entry.get("indexInfo") or {}
    return {
        "value": idx.get("value"),
        "category": idx.get("category"),
        "in_season": entry.get("inSeason"),
    }


def parse_daily_info(raw: dict) -> list[dict]:
    """
    Flatten the raw Pollen API response into one dict per forecast day:

        {
            "date": "YYYY-MM-DD",
            "types": {"grass": {...}, "tree": {...}, "weed": {...}},
            "plants": {"olive": {...}, "graminales": {...}, "oak": {...}, ...},
        }

    `types` comes from `pollenTypeInfo` (overall index per GRASS/TREE/WEED).
    `plants` comes from `plantInfo`, keyed by lowercase Google plant code
    (mapped through PLANT_CODE_MAP where an Open-Meteo equivalent exists).
    """
    days = []
    for day in raw.get("dailyInfo", []):
        d = day.get("date", {})
        iso_date = date(d["year"], d["month"], d["day"]).isoformat()

        types = {}
        for entry in day.get("pollenTypeInfo", []):
            code = entry.get("code", "").lower()
            if code:
                types[code] = _index_info(entry)

        plants = {}
        for entry in day.get("plantInfo", []):
            code = entry.get("code", "")
            key = PLANT_CODE_MAP.get(code, code.lower())
            if key:
                plants[key] = _index_info(entry)

        days.append({"date": iso_date, "types": types, "plants": plants})
    return days


def build_dataframe(daily: list[dict], location_name: str, lat: float, lon: float) -> pd.DataFrame:
    """Flatten parsed daily records into a DataFrame (one row per day)."""
    rows = []
    for day in daily:
        row = {
            "date": day["date"],
            "location": location_name,
            "latitude": lat,
            "longitude": lon,
        }
        for type_name, info in day["types"].items():
            row[f"upi_type_{type_name}"] = info["value"]
        for plant_name, info in day["plants"].items():
            row[f"upi_{plant_name}"] = info["value"]
            row[f"upi_{plant_name}_category"] = info["category"]
            row[f"upi_{plant_name}_in_season"] = info["in_season"]
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def save_outputs(raw: dict, df: pd.DataFrame, name: str, output_dir: Path = OUTPUT_DIR) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if not df.empty:
        p = output_dir / f"{name}_googlepollen_{ts}.csv"
        df.to_csv(p, index=False)
        print(f"  [pollen UPI]  {p}")

    p = output_dir / f"{name}_googlepollen_raw_{ts}.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    print(f"  [raw JSON]    {p}")


# ---------------------------------------------------------------------------
# High-level entry points
# ---------------------------------------------------------------------------

def run_for_location(
    name: str,
    lat: float,
    lon: float,
    api_key: str,
    days: int = MAX_FORECAST_DAYS,
    output_dir: Path = OUTPUT_DIR,
) -> pd.DataFrame:
    """Fetch, parse, save, and return the daily UPI DataFrame for one location."""
    print(f"\n{'='*60}")
    print(f"Location : {name}  ({lat}, {lon})")
    print("Mode     : forecast (Google Pollen UPI)")

    raw = fetch_forecast(lat, lon, api_key, days=days)
    daily = parse_daily_info(raw)
    df = build_dataframe(daily, name, lat, lon)
    save_outputs(raw, df, name, output_dir)

    print(f"  [rows]        {len(df)} daily UPI records")
    return df


def run_all_greek_cities(
    api_key: str,
    days: int = MAX_FORECAST_DAYS,
    output_dir: Path = OUTPUT_DIR,
) -> pd.DataFrame:
    """Fetch Google Pollen UPI for all predefined Greek cities."""
    frames = []
    for city, coords in GREEK_LOCATIONS.items():
        try:
            df = run_for_location(
                name=city,
                lat=coords["latitude"],
                lon=coords["longitude"],
                api_key=api_key,
                days=days,
                output_dir=output_dir,
            )
            frames.append(df)
        except Exception as exc:
            print(f"  [ERROR] {city}: {exc}", file=sys.stderr)

    if not frames:
        return pd.DataFrame()

    all_df = pd.concat(frames, ignore_index=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)
    p = output_dir / f"ALL_GREECE_googlepollen_{ts}.csv"
    all_df.to_csv(p, index=False)
    print(f"\n{'='*60}")
    print(f"All cities saved -> {p}")
    print(f"Total rows: {len(all_df)}")
    return all_df


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch daily pollen UPI (0-5) from the Google Maps Platform Pollen API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python google_pollen_fetcher.py
  python google_pollen_fetcher.py --city Athens
  python google_pollen_fetcher.py --lat 37.98 --lon 23.73 --name "Custom City"
  python google_pollen_fetcher.py --days 3
  python google_pollen_fetcher.py --list-cities
        """,
    )
    parser.add_argument(
        "--days", type=int, default=MAX_FORECAST_DAYS,
        help=f"Forecast days, 1-{MAX_FORECAST_DAYS} (Google's hard limit). Default: {MAX_FORECAST_DAYS}.",
    )
    parser.add_argument("--city", metavar="CITY_NAME", help=f"Single city. Available: {', '.join(GREEK_LOCATIONS)}")
    parser.add_argument("--lat", type=float, help="Custom latitude (requires --lon and --name)")
    parser.add_argument("--lon", type=float, help="Custom longitude (requires --lat and --name)")
    parser.add_argument("--name", metavar="LOCATION_NAME", help="Name for the custom location")
    parser.add_argument(
        "--output-dir", type=Path, default=OUTPUT_DIR,
        help=f"Directory for output files (default: {OUTPUT_DIR})",
    )
    parser.add_argument("--list-cities", action="store_true", help="Print available Greek cities and exit")
    parser.add_argument(
        "--api-key", dest="api_key", default=None,
        help="Override GOOGLE_POLLEN_API_KEY from the environment (not recommended; prefer .env)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.list_cities:
        print("Available Greek cities:")
        for city, coords in GREEK_LOCATIONS.items():
            print(f"  {city:<20} lat={coords['latitude']}, lon={coords['longitude']}")
        return

    api_key = args.api_key or load_api_key()
    if not api_key:
        print(
            "Error: GOOGLE_POLLEN_API_KEY is not set. Add it to backend/.env or "
            "data_collection/.env, or pass --api-key.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.lat is not None or args.lon is not None:
        if args.lat is None or args.lon is None or not args.name:
            print("Error: --lat, --lon, and --name are all required together.", file=sys.stderr)
            sys.exit(1)
        run_for_location(args.name, args.lat, args.lon, api_key, days=args.days, output_dir=args.output_dir)

    elif args.city:
        city = args.city.strip()
        if city not in GREEK_LOCATIONS:
            print(f"Error: '{city}' not found. Use --list-cities to see options.", file=sys.stderr)
            sys.exit(1)
        coords = GREEK_LOCATIONS[city]
        run_for_location(city, coords["latitude"], coords["longitude"], api_key, days=args.days, output_dir=args.output_dir)

    else:
        run_all_greek_cities(api_key, days=args.days, output_dir=args.output_dir)


if __name__ == "__main__":
    main()

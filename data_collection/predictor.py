"""
predictor.py — ML time-series forecasting for the AllergyMap platform.

Trains a RandomForestRegressor with lag features per city × variable and
generates a 7-day (168-hour) hourly forecast, saving results to MongoDB.

Design: proof-of-concept for thesis — prioritises simplicity over accuracy.
  - Features: lag_24h, lag_48h, lag_72h, rolling_mean_24h, hour, dayofweek, month
  - Model: RandomForestRegressor (n_estimators=100)
  - Forecasting: iterative one-step-ahead (feeds predictions back as lags)
  - Duplicate handling: upsert on (city, variable, forecast_timestamp)

Usage (standalone):
    python predictor.py                             # all cities, all variables
    python predictor.py --city Athens
    python predictor.py --city Athens --variable grass_pollen
    python predictor.py --source mongo --city Athens
    python predictor.py --no-mongo                  # print only, skip MongoDB
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OUTPUT_DIR = Path(__file__).parent / "output"

VARIABLES = [
    "grass_pollen",
    "olive_pollen",
    "temperature_2m",
    "relative_humidity_2m",
]

GREEK_CITIES = [
    "Athens", "Thessaloniki", "Patras", "Heraklion",
    "Ioannina", "Larissa", "Volos", "Rhodes", "Chania", "Alexandroupoli",
]

FORECAST_HOURS = 168        # 7 days
LAG_HOURS = [24, 48, 72]
ROLL_WINDOW = 24
MIN_TRAINING_ROWS = 72      # need at least 72 h of actual data
MODEL_NAME = "RandomForest"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_from_csv(city: str, output_dir: Path = OUTPUT_DIR) -> pd.DataFrame:
    """Load all *_combined_*.csv files for a city and merge into one DataFrame."""
    files = sorted(output_dir.glob(f"{city}_combined_*.csv"))
    if not files:
        return pd.DataFrame()
    dfs = [pd.read_csv(f, parse_dates=["datetime"]) for f in files]
    df = pd.concat(dfs, ignore_index=True)
    df = df.drop_duplicates(subset=["datetime"]).sort_values("datetime")
    df = df.set_index("datetime")
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def load_from_mongo(city: str, mongo_uri: str) -> pd.DataFrame:
    """Query env_snapshots for a city and return a flat DataFrame."""
    from pymongo import MongoClient

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    db_name = mongo_uri.rstrip("/").rsplit("/", 1)[-1].split("?")[0] or "allergymap"
    db = client[db_name]
    docs = list(db.env_snapshots.find({"city": city}).sort("timestamp", 1))
    client.close()

    if not docs:
        return pd.DataFrame()

    rows = []
    for doc in docs:
        row = {"datetime": doc["timestamp"]}
        for subdict in [doc.get("weather", {}), doc.get("pollen", {}), doc.get("air_quality", {})]:
            if subdict:
                row.update(subdict)
        rows.append(row)

    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.drop_duplicates(subset=["datetime"]).sort_values("datetime")
    df = df.set_index("datetime")
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------

def build_training_data(series: pd.Series) -> tuple[pd.DataFrame, pd.Series]:
    """
    Build lag-based feature matrix from a univariate time series.

    Features  : lag_24, lag_48, lag_72, roll_mean_24, hour, dayofweek, month
    Target    : value at time t
    """
    df = pd.DataFrame({"value": series})
    for lag in LAG_HOURS:
        df[f"lag_{lag}"] = df["value"].shift(lag)
    df["roll_mean_24"] = df["value"].shift(1).rolling(ROLL_WINDOW).mean()
    df["hour"]       = df.index.hour
    df["dayofweek"]  = df.index.dayofweek
    df["month"]      = df.index.month
    df = df.dropna()

    feature_cols = [f"lag_{h}" for h in LAG_HOURS] + ["roll_mean_24", "hour", "dayofweek", "month"]
    return df[feature_cols], df["value"]


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train_model(series: pd.Series) -> RandomForestRegressor:
    """
    Train a RandomForestRegressor on the series.
    Raises ValueError when there is not enough data.
    """
    clean = series.dropna()
    if len(clean) < MIN_TRAINING_ROWS:
        raise ValueError(
            f"Not enough data: {len(clean)} rows < {MIN_TRAINING_ROWS} minimum"
        )
    # Forward-fill short gaps (max 3 consecutive NaN)
    series = series.ffill(limit=3)

    X, y = build_training_data(series)
    if len(X) < 10:
        raise ValueError(f"Only {len(X)} training samples after feature engineering")

    model = RandomForestRegressor(
        n_estimators=100,
        max_depth=10,
        min_samples_leaf=2,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X, y)
    return model


# ---------------------------------------------------------------------------
# Forecasting
# ---------------------------------------------------------------------------

def forecast(
    model: RandomForestRegressor,
    seed_series: pd.Series,
    hours: int = FORECAST_HOURS,
) -> list[dict]:
    """
    Iteratively forecast `hours` steps ahead using the trained model.

    Seed: last max(LAG_HOURS)=72 actual values to initialise the rolling window.
    Each predicted value is appended to the window and used as a lag in the
    next step.

    Returns a list of dicts: [{"forecast_timestamp": datetime, "predicted_value": float}, ...]
    """
    required = max(LAG_HOURS)
    seed = seed_series.ffill(limit=3).tail(required)
    window = list(seed.values)          # grows by 1 each iteration
    last_ts = seed.index[-1]            # pd.Timestamp, tz-naive

    predictions = []
    for i in range(1, hours + 1):
        ts = last_ts + timedelta(hours=i)   # pd.Timestamp

        # Pull lags from the rolling window (fall back to earliest value if short)
        lag24 = window[-24] if len(window) >= 24 else window[0]
        lag48 = window[-48] if len(window) >= 48 else window[0]
        lag72 = window[-72] if len(window) >= 72 else window[0]
        roll24 = float(np.mean(window[-24:])) if len(window) >= 24 else float(np.mean(window))

        x = [[lag24, lag48, lag72, roll24, ts.hour, ts.weekday(), ts.month]]
        pred = float(model.predict(x)[0])
        pred = max(0.0, pred)   # pollen / AQI cannot be negative

        predictions.append({
            "forecast_timestamp": ts.to_pydatetime(),
            "predicted_value": round(pred, 4),
        })
        window.append(pred)

    return predictions


# ---------------------------------------------------------------------------
# MongoDB persistence
# ---------------------------------------------------------------------------

def save_predictions_to_mongo(
    city: str,
    variable: str,
    forecasts: list[dict],
    mongo_uri: str,
) -> int:
    """Upsert forecast documents into the `predictions` collection."""
    from pymongo import MongoClient
    from pymongo.operations import UpdateOne

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    db_name = mongo_uri.rstrip("/").rsplit("/", 1)[-1].split("?")[0] or "allergymap"
    db = client[db_name]
    collection = db["predictions"]

    generated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    ops = [
        UpdateOne(
            filter={
                "city": city,
                "variable": variable,
                "forecast_timestamp": f["forecast_timestamp"],
            },
            update={"$set": {
                "city": city,
                "variable": variable,
                "forecast_timestamp": f["forecast_timestamp"],
                "predicted_value": f["predicted_value"],
                "model_used": MODEL_NAME,
                "generated_at": generated_at,
            }},
            upsert=True,
        )
        for f in forecasts
    ]

    try:
        result = collection.bulk_write(ops, ordered=False)
        print(
            f"  [mongo] {result.upserted_count} inserted, "
            f"{result.modified_count} updated "
            f"({len(forecasts)} forecasts)"
        )
        return result.upserted_count + result.modified_count
    finally:
        client.close()


# ---------------------------------------------------------------------------
# High-level runners
# ---------------------------------------------------------------------------

def run_prediction(
    city: str,
    variable: str,
    df: pd.DataFrame,
    mongo_uri: str | None = None,
    save_to_mongo: bool = True,
) -> list[dict]:
    """
    Train + forecast for one city × variable pair.
    Saves to MongoDB if save_to_mongo=True and mongo_uri is provided.
    Returns the forecast list (empty on failure).
    """
    if variable not in df.columns:
        print(f"  [SKIP] {variable}: column not found in data")
        return []

    series = df[variable].dropna()
    if len(series) < MIN_TRAINING_ROWS:
        print(f"  [SKIP] {variable}: {len(series)} rows (need ≥{MIN_TRAINING_ROWS})")
        return []

    print(f"  [{variable}] training on {len(series)} rows… ", end="", flush=True)
    try:
        model = train_model(series)
    except ValueError as exc:
        print(f"\n  [SKIP] {exc}")
        return []

    forecasts = forecast(model, series)
    print(f"done → {len(forecasts)} hourly predictions")

    if save_to_mongo and mongo_uri:
        try:
            save_predictions_to_mongo(city, variable, forecasts, mongo_uri)
        except Exception as exc:
            print(f"  [mongo] Save failed: {exc}", file=sys.stderr)

    return forecasts


def run_city(
    city: str,
    variables: list[str] | None = None,
    source: str = "csv",
    output_dir: Path = OUTPUT_DIR,
    mongo_uri: str | None = None,
    save_to_mongo: bool = True,
) -> dict[str, list[dict]]:
    """
    Run predictions for all variables for one city.

    Parameters
    ----------
    city        : city name (must match data filenames / MongoDB documents)
    variables   : list of variable names to predict (default: VARIABLES)
    source      : 'csv' or 'mongo'
    output_dir  : path to CSV output directory (used when source='csv')
    mongo_uri   : MongoDB connection string
    save_to_mongo : whether to persist results to MongoDB

    Returns dict mapping variable name → forecast list.
    """
    if variables is None:
        variables = VARIABLES

    print(f"\n{'='*60}")
    print(f"City: {city}  |  source: {source}")

    if source == "mongo":
        if not mongo_uri:
            print("  [ERROR] --source mongo requires MONGO_URI", file=sys.stderr)
            return {}
        df = load_from_mongo(city, mongo_uri)
    else:
        df = load_from_csv(city, output_dir)

    if df.empty:
        print(f"  [SKIP] No data found for {city}")
        return {}

    print(f"  Loaded {len(df)} rows  "
          f"({df.index.min().date()} → {df.index.max().date()})")

    results: dict[str, list[dict]] = {}
    for var in variables:
        forecasts = run_prediction(city, var, df, mongo_uri, save_to_mongo)
        if forecasts:
            results[var] = forecasts

    return results


# ---------------------------------------------------------------------------
# MONGO_URI helper (reuses mongo_importer logic when available)
# ---------------------------------------------------------------------------

def _get_mongo_uri() -> str:
    try:
        from mongo_importer import get_mongo_uri
        return get_mongo_uri()
    except ImportError:
        pass

    import os
    for env_path in [
        Path(__file__).parent / ".env",
        Path(__file__).parent.parent / "backend" / ".env",
    ]:
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("MONGO_URI="):
                        val = line.partition("=")[2].strip().strip('"').strip("'")
                        os.environ.setdefault("MONGO_URI", val)

    return os.environ.get("MONGO_URI", "mongodb://localhost:27017/allergymap")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AllergyMap ML predictor — train RandomForest and forecast environmental variables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python predictor.py
  python predictor.py --city Athens
  python predictor.py --city Athens --variable grass_pollen
  python predictor.py --source mongo --city Athens
  python predictor.py --no-mongo
        """,
    )
    parser.add_argument("--city", help="Single city to predict (default: all cities)")
    parser.add_argument(
        "--variable",
        help=f"Single variable to predict. Choices: {VARIABLES}",
    )
    parser.add_argument(
        "--source", choices=["csv", "mongo"], default="csv",
        help="Where to load training data from (default: csv)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=OUTPUT_DIR,
        help=f"CSV output directory for --source csv (default: {OUTPUT_DIR})",
    )
    parser.add_argument(
        "--no-mongo", action="store_true",
        help="Do not save predictions to MongoDB (print/return only)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    mongo_uri = _get_mongo_uri()
    save_to_mongo = not args.no_mongo

    cities    = [args.city]    if args.city    else GREEK_CITIES
    variables = [args.variable] if args.variable else VARIABLES

    for city in cities:
        run_city(
            city=city,
            variables=variables,
            source=args.source,
            output_dir=args.output_dir,
            mongo_uri=mongo_uri,
            save_to_mongo=save_to_mongo,
        )

    print(f"\n{'='*60}")
    print("Prediction run complete.")


if __name__ == "__main__":
    main()

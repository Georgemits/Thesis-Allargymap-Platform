"""
Open-Meteo data fetcher for the AllergyMap crowdsensing thesis platform.

Fetches two types of environmental data per location:
  1. Weather  : temperature, humidity, wind speed/direction, precipitation,
                UV index, atmospheric pressure
  2. Air quality: pollen (alder, birch, grass, mugwort, olive, ragweed),
                  dust, PM10, PM2.5, European AQI

Supports:
  - Forecast mode  : up to 7 days ahead (default)
  - Past-days mode : recent past via `past_days` parameter (up to 92 days)
  - Historical mode: full date-range query via the archive API (1940–present,
                     weather only — air quality archive not available)
  - Multi-location : all Greek cities in GREEK_LOCATIONS in one call

No API key required.

Usage (CLI):
    python open_meteo_fetcher.py                        # forecast, all cities
    python open_meteo_fetcher.py --mode past --days 30  # last 30 days, all cities
    python open_meteo_fetcher.py --mode historical --start 2024-01-01 --end 2024-12-31
    python open_meteo_fetcher.py --city Athens --mode forecast
    python open_meteo_fetcher.py --lat 37.98 --lon 23.73 --name MyCity

Output:
    data_collection/output/<name>_weather_<timestamp>.csv
    data_collection/output/<name>_airquality_<timestamp>.csv
    data_collection/output/<name>_combined_<timestamp>.csv
    data_collection/output/<name>_<timestamp>.json  (raw API response)
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------
FORECAST_WEATHER_API = "https://api.open-meteo.com/v1/forecast"
FORECAST_AQI_API     = "https://air-quality-api.open-meteo.com/v1/air-quality"
HISTORICAL_API       = "https://archive-api.open-meteo.com/v1/archive"

# ---------------------------------------------------------------------------
# Variable groups
# ---------------------------------------------------------------------------
WEATHER_VARS = [
    "temperature_2m",          # °C  — air temperature at 2 m
    "relative_humidity_2m",    # %   — main allergy trigger (paper 1 & 2)
    "apparent_temperature",    # °C  — feels-like temperature
    "precipitation",           # mm  — total (rain + snow)
    "wind_speed_10m",          # km/h — pollen dispersal proxy
    "wind_direction_10m",      # °   — pollen transport direction
    "pressure_msl",            # hPa — atmospheric pressure
    "cloud_cover",             # %   — solar radiation proxy
    "uv_index",                # idx — UV exposure (forecast only)
]

# uv_index is not available in the historical archive API
HISTORICAL_WEATHER_VARS = [v for v in WEATHER_VARS if v != "uv_index"]

AQI_VARS = [
    # Pollen — Europe only, grains/m³ (pollen season ~4-day forecast)
    "alder_pollen",
    "birch_pollen",
    "grass_pollen",
    "mugwort_pollen",
    "olive_pollen",    # critical for Greece (Mediterranean)
    "ragweed_pollen",
    # Particulates — μg/m³ (paper 1 uses dust as objective input)
    "pm10",
    "pm2_5",
    "dust",            # Saharan dust, common in Greece (paper 1)
    # Air quality indices
    "european_aqi",
    "european_aqi_pm10",
    "european_aqi_pm2_5",
    "european_aqi_ozone",
    "european_aqi_nitrogen_dioxide",
]

# ---------------------------------------------------------------------------
# Predefined Greek locations (WGS84)
# ---------------------------------------------------------------------------
GREEK_LOCATIONS: dict[str, dict[str, float]] = {
    "Athens":        {"latitude": 37.9838,  "longitude": 23.7275},
    "Thessaloniki":  {"latitude": 40.6401,  "longitude": 22.9444},
    "Patras":        {"latitude": 38.2466,  "longitude": 21.7346},
    "Heraklion":     {"latitude": 35.3387,  "longitude": 25.1442},
    "Ioannina":      {"latitude": 39.6650,  "longitude": 20.8537},
    "Larissa":       {"latitude": 39.6390,  "longitude": 22.4191},
    "Volos":         {"latitude": 39.3600,  "longitude": 22.9400},
    "Rhodes":        {"latitude": 36.4341,  "longitude": 28.2176},
    "Chania":        {"latitude": 35.5138,  "longitude": 24.0180},
    "Alexandroupoli": {"latitude": 40.8455, "longitude": 25.8743},
}

OUTPUT_DIR = Path(__file__).parent / "output"


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def _get(url: str, params: dict) -> dict:
    """Perform a GET request and return the JSON body. Raises on HTTP errors."""
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_forecast(lat: float, lon: float, forecast_days: int = 7) -> dict:
    """Fetch weather + air quality forecast for the next `forecast_days` days."""
    weather = _get(FORECAST_WEATHER_API, {
        "latitude":     lat,
        "longitude":    lon,
        "hourly":       ",".join(WEATHER_VARS),
        "forecast_days": forecast_days,
        "timezone":     "auto",
    })

    aqi = _get(FORECAST_AQI_API, {
        "latitude":  lat,
        "longitude": lon,
        "hourly":    ",".join(AQI_VARS),
        "timezone":  "auto",
    })

    return {"weather": weather, "air_quality": aqi}


def fetch_past_days(lat: float, lon: float, days: int = 30) -> dict:
    """Fetch recent past data using `past_days` (max 92 for air quality)."""
    days_aq = min(days, 92)

    weather = _get(FORECAST_WEATHER_API, {
        "latitude":  lat,
        "longitude": lon,
        "hourly":    ",".join(WEATHER_VARS),
        "past_days": days,
        "timezone":  "auto",
    })

    aqi = _get(FORECAST_AQI_API, {
        "latitude":  lat,
        "longitude": lon,
        "hourly":    ",".join(AQI_VARS),
        "past_days": days_aq,
        "timezone":  "auto",
    })

    return {"weather": weather, "air_quality": aqi}


def fetch_historical(
    lat: float,
    lon: float,
    start_date: str,
    end_date: str,
) -> dict:
    """
    Fetch historical weather via the archive API.
    Date format: 'YYYY-MM-DD'.  Data available from 1940 to yesterday.
    Note: air quality historical archive is not provided by Open-Meteo.
    """
    weather = _get(HISTORICAL_API, {
        "latitude":   lat,
        "longitude":  lon,
        "hourly":     ",".join(HISTORICAL_WEATHER_VARS),
        "start_date": start_date,
        "end_date":   end_date,
        "timezone":   "auto",
    })

    return {"weather": weather, "air_quality": None}


# ---------------------------------------------------------------------------
# DataFrame builders
# ---------------------------------------------------------------------------

def _hourly_to_df(api_response: dict, location_name: str) -> pd.DataFrame:
    """Convert the `hourly` block of an Open-Meteo response to a DataFrame."""
    hourly = api_response.get("hourly", {})
    if not hourly:
        return pd.DataFrame()

    df = pd.DataFrame(hourly)
    df.rename(columns={"time": "datetime"}, inplace=True)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df.insert(0, "location", location_name)
    df.insert(1, "latitude",  api_response.get("latitude"))
    df.insert(2, "longitude", api_response.get("longitude"))
    return df


def build_dataframes(
    raw: dict,
    location_name: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Returns three DataFrames: (weather_df, aqi_df, combined_df).
    Merge is on datetime + location (inner join on common timestamps).
    """
    weather_df = _hourly_to_df(raw["weather"], location_name)

    if raw["air_quality"] is not None:
        aqi_df = _hourly_to_df(raw["air_quality"], location_name)
    else:
        aqi_df = pd.DataFrame()

    if weather_df.empty or aqi_df.empty:
        combined_df = weather_df if not weather_df.empty else aqi_df
    else:
        combined_df = pd.merge(
            weather_df,
            aqi_df.drop(columns=["latitude", "longitude"], errors="ignore"),
            on=["location", "datetime"],
            how="inner",
        )

    return weather_df, aqi_df, combined_df


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def save_outputs(
    raw: dict,
    weather_df: pd.DataFrame,
    aqi_df: pd.DataFrame,
    combined_df: pd.DataFrame,
    name: str,
    output_dir: Path = OUTPUT_DIR,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # CSV files
    if not weather_df.empty:
        p = output_dir / f"{name}_weather_{ts}.csv"
        weather_df.to_csv(p, index=False)
        print(f"  [weather]     {p}")

    if not aqi_df.empty:
        p = output_dir / f"{name}_airquality_{ts}.csv"
        aqi_df.to_csv(p, index=False)
        print(f"  [air quality] {p}")

    if not combined_df.empty:
        p = output_dir / f"{name}_combined_{ts}.csv"
        combined_df.to_csv(p, index=False)
        print(f"  [combined]    {p}")

    # Raw JSON (useful for debugging or direct DB ingestion)
    p = output_dir / f"{name}_raw_{ts}.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    print(f"  [raw JSON]    {p}")


# ---------------------------------------------------------------------------
# High-level entry point
# ---------------------------------------------------------------------------

def run_for_location(
    name: str,
    lat: float,
    lon: float,
    mode: str,
    days: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    output_dir: Path = OUTPUT_DIR,
) -> pd.DataFrame:
    """
    Fetch, build DataFrames, save, and return the combined DataFrame for one location.

    Parameters
    ----------
    mode : 'forecast' | 'past' | 'historical'
    days : used with mode='past', default 30
    start_date / end_date : used with mode='historical', format 'YYYY-MM-DD'
    """
    print(f"\n{'='*60}")
    print(f"Location : {name}  ({lat}, {lon})")
    print(f"Mode     : {mode}")

    if mode == "forecast":
        raw = fetch_forecast(lat, lon)
    elif mode == "past":
        raw = fetch_past_days(lat, lon, days=days or 30)
    elif mode == "historical":
        if not start_date or not end_date:
            raise ValueError("--start and --end are required for historical mode")
        raw = fetch_historical(lat, lon, start_date, end_date)
    else:
        raise ValueError(f"Unknown mode: {mode}")

    weather_df, aqi_df, combined_df = build_dataframes(raw, name)
    save_outputs(raw, weather_df, aqi_df, combined_df, name, output_dir)

    rows = len(combined_df) if not combined_df.empty else 0
    print(f"  [rows]        {rows} combined hourly records")
    return combined_df


def run_all_greek_cities(
    mode: str,
    days: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    output_dir: Path = OUTPUT_DIR,
) -> pd.DataFrame:
    """Fetch data for all predefined Greek cities and return a concatenated DataFrame."""
    frames = []
    for city, coords in GREEK_LOCATIONS.items():
        try:
            df = run_for_location(
                name=city,
                lat=coords["latitude"],
                lon=coords["longitude"],
                mode=mode,
                days=days,
                start_date=start_date,
                end_date=end_date,
                output_dir=output_dir,
            )
            frames.append(df)
        except Exception as exc:
            print(f"  [ERROR] {city}: {exc}", file=sys.stderr)

    if not frames:
        return pd.DataFrame()

    all_df = pd.concat(frames, ignore_index=True)

    # Save the mega-file with all cities combined
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)
    p = output_dir / f"ALL_GREECE_combined_{ts}.csv"
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
        description="Fetch weather + pollen/air quality data from Open-Meteo for the AllergyMap platform.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python open_meteo_fetcher.py
  python open_meteo_fetcher.py --mode past --days 30
  python open_meteo_fetcher.py --mode historical --start 2024-01-01 --end 2024-12-31
  python open_meteo_fetcher.py --city Athens --mode forecast
  python open_meteo_fetcher.py --lat 37.98 --lon 23.73 --name "Custom City"
  python open_meteo_fetcher.py --list-cities
        """,
    )
    parser.add_argument(
        "--mode", choices=["forecast", "past", "historical"],
        default="forecast",
        help="Data mode: forecast (default), past N days, or historical date range",
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Number of past days to fetch (mode=past only, max 92 for air quality)",
    )
    parser.add_argument(
        "--start", dest="start_date", metavar="YYYY-MM-DD",
        help="Start date for historical mode",
    )
    parser.add_argument(
        "--end", dest="end_date", metavar="YYYY-MM-DD",
        help="End date for historical mode",
    )
    parser.add_argument(
        "--city", metavar="CITY_NAME",
        help=f"Single city from the predefined list. Available: {', '.join(GREEK_LOCATIONS)}",
    )
    parser.add_argument(
        "--lat", type=float,
        help="Custom latitude (requires --lon and --name)",
    )
    parser.add_argument(
        "--lon", type=float,
        help="Custom longitude (requires --lat and --name)",
    )
    parser.add_argument(
        "--name", metavar="LOCATION_NAME",
        help="Name for the custom location (used in output filenames)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=OUTPUT_DIR,
        help=f"Directory for output files (default: {OUTPUT_DIR})",
    )
    parser.add_argument(
        "--list-cities", action="store_true",
        help="Print available Greek cities and exit",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.list_cities:
        print("Available Greek cities:")
        for city, coords in GREEK_LOCATIONS.items():
            print(f"  {city:<20} lat={coords['latitude']}, lon={coords['longitude']}")
        return

    common = dict(
        mode=args.mode,
        days=args.days,
        start_date=args.start_date,
        end_date=args.end_date,
        output_dir=args.output_dir,
    )

    if args.lat is not None or args.lon is not None:
        # Custom coordinates
        if args.lat is None or args.lon is None or not args.name:
            print("Error: --lat, --lon, and --name are all required together.", file=sys.stderr)
            sys.exit(1)
        run_for_location(name=args.name, lat=args.lat, lon=args.lon, **common)

    elif args.city:
        city = args.city.strip()
        if city not in GREEK_LOCATIONS:
            print(
                f"Error: '{city}' not found. Use --list-cities to see options.",
                file=sys.stderr,
            )
            sys.exit(1)
        coords = GREEK_LOCATIONS[city]
        run_for_location(name=city, lat=coords["latitude"], lon=coords["longitude"], **common)

    else:
        # Default: fetch all Greek cities
        run_all_greek_cities(**common)


if __name__ == "__main__":
    main()

"""
Daily collection scheduler for the AllergyMap crowdsensing platform.

Runs the environmental data pipeline on a repeating daily schedule so the
platform accumulates a continuous observational record instead of depending on
manual, ad-hoc fetches.

Each scheduled *cycle* executes up to two passes:

  1. Forecast pass. Fetches the Open-Meteo forecast window and attaches
     Google's daily Universal Pollen Index (UPI) where available. Google's
     Pollen API is forecast-only and exposes no historical endpoint, so this
     pass captures values that cannot be recovered retroactively. This is the
     pass that must not be missed.

  2. Reanalysis pass. Re-fetches the last N days from Open-Meteo. Because
     `mongo_importer` upserts on (city, timestamp), hours that were first
     stored as forecasts are progressively overwritten by Open-Meteo's
     analysed values for the same hours. The collection therefore converges
     towards observed conditions with no manual correction step.

Both passes are idempotent: running a cycle twice on the same day leaves the
database in the same state.

Configuration is read from a JSON file (`collector_config.json`, committed
with safe non-secret defaults) and may be overridden per run from the command
line. No schedule, location, or connection parameter is hard-coded. Secrets
(MONGO_URI, GOOGLE_POLLEN_API_KEY) come from the environment or .env only.

Usage (CLI):
    python scheduler.py                        # run forever on the configured schedule
    python scheduler.py --once                 # single cycle, then exit (cron / CI)
    python scheduler.py --backfill 92          # one-off historical load, then exit
    python scheduler.py --run-at 06:00,18:00   # override the schedule for this run
    python scheduler.py --once --dry-run       # fetch and report, write nothing

Exit codes:
    0: success.
    1: configuration error or fatal runtime failure.
"""

import argparse
import json
import logging
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

from mongo_importer import get_mongo_uri, import_dataframe
from open_meteo_fetcher import GREEK_LOCATIONS, run_for_location

# ---------------------------------------------------------------------------
# Defaults and paths
# ---------------------------------------------------------------------------
MODULE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = MODULE_DIR / "collector_config.json"

#: Fallback configuration, used for any key the config file does not define.
DEFAULT_CONFIG: dict = {
    "timezone": "Europe/Athens",
    "run_at": ["06:00", "18:00"],
    "cities": None,
    "output_dir": "output",
    "retention_days": 14,
    "forecast_pass": {"enabled": True, "pollen_source": "google"},
    "reanalysis_pass": {"enabled": True, "days": 3, "pollen_source": "open_meteo"},
    "backfill": {"days": 92, "pollen_source": "open_meteo"},
}

VALID_POLLEN_SOURCES = ("google", "open_meteo")

LOGGER = logging.getLogger("allergymap.scheduler")

#: Flipped by SIGINT/SIGTERM so the sleep loop can exit between cycles.
_STOP_REQUESTED = False


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, override: dict) -> dict:
    """Return `base` updated recursively with `override`.

    Nested dictionaries are merged key by key; every other value is replaced
    outright. Neither input is mutated.

    Args:
        base: Defaults.
        override: Values that take precedence.

    Returns:
        A new merged dictionary.
    """
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Path) -> dict:
    """Load the collector configuration, falling back to built-in defaults.

    Args:
        path: Path to a JSON configuration file. A missing file is not an
            error; the defaults are used and a warning is logged.

    Returns:
        The effective configuration dictionary.

    Raises:
        ValueError: If the file exists but is not valid JSON.
    """
    if not path.exists():
        LOGGER.warning("No config file at %s — using built-in defaults.", path)
        return dict(DEFAULT_CONFIG)

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object at the top level.")

    LOGGER.info("Loaded configuration from %s", path)
    return _deep_merge(DEFAULT_CONFIG, raw)


def apply_overrides(config: dict, args: argparse.Namespace) -> dict:
    """Apply command-line overrides on top of the file configuration.

    Args:
        config: Configuration as loaded from disk.
        args: Parsed command-line arguments.

    Returns:
        A new configuration dictionary with the overrides applied.
    """
    overrides: dict = {}

    if args.run_at:
        overrides["run_at"] = [item.strip() for item in args.run_at.split(",") if item.strip()]
    if args.timezone:
        overrides["timezone"] = args.timezone
    if args.cities:
        overrides["cities"] = [item.strip() for item in args.cities.split(",") if item.strip()]
    if args.output_dir:
        overrides["output_dir"] = args.output_dir
    if args.retention_days is not None:
        overrides["retention_days"] = args.retention_days
    if args.pollen_source:
        overrides["forecast_pass"] = {"pollen_source": args.pollen_source}
        overrides["backfill"] = {"pollen_source": args.pollen_source}

    return _deep_merge(config, overrides) if overrides else config


def validate_config(config: dict) -> None:
    """Fail fast on a configuration the scheduler cannot honour.

    Args:
        config: The effective configuration.

    Raises:
        ValueError: If any value is missing, malformed, or out of range.
    """
    try:
        ZoneInfo(config["timezone"])
    except (ZoneInfoNotFoundError, KeyError, TypeError) as exc:
        raise ValueError(f"Invalid timezone {config.get('timezone')!r}: {exc}") from exc

    run_at = config.get("run_at")
    if not isinstance(run_at, list) or not run_at:
        raise ValueError("'run_at' must be a non-empty list of 'HH:MM' strings.")
    for value in run_at:
        _parse_clock_time(value)

    for pass_name in ("forecast_pass", "reanalysis_pass", "backfill"):
        source = config.get(pass_name, {}).get("pollen_source", "open_meteo")
        if source not in VALID_POLLEN_SOURCES:
            raise ValueError(
                f"{pass_name}.pollen_source must be one of {VALID_POLLEN_SOURCES}, got {source!r}"
            )

    days = config.get("reanalysis_pass", {}).get("days", 0)
    if not isinstance(days, int) or not 0 <= days <= 92:
        raise ValueError("reanalysis_pass.days must be an integer between 0 and 92.")

    backfill_days = config.get("backfill", {}).get("days", 0)
    if not isinstance(backfill_days, int) or not 0 < backfill_days <= 92:
        raise ValueError(
            "backfill.days must be an integer between 1 and 92 "
            "(Open-Meteo's air-quality archive limit)."
        )

    unknown = set(config.get("cities") or []) - set(GREEK_LOCATIONS)
    if unknown:
        raise ValueError(
            f"Unknown cities in configuration: {sorted(unknown)}. "
            f"Available: {sorted(GREEK_LOCATIONS)}"
        )


def resolve_locations(config: dict) -> dict:
    """Return the {city: coordinates} mapping this run should cover.

    Args:
        config: The effective configuration. A null or empty `cities` entry
            means every city in `GREEK_LOCATIONS`.

    Returns:
        A mapping of city name to its latitude/longitude dictionary.
    """
    selected = config.get("cities")
    if not selected:
        return dict(GREEK_LOCATIONS)
    return {city: GREEK_LOCATIONS[city] for city in selected}


def resolve_output_dir(config: dict) -> Path:
    """Return the output directory as an absolute path.

    Args:
        config: The effective configuration.

    Returns:
        The configured directory, resolved relative to this module when the
        configured value is not already absolute.
    """
    configured = Path(config.get("output_dir") or "output")
    return configured if configured.is_absolute() else MODULE_DIR / configured


# ---------------------------------------------------------------------------
# Pipeline passes
# ---------------------------------------------------------------------------

def fetch_locations(
    locations: dict,
    mode: str,
    output_dir: Path,
    days: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """Fetch one mode for every location and concatenate the results.

    A failure for one city is logged and skipped rather than aborting the
    cycle, so a single unreachable location cannot cost a whole day of data.

    Args:
        locations: Mapping of city name to coordinates.
        mode: One of 'forecast', 'past', or 'historical', as understood by
            `open_meteo_fetcher.run_for_location`.
        output_dir: Directory for the intermediate CSV/JSON artefacts.
        days: Number of past days, for mode='past'.
        start_date: Range start 'YYYY-MM-DD', for mode='historical'.
        end_date: Range end 'YYYY-MM-DD', for mode='historical'.

    Returns:
        The concatenated hourly DataFrame, empty if every city failed.
    """
    frames: list[pd.DataFrame] = []
    for city, coords in locations.items():
        try:
            frame = run_for_location(
                name=city,
                lat=coords["latitude"],
                lon=coords["longitude"],
                mode=mode,
                days=days,
                start_date=start_date,
                end_date=end_date,
                output_dir=output_dir,
            )
            if not frame.empty:
                frames.append(frame)
        except Exception as exc:  # noqa: BLE001 - one city must not kill the cycle
            LOGGER.error("Fetch failed for %s (mode=%s): %s", city, mode, exc)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def run_pass(
    label: str,
    locations: dict,
    mode: str,
    pollen_source: str,
    output_dir: Path,
    mongo_uri: str,
    days: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    dry_run: bool = False,
) -> int:
    """Run a single fetch-and-upsert pass.

    Args:
        label: Human-readable pass name, used in log lines.
        locations: Mapping of city name to coordinates.
        mode: 'forecast', 'past', or 'historical'.
        pollen_source: 'google' or 'open_meteo'.
        output_dir: Directory for the intermediate artefacts.
        mongo_uri: MongoDB connection string.
        days: Number of past days, for mode='past'.
        start_date: Range start 'YYYY-MM-DD', for mode='historical'.
        end_date: Range end 'YYYY-MM-DD', for mode='historical'.
        dry_run: When true, fetch and report but write nothing to MongoDB.

    Returns:
        The number of documents inserted or modified (0 on a dry run).
    """
    LOGGER.info("[%s] fetching %d location(s), mode=%s, days=%s", label, len(locations), mode, days)
    frame = fetch_locations(
        locations, mode=mode, output_dir=output_dir, days=days,
        start_date=start_date, end_date=end_date,
    )

    if frame.empty:
        LOGGER.warning("[%s] no rows fetched — skipping import.", label)
        return 0

    LOGGER.info("[%s] fetched %d hourly rows", label, len(frame))

    if dry_run:
        LOGGER.info("[%s] dry run — not writing to MongoDB.", label)
        return 0

    affected = import_dataframe(frame, mongo_uri, pollen_source=pollen_source)
    LOGGER.info("[%s] %d document(s) inserted or updated (pollen_source=%s)",
                label, affected, pollen_source)
    return affected


def run_cycle(config: dict, mongo_uri: str, dry_run: bool = False) -> int:
    """Run one full collection cycle: forecast pass, then reanalysis pass.

    Args:
        config: The effective configuration.
        mongo_uri: MongoDB connection string.
        dry_run: When true, write nothing to MongoDB.

    Returns:
        The total number of documents inserted or modified.
    """
    locations = resolve_locations(config)
    output_dir = resolve_output_dir(config)
    total = 0

    forecast = config.get("forecast_pass", {})
    if forecast.get("enabled", True):
        total += run_pass(
            label="forecast",
            locations=locations,
            mode="forecast",
            pollen_source=forecast.get("pollen_source", "google"),
            output_dir=output_dir,
            mongo_uri=mongo_uri,
            dry_run=dry_run,
        )

    reanalysis = config.get("reanalysis_pass", {})
    if reanalysis.get("enabled", True) and reanalysis.get("days", 0) > 0:
        total += run_pass(
            label="reanalysis",
            locations=locations,
            mode="past",
            pollen_source=reanalysis.get("pollen_source", "open_meteo"),
            output_dir=output_dir,
            mongo_uri=mongo_uri,
            days=reanalysis["days"],
            dry_run=dry_run,
        )

    pruned = prune_output(output_dir, config.get("retention_days"))
    if pruned:
        LOGGER.info("Pruned %d intermediate file(s) older than %s day(s).",
                    pruned, config.get("retention_days"))

    LOGGER.info("Cycle complete — %d document(s) affected in total.", total)
    return total


def run_backfill(config: dict, days: int, mongo_uri: str, dry_run: bool = False) -> int:
    """Run a one-off historical load of the last `days` days.

    Open-Meteo serves at most 92 past days of air-quality data (pollen and
    dust), so this is the widest retroactive window available.

    Args:
        config: The effective configuration.
        days: Number of past days to load (1-92).
        mongo_uri: MongoDB connection string.
        dry_run: When true, write nothing to MongoDB.

    Returns:
        The number of documents inserted or modified.
    """
    return run_pass(
        label=f"backfill-{days}d",
        locations=resolve_locations(config),
        mode="past",
        pollen_source=config.get("backfill", {}).get("pollen_source", "open_meteo"),
        output_dir=resolve_output_dir(config),
        mongo_uri=mongo_uri,
        days=days,
        dry_run=dry_run,
    )


def run_weather_fill(
    config: dict,
    start_date: str,
    end_date: str,
    mongo_uri: str,
    dry_run: bool = False,
) -> int:
    """Fill weather-only gaps from Open-Meteo's archive API.

    The two past-data endpoints do not reach equally far back. The
    air-quality endpoint honours the full `past_days=92`, but the weather
    forecast endpoint retains roughly 70 days, so a 92-day backfill lands
    pollen and dust for the whole window and temperature and humidity for
    only part of it. The archive API covers weather from 1940 and closes that
    gap.

    Safe to overlay on existing documents: `mongo_importer` writes dotted
    paths, so this fills `weather.*` without touching the pollen or air
    quality already stored for the same hours. `uv_index` is absent from the
    archive and stays null for the filled range.

    Args:
        config: The effective configuration.
        start_date: Range start, 'YYYY-MM-DD'.
        end_date: Range end, 'YYYY-MM-DD'. The archive lags reality by a few
            days; ask for a date too recent and the tail comes back empty.
        mongo_uri: MongoDB connection string.
        dry_run: When true, write nothing to MongoDB.

    Returns:
        The number of documents inserted or modified.
    """
    return run_pass(
        label=f"weather-fill {start_date}..{end_date}",
        locations=resolve_locations(config),
        mode="historical",
        pollen_source="open_meteo",
        output_dir=resolve_output_dir(config),
        mongo_uri=mongo_uri,
        start_date=start_date,
        end_date=end_date,
        dry_run=dry_run,
    )


def parse_iso_date(value: str) -> str:
    """Validate a 'YYYY-MM-DD' string and return it unchanged.

    Args:
        value: The candidate date string.

    Returns:
        The same string, once confirmed parseable.

    Raises:
        ValueError: If the string is not an ISO calendar date.
    """
    datetime.strptime(value, "%Y-%m-%d")
    return value


def prune_output(output_dir: Path, retention_days: int | None) -> int:
    """Delete intermediate CSV/JSON artefacts older than the retention window.

    The database is the system of record; these files are regenerable, and
    without pruning a long-running collector fills the volume.

    Args:
        output_dir: Directory holding the artefacts.
        retention_days: Age limit in days. None or 0 disables pruning.

    Returns:
        The number of files removed.
    """
    if not retention_days or not output_dir.exists():
        return 0

    cutoff = time.time() - retention_days * 86400
    removed = 0
    for pattern in ("*.csv", "*.json"):
        for path in output_dir.glob(pattern):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError as exc:
                LOGGER.warning("Could not prune %s: %s", path.name, exc)
    return removed


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

def _parse_clock_time(value: str) -> tuple[int, int]:
    """Parse an 'HH:MM' string into an (hour, minute) pair.

    Args:
        value: A 24-hour clock time such as '06:00'.

    Returns:
        The hour and minute as integers.

    Raises:
        ValueError: If the string is not a valid 'HH:MM' time.
    """
    try:
        hour_str, minute_str = str(value).split(":")
        hour, minute = int(hour_str), int(minute_str)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid time {value!r}: expected 'HH:MM'.") from exc

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid time {value!r}: outside 00:00-23:59.")
    return hour, minute


def next_run_time(now: datetime, run_at: list[str]) -> datetime:
    """Return the next scheduled moment strictly after `now`.

    Args:
        now: The current time, timezone-aware.
        run_at: Daily run times as 'HH:MM' strings.

    Returns:
        The soonest future run time, rolling over to tomorrow when every
        configured time has already passed today.
    """
    candidates = []
    for value in run_at:
        hour, minute = _parse_clock_time(value)
        today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        candidates.append(today if today > now else today + timedelta(days=1))
    return min(candidates)


def _sleep_until(target: datetime, tz: ZoneInfo) -> None:
    """Sleep until `target`, waking often enough to honour a stop signal.

    Args:
        target: The moment to wake up, timezone-aware.
        tz: Timezone used to read the current time.
    """
    while not _STOP_REQUESTED:
        remaining = (target - datetime.now(tz)).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 30.0))


def _handle_stop(signum, _frame) -> None:
    """Record a termination signal so the main loop can exit cleanly."""
    global _STOP_REQUESTED
    _STOP_REQUESTED = True
    LOGGER.info("Received signal %s — finishing up and exiting.", signum)


def run_forever(config: dict, mongo_uri: str, dry_run: bool = False) -> None:
    """Run collection cycles on the configured schedule until stopped.

    Args:
        config: The effective configuration.
        mongo_uri: MongoDB connection string.
        dry_run: When true, write nothing to MongoDB.
    """
    tz = ZoneInfo(config["timezone"])
    LOGGER.info("Scheduler started — run_at=%s (%s)", config["run_at"], config["timezone"])

    while not _STOP_REQUESTED:
        upcoming = next_run_time(datetime.now(tz), config["run_at"])
        LOGGER.info("Next cycle at %s", upcoming.isoformat(timespec="minutes"))
        _sleep_until(upcoming, tz)
        if _STOP_REQUESTED:
            break
        try:
            run_cycle(config, mongo_uri, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - a bad cycle must not kill the collector
            LOGGER.exception("Cycle failed: %s", exc)

    LOGGER.info("Scheduler stopped.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    """Build and parse the command-line interface."""
    parser = argparse.ArgumentParser(
        description="Scheduled environmental data collector for the AllergyMap platform.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scheduler.py                        run forever on the configured schedule
  python scheduler.py --once                 single cycle, then exit (cron / CI)
  python scheduler.py --backfill 92          one-off 92-day historical load
  python scheduler.py --fill-weather 2026-05-25 2026-06-19
                                             close the weather-only gap the
                                             92-day backfill leaves behind
  python scheduler.py --run-at 06:00,18:00   override the schedule for this run
  python scheduler.py --once --dry-run       fetch and report, write nothing
        """,
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH,
                        help=f"Path to the JSON config file (default: {DEFAULT_CONFIG_PATH.name})")
    parser.add_argument("--once", action="store_true",
                        help="Run a single cycle immediately and exit")
    parser.add_argument("--backfill", type=int, metavar="DAYS",
                        help="Run a one-off historical load of DAYS past days (1-92), then exit")
    parser.add_argument("--fill-weather", nargs=2, metavar=("START", "END"),
                        help="Fill weather-only gaps from the archive API over the "
                             "'YYYY-MM-DD YYYY-MM-DD' range, then exit. Use this after "
                             "--backfill: the weather endpoint reaches back ~70 days "
                             "while air quality reaches 92")
    parser.add_argument("--run-at", metavar="HH:MM[,HH:MM...]",
                        help="Override the daily run times")
    parser.add_argument("--timezone", metavar="TZ",
                        help="Override the schedule timezone (IANA name, e.g. Europe/Athens)")
    parser.add_argument("--cities", metavar="CITY[,CITY...]",
                        help="Restrict collection to these cities")
    parser.add_argument("--pollen-source", choices=VALID_POLLEN_SOURCES,
                        help="Override the pollen source for the forecast and backfill passes")
    parser.add_argument("--output-dir", metavar="PATH",
                        help="Override the directory for intermediate CSV/JSON artefacts")
    parser.add_argument("--retention-days", type=int, metavar="N",
                        help="Override how long intermediate artefacts are kept (0 disables pruning)")
    parser.add_argument("--mongo-uri", metavar="URI",
                        help="Override MONGO_URI for this run")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and report without writing to MongoDB")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="Logging verbosity (default: INFO)")
    return parser.parse_args()


def main() -> None:
    """Entry point: resolve configuration, then run once, backfill, or loop."""
    args = _parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )

    try:
        config = validate_and_build(args)
    except ValueError as exc:
        LOGGER.error("Configuration error: %s", exc)
        sys.exit(1)

    mongo_uri = args.mongo_uri or get_mongo_uri()
    LOGGER.info("MongoDB target: %s", _redact_uri(mongo_uri))

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    try:
        if args.fill_weather:
            run_weather_fill(config, *args.fill_weather, mongo_uri, dry_run=args.dry_run)
        elif args.backfill:
            run_backfill(config, args.backfill, mongo_uri, dry_run=args.dry_run)
        elif args.once:
            run_cycle(config, mongo_uri, dry_run=args.dry_run)
        else:
            run_forever(config, mongo_uri, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 - report and exit non-zero for the supervisor
        LOGGER.exception("Fatal error: %s", exc)
        sys.exit(1)


def validate_and_build(args: argparse.Namespace) -> dict:
    """Load, override, and validate the configuration for this run.

    Args:
        args: Parsed command-line arguments.

    Returns:
        The validated effective configuration.

    Raises:
        ValueError: If the resulting configuration is unusable.
    """
    config = apply_overrides(load_config(args.config), args)
    if args.backfill is not None and not 0 < args.backfill <= 92:
        raise ValueError("--backfill must be between 1 and 92 days.")
    if args.fill_weather:
        start, end = (parse_iso_date(value) for value in args.fill_weather)
        if start > end:
            raise ValueError("--fill-weather START must not be after END.")
    validate_config(config)
    return config


def _redact_uri(uri: str) -> str:
    """Return `uri` with any embedded credentials replaced by '***'.

    Args:
        uri: A MongoDB connection string.

    Returns:
        The connection string, safe to write to a log.
    """
    if "@" not in uri:
        return uri
    scheme, _, rest = uri.partition("://")
    _, _, host = rest.partition("@")
    return f"{scheme}://***@{host}"


if __name__ == "__main__":
    main()

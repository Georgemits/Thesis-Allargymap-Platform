# data_collection

Fetches environmental data (weather, pollen, air quality) for the AllergyMap
platform and imports it into MongoDB (`env_snapshots` collection).

## Files

| File                        | Purpose                                                              |
|------------------------------|-----------------------------------------------------------------------|
| `open_meteo_fetcher.py`      | Weather + air quality (pollen grains/m3, dust, PM10/PM2.5, AQI) from Open-Meteo. No API key required. Forecast, past-days, and historical (archive) modes. |
| `google_pollen_fetcher.py`   | Daily pollen **Universal Pollen Index (UPI, 0-5)** from the Google Maps Platform Pollen API. Forecast only, capped at 5 days. Requires `GOOGLE_POLLEN_API_KEY`. |
| `pollen_source.py`           | Source-agnostic abstraction: normalizes both providers into the `env_snapshots` pollen schema, with Google as primary and automatic Open-Meteo fallback. |
| `mongo_importer.py`          | Imports combined Open-Meteo CSVs into MongoDB, attaching pollen fields via `pollen_source.py` (`--pollen-source google\|open_meteo`, default `google`). |
| `scheduler.py`               | **Scheduled collector.** Runs the fetch -> normalise -> upsert pipeline on a daily schedule (`collector_config.json`). Also does one-off backfills (`--backfill N`) and single cron-style runs (`--once`). |
| `collector_config.json`      | Schedule, city scope, retention, and per-pass pollen source for `scheduler.py`. Contains no secrets and is committed so the stack is clone-and-run. |
| `predictor.py`               | RandomForest time-series forecasting (severity/pollen prediction), used by the backend's `/api/predictions` routes. |
| `tests/`                     | Unit tests for both fetchers and the normalizer (HTTP calls mocked, no network/API key needed). |
| `output/`                    | Generated CSV/JSON files (gitignored, regeneratable). |

## Hybrid pollen source (Google primary, Open-Meteo fallback)

AllergyMap v2 uses **Google's Pollen API as the primary pollen source** and
**Open-Meteo as fallback + historical/archive**. The selection is automatic and
happens per date, not per run:

1. `mongo_importer.py` (default `--pollen-source google`) groups the rows being
   imported by city and makes one Google Pollen API call per city.
2. Any row whose date falls inside Google's forecast window (today .. today+4)
   gets Google's daily UPI attached (`pollen_source: "google"`).
3. Any row outside that window — or every row for a city whose Google call
   fails (missing key, HTTP error, quota) — falls back automatically to
   Open-Meteo-only fields (`pollen_source: "open_meteo"`,
   `pollen_source_fallback_reason` explains why).
4. `--pollen-source open_meteo` skips Google entirely (explicit opt-out, or
   for `--mode past`/`--mode historical` batches where every date is
   guaranteed out of Google's window anyway).

### Scale mapping — grains/m3 vs UPI (kept side by side, not converted)

| | Open-Meteo `pollen` | Google `pollen_upi` |
|---|---|---|
| Unit | grains/m3 (continuous concentration) | Universal Pollen Index, 0-5 (discrete daily category) |
| Cadence | hourly | daily |
| Source model | CAMS European pollen forecast | Google's own undisclosed model |
| Coverage | forecast (7d) + past (92d) + full archive (1940-present, weather only) | forecast only, 5 days |

Google publishes no official formula mapping UPI buckets to grains/m3, and the
two are on different cadences and (likely) different underlying models.
Converting one into the other would fabricate precision that doesn't exist, so
every `env_snapshots` document stores **both** values instead — itself a
thesis-relevant comparison point. See `pollen_source.py`'s module docstring
for the exact field shapes.

### African / Saharan dust

`dust` (μg/m3) is sourced exclusively from Open-Meteo's air-quality endpoint,
which is itself powered by the **CAMS (Copernicus Atmosphere Monitoring
Service) global atmospheric composition model** — Open-Meteo re-serves CAMS
output rather than running its own dust model. Google's Pollen API does not
cover dust.

> **TODO (future work):** an optional *direct* CAMS/Copernicus Atmosphere Data
> Store (ADS) integration, bypassing Open-Meteo, would give access to CAMS's
> full spatial/temporal resolution and additional species (e.g. desert dust
> optical depth) at the cost of requiring a free Copernicus ADS API key and
> handling NetCDF/GRIB output directly instead of Open-Meteo's simplified
> hourly JSON. Not needed for v2 — Open-Meteo's CAMS passthrough is sufficient.

## Environment variables

`GOOGLE_POLLEN_API_KEY` is read from (in order, first match wins):

1. The process environment (e.g. set by Docker Compose).
2. `data_collection/.env` (optional — copy `.env.example`).
3. `backend/.env` (fallback — the key usually only needs to live here).

`MONGO_URI` follows the same fallback chain (`mongo_importer.get_mongo_uri()`).
Never hard-code either value; both are gitignored.

## Usage

```bash
pip install -r requirements.txt

# Weather + air quality + Open-Meteo pollen, all Greek cities
python open_meteo_fetcher.py --mode forecast

# Google Pollen UPI forecast (5-day cap), all Greek cities
python google_pollen_fetcher.py

# Import Open-Meteo CSVs into MongoDB, Google pollen primary (default)
python mongo_importer.py

# Import using only Open-Meteo pollen (e.g. for a historical backfill)
python mongo_importer.py --pollen-source open_meteo

# Or fetch + push in one step (uses --pollen-source google by default)
python open_meteo_fetcher.py --mode forecast --push-to-mongo
```

Run all data_collection scripts from this directory (they resolve `output/`
and `.env` relative to `Path(__file__)`, and import each other with flat,
un-packaged imports).

## Testing

```bash
python -m unittest discover -s tests -v
```

All HTTP calls are mocked (`unittest.mock`) — no network access or API key
is required to run the tests.

## The two past windows are not the same length

`--mode past --days 92` sends `past_days=92` to both endpoints, but they honour
it differently:

| Endpoint | Actual past coverage | Fields |
|---|---|---|
| air-quality | the full **92 days** | pollen, dust, PM10, PM2.5, AQI |
| forecast (weather) | roughly **70 days** | temperature, humidity, wind, pressure, precipitation, cloud, UV |

Because the two frames are merged on the hour, the earlier rows still exist —
they simply carry `null` weather. Measured on 2026-08-26, a 92-day backfill
produced pollen and dust from 25 May but temperature and humidity only from
18 June: 23 days of the olive season with no temperature, which is one half of
the allergen-versus-weather correlation.

The archive API (weather back to 1940) closes the gap:

```bash
python scheduler.py --backfill 92                          # everything, 92 days
python scheduler.py --fill-weather 2026-05-26 2026-06-19   # weather for the gap
```

### Why partial sources can be layered safely

Two rules in `mongo_importer` make this work:

1. **Dotted paths.** Updates write `weather.temperature_2m`, never a whole
   `weather` sub-document, so one group cannot replace another.
2. **Nulls are written only on insert.** Real values go in `$set`, nulls in
   `$setOnInsert`. A brand-new hour still gets the full field shape, but no
   pass can ever blank a value another pass supplied.

Rule 1 alone is not enough, and finding that out cost 6000 hours of olive
pollen on 2026-08-26: `pollen_source.normalize_open_meteo` returns all six
plant keys explicitly set to None when the frame has no pollen columns, so a
weather-only import carries a full sub-document *of nulls* and dotted paths
wrote them faithfully. Rule 2 closes it.

A useful consequence is that the passes are **order-independent** -- re-running
`--backfill 92` after a weather fill no longer re-nulls the weather it filled.

`uv_index` is absent from the archive, so it stays null over the filled range.

Pick the range from the data rather than from the calendar — query the first
non-null `weather.temperature_2m` and fill from the window start up to it.

## Timestamps: everything is UTC

Open-Meteo is queried with `timezone=auto`, so it answers in **local** time
(`2026-08-03T00:00` meaning 00:00 Europe/Athens) and reports the offset
separately in `utc_offset_seconds`. Taking those strings at face value and
labelling them UTC shifts every record by two hours in winter and three in
summer -- enough to misalign environmental data against user symptom reports
and to corrupt any hour-of-day analysis, without ever raising an error.

The pipeline therefore normalises on the way in:

| Column / field | Meaning |
|---|---|
| `datetime` (CSV) / `timestamp` (Mongo) | naive **UTC** -- the canonical key used for merges, upserts, and every query |
| `datetime_local` (CSV only) | the original local time, kept for human inspection and for matching sources that publish per-local-day values, such as Google's daily UPI |

The API mirrors the same rule: `backend/app/utils/serialization.py` renders
every datetime with an explicit `+00:00` offset, because a browser parses an
offset-less date-time string as local time.

> Found on 2026-08-26, after the first 92-day backfill had already been
> imported with shifted timestamps. If a database predates the fix, clear
> `env_snapshots` and re-run the backfill -- the upsert key is
> `(city, timestamp)`, so corrected rows would otherwise land beside the old
> ones instead of replacing them.

## Scheduled collection (`scheduler.py`)

`scheduler.py` turns the pipeline from a manual command into a continuously
running service, so the platform accumulates an unbroken observational record.

### What one cycle does

Each cycle runs two passes, both idempotent (upsert on `(city, timestamp)`):

1. **Forecast pass** — Open-Meteo forecast window with Google's daily UPI
   attached. Google's Pollen API is forecast-only and has **no historical
   endpoint**, so a UPI value not captured on the day is lost permanently.
   This is the pass that must not be missed.
2. **Reanalysis pass** — re-fetches the last `reanalysis_pass.days` days from
   Open-Meteo. Hours first stored as *forecasts* are progressively overwritten
   by Open-Meteo's *analysed* values for the same hours, so the collection
   converges towards observed conditions with no manual correction step. This
   is why the two passes use different pollen sources by default.

Intermediate CSV/JSON artefacts older than `retention_days` are pruned at the
end of every cycle. MongoDB is the system of record; those files are
regenerable.

### Configuration

`collector_config.json` (committed, no secrets). Any key may be omitted — the
built-in defaults in `scheduler.DEFAULT_CONFIG` fill the gaps.

| Key | Meaning |
|-----|---------|
| `timezone` | IANA name used to interpret `run_at` (default `Europe/Athens`). |
| `run_at` | Daily run times as `HH:MM` strings. |
| `cities` | List of city names, or `null` for every city in `GREEK_LOCATIONS`. |
| `output_dir` | Where intermediate artefacts are written (relative to this directory). |
| `retention_days` | Age limit for those artefacts; `0` disables pruning. |
| `forecast_pass` | `{enabled, pollen_source}` — defaults to `google`. |
| `reanalysis_pass` | `{enabled, days, pollen_source}` — defaults to 3 days of `open_meteo`. |
| `backfill` | `{days, pollen_source}` — defaults used by `--backfill`. |

`MONGO_URI` and `GOOGLE_POLLEN_API_KEY` are **never** read from this file; they
come from the environment or `.env` (see above).

### Usage

```bash
# One-off historical load — the widest retroactive window Open-Meteo serves
# for pollen and dust is 92 days. Run this once, first.
python scheduler.py --backfill 92

# Run forever on the configured schedule (this is what the Docker service does)
python scheduler.py

# A single cycle, then exit — for cron, launchd, or CI
python scheduler.py --once

# Fetch and report without touching MongoDB
python scheduler.py --once --dry-run

# Override the schedule or scope for one run
python scheduler.py --run-at 06:00,14:00,22:00 --cities Athens,Patras
```

Every parameter is overridable from the command line; run
`python scheduler.py --help` for the full list.

### In Docker

The `collector` service (see `docker/README.md`) runs `scheduler.py` with
`restart: unless-stopped`, writing its artefacts to the `collector_output`
named volume. The older `seeder` service remains as a one-shot bootstrap.

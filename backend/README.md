# backend

Flask REST API for AllergyMap (symptom reports, environmental data, ML predictions).

## Structure

```
backend/
├── run.py                 ← python run.py  (dev server, port 5000)
├── requirements.txt
├── .env.example            ← copy to .env before running
└── app/
    ├── __init__.py         ← app factory, registers all blueprints
    ├── config.py            ← env-var driven config (loads backend/.env via python-dotenv)
    ├── extensions.py        ← MongoDB (flask-pymongo) connection
    ├── models/               ← document schema docstrings (not ORM models)
    │   ├── report.py         ← symptom report + build_report()
    │   └── env_snapshot.py    ← weather/pollen/air_quality snapshot schema
    ├── routes/
    │   ├── health.py         ← GET /health
    │   ├── reports.py        ← POST/GET /api/reports, GET /api/reports/heatmap
    │   ├── env_data.py       ← GET /api/env/latest, /city/<city> (?days= or ?start_date=&end_date=), /capabilities
    │   └── predictions.py    ← GET/POST /api/predictions/* (?start_date=&end_date= supported too; lazily imports data_collection/predictor.py)
    └── utils/
        ├── geo.py             ← haversine distance, nearest_city()
        └── validation.py       ← parse_date_range() -- shared start_date/end_date validation for env_data.py and predictions.py
```

## Configuration

All config comes from environment variables (never hard-coded), loaded from
`backend/.env` via `python-dotenv` in `config.py`:

| Variable                | Default                                    | Notes |
|--------------------------|---------------------------------------------|-------|
| `FLASK_ENV`               | `development`                               | selects `DevelopmentConfig` / `ProductionConfig` |
| `SECRET_KEY`               | `dev-secret-key`                            | change in production |
| `MONGO_URI`                | `mongodb://localhost:27017/allergymap`      | |
| `GOOGLE_POLLEN_API_KEY`    | `""` (empty)                                | primary pollen source; empty = Open-Meteo-only fallback everywhere. See `data_collection/README.md` for the full hybrid-source explanation. |

```bash
cp .env.example .env   # then fill in real values; .env is gitignored
```

## Running

```bash
pip install -r requirements.txt
python run.py                 # http://localhost:5000
```

## Custom date ranges

`GET /api/env/city/<city>` and `GET /api/predictions/<city>[/<variable>]` accept
either a relative `?days=N` window (default) or an explicit
`?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD` range. Both ends are validated
(`app/utils/validation.py`) and return HTTP 400 with a message on malformed
or inverted ranges. `GET /api/env/capabilities` returns each provider's actual
date-range limits (Google Pollen: ~5-day forecast only; Open-Meteo: 7-day
forecast / 92-day past / full weather archive) so a frontend date picker can
clamp itself instead of hard-coding those numbers.

## Testing

```bash
python -m unittest discover -s tests -v
```

Tests cover `parse_date_range()` and the predictions date-range filter only
(pure functions, no MongoDB required). Route-level/integration testing needs
a running MongoDB -- not currently automated.

## Note on the `predictions` blueprint

The brief architecture for this project describes a separate `ml_service/`
Flask app (port 5002) and an `analysis.py` route module. The actual v1
codebase instead ships ML forecasting as `data_collection/predictor.py`,
invoked in-process by `routes/predictions.py` (`POST /api/predictions/run`).
v2 development adapts to this existing structure rather than introducing a
second Flask service, to avoid duplicating the RandomForest logic.

## Timestamps

All datetimes leaving the API carry an explicit `+00:00` offset, rendered by
`app/utils/serialization.py` (`iso_utc`, `serialize_doc`). MongoDB stores BSON
dates as UTC and pymongo returns them naive; emitting `.isoformat()` directly
would produce an offset-less string, which browsers parse as **local** time --
a silent two to three hour error for a Greek deployment. Use `serialize_doc`
for any new route that returns documents.

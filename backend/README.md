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
    │   ├── env_data.py       ← GET /api/env/latest, GET /api/env/city/<city>
    │   └── predictions.py    ← GET/POST /api/predictions/* (lazily imports data_collection/predictor.py)
    └── utils/
        └── geo.py            ← haversine distance, nearest_city()
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

## Note on the `predictions` blueprint

The brief architecture for this project describes a separate `ml_service/`
Flask app (port 5002) and an `analysis.py` route module. The actual v1
codebase instead ships ML forecasting as `data_collection/predictor.py`,
invoked in-process by `routes/predictions.py` (`POST /api/predictions/run`).
v2 development adapts to this existing structure rather than introducing a
second Flask service, to avoid duplicating the RandomForest logic.

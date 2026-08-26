"""
Prediction routes for the AllergyMap platform.

Routes:
  GET  /api/predictions/<city>             — latest stored forecast for all variables
                                              (optional ?start_date=&end_date=YYYY-MM-DD)
  GET  /api/predictions/<city>/<variable>  — latest stored forecast for one variable
                                              (optional ?start_date=&end_date=YYYY-MM-DD)
  POST /api/predictions/run                — trigger ML training + forecast for a city
        body: {"city": "Athens", "source": "csv"}   (source optional, default "csv")

start_date/end_date filter the stored forecast (predictor.py generates 168
hourly points = 7 days from whenever /run last completed for that city). A
range outside that stored window is not an error -- it just returns an empty
list, since re-running the forecast is a separate, explicit POST /run call.

The POST endpoint imports predictor.py from data_collection/ at call time,
so scikit-learn / numpy are only required when a prediction is actually run.

Path resolution works in both environments:
  - Local dev  : project_root/backend/app/routes/ → up 4 levels → project_root/data_collection/
  - Docker     : /app/app/routes/ → up 3 levels → /app/data_collection/
"""

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, request, current_app
from ..extensions import mongo
from ..utils.serialization import iso_utc, serialize_doc
from ..utils.validation import parse_date_range

bp = Blueprint("predictions", __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize(doc: dict) -> dict:
    """Make a prediction document JSON-safe, timestamps in explicit UTC."""
    return serialize_doc(doc, datetime_fields=("forecast_timestamp", "generated_at"))


def _find_data_collection() -> Path:
    """
    Return the path to data_collection/ regardless of whether we're running
    in Docker (backend/ flattened into /app/) or local dev.
    """
    here = Path(__file__).resolve()
    # Try ancestor distances 3 (Docker) and 4 (local dev)
    for depth in (3, 4):
        candidate = here.parents[depth - 1] / "data_collection"
        if (candidate / "predictor.py").exists():
            return candidate
    raise RuntimeError(
        "Cannot find data_collection/predictor.py. "
        "Set DATA_COLLECTION_DIR env var or check the project layout."
    )


def _import_predictor():
    """Lazily import predictor.py, adding data_collection/ to sys.path if needed."""
    import os
    # Allow override via env var for unusual layouts
    dc_dir = os.environ.get("DATA_COLLECTION_DIR") or str(_find_data_collection())
    if dc_dir not in sys.path:
        sys.path.insert(0, dc_dir)
    import predictor as _p   # noqa: PLC0415
    return _p


def _filter_by_date_range(docs: list[dict], start_date, end_date) -> list[dict]:
    """Keep only docs whose forecast_timestamp date falls within [start_date, end_date]."""
    if not (start_date and end_date):
        return docs
    start_iso, end_iso = start_date.isoformat(), end_date.isoformat()
    return [d for d in docs if start_iso <= d["forecast_timestamp"][:10] <= end_iso]


def _latest_predictions(city: str, variable: str | None = None) -> list[dict]:
    """
    Return the most recent set of forecast documents for a city (+ optional variable).

    Strategy: find the maximum generated_at across matching documents,
    then return all docs with that generated_at (±1 second tolerance).
    Falls back to all stored docs if none are recent.
    """
    query: dict = {"city": city}
    if variable:
        query["variable"] = variable

    # Find the newest batch
    newest = mongo.db.predictions.find_one(
        query, sort=[("generated_at", -1)]
    )
    if not newest:
        return []

    cutoff = newest["generated_at"] - timedelta(seconds=5)
    query["generated_at"] = {"$gte": cutoff}

    docs = list(
        mongo.db.predictions
        .find(query)
        .sort("forecast_timestamp", 1)
    )
    return [_serialize(d) for d in docs]


# ---------------------------------------------------------------------------
# GET endpoints
# ---------------------------------------------------------------------------

@bp.get("/<city>")
def city_predictions(city: str):
    """Return the latest forecast for all variables for a city, grouped by variable."""
    try:
        start_date, end_date = parse_date_range(request.args)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    docs = _filter_by_date_range(_latest_predictions(city), start_date, end_date)
    if not docs:
        return jsonify({"city": city, "variables": {}, "message": "No predictions found. POST /api/predictions/run to generate."}), 404

    by_var: dict[str, list] = {}
    for d in docs:
        var = d.pop("variable", "unknown")
        by_var.setdefault(var, []).append({
            "forecast_timestamp": d["forecast_timestamp"],
            "predicted_value": d["predicted_value"],
        })

    return jsonify({"city": city, "model_used": docs[0].get("model_used"), "variables": by_var}), 200


@bp.get("/<city>/<variable>")
def city_variable_predictions(city: str, variable: str):
    """Return the latest forecast for one variable in a city."""
    try:
        start_date, end_date = parse_date_range(request.args)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    docs = _filter_by_date_range(_latest_predictions(city, variable), start_date, end_date)
    if not docs:
        return jsonify({"city": city, "variable": variable, "forecasts": [], "message": "No predictions found."}), 404

    return jsonify({
        "city": city,
        "variable": variable,
        "model_used": docs[0].get("model_used"),
        "generated_at": docs[0].get("generated_at"),
        "forecasts": [
            {"forecast_timestamp": d["forecast_timestamp"], "predicted_value": d["predicted_value"]}
            for d in docs
        ],
    }), 200


# ---------------------------------------------------------------------------
# POST — trigger prediction run
# ---------------------------------------------------------------------------

@bp.post("/run")
def run_predictions():
    """
    Trigger ML training + forecast for a city.

    Body (JSON):
        city   : str   — city name (required)
        source : str   — "csv" or "mongo" (optional, default "csv")

    The predictor reads training data, trains RandomForest per variable,
    generates 168-hour forecasts and saves them to MongoDB.
    Returns the generated forecasts.
    """
    data = request.get_json(silent=True) or {}
    city = data.get("city", "").strip()
    if not city:
        return jsonify({"error": "city is required"}), 400

    source = data.get("source", "csv")
    if source not in ("csv", "mongo"):
        return jsonify({"error": "source must be 'csv' or 'mongo'"}), 400

    mongo_uri = current_app.config.get("MONGO_URI", "mongodb://localhost:27017/allergymap")

    try:
        predictor = _import_predictor()
    except Exception as exc:
        return jsonify({"error": f"Cannot load predictor module: {exc}"}), 500

    try:
        results = predictor.run_city(
            city=city,
            source=source,
            mongo_uri=mongo_uri,
            save_to_mongo=True,
        )
    except Exception as exc:
        return jsonify({"error": f"Prediction failed: {exc}"}), 500

    if not results:
        return jsonify({"city": city, "message": "No forecasts generated — check that training data exists."}), 422

    # Build response: variable → first + last forecast timestamps + count
    summary = {
        var: {
            "count": len(fcs),
            "from": iso_utc(fcs[0]["forecast_timestamp"]) if fcs else None,
            "to":   iso_utc(fcs[-1]["forecast_timestamp"]) if fcs else None,
        }
        for var, fcs in results.items()
    }
    return jsonify({"city": city, "model_used": predictor.MODEL_NAME, "variables": summary}), 200

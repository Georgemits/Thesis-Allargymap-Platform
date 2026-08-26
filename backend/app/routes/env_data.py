"""
Routes:
  GET /api/env/latest          — latest snapshot per city
  GET /api/env/city/<city>     — time series for one city
                                  (?days=7, or ?start_date=&end_date=YYYY-MM-DD)
  GET /api/env/capabilities    — provider date-range limits (for UI clamping)

Each returned document is the full env_snapshots document (see
backend/app/models/env_snapshot.py) -- pollen (grains/m3 + Google UPI) and
air_quality (including `dust`, CAMS-sourced via Open-Meteo) are exposed
together, unfiltered, alongside weather.
"""
from flask import Blueprint, jsonify, request
from ..extensions import mongo
from ..utils.serialization import serialize_doc
from ..utils.validation import parse_date_range, parse_positive_int

bp = Blueprint("env_data", __name__)

# Provider date-range limits, surfaced via GET /api/env/capabilities so the
# frontend date-picker can clamp its calendar instead of hard-coding these
# numbers separately. Must stay in sync with:
#   - data_collection/google_pollen_fetcher.py: MAX_FORECAST_DAYS
#   - data_collection/open_meteo_fetcher.py: fetch_forecast/fetch_past_days/fetch_historical
PROVIDER_CAPABILITIES = {
    "google_pollen": {
        "max_forecast_days": 5,
        "supports_history": False,
        "note": "Forecast only (today .. today+4). No historical/archive endpoint.",
    },
    "open_meteo": {
        "forecast_days": 7,
        "max_past_days": 92,
        "archive_from": "1940-01-01",
        "note": "Archive API covers weather only -- no pollen/air-quality history.",
    },
}


def _serialize(doc: dict) -> dict:
    """Make an env_snapshot document JSON-safe, timestamps in explicit UTC."""
    return serialize_doc(doc)


@bp.get("/latest")
def latest_per_city():
    pipeline = [
        {"$sort": {"timestamp": -1}},
        {"$group": {"_id": "$city", "doc": {"$first": "$$ROOT"}}},
        {"$replaceRoot": {"newRoot": "$doc"}},
    ]
    docs = list(mongo.db.env_snapshots.aggregate(pipeline))
    return jsonify([_serialize(d) for d in docs]), 200


@bp.get("/city/<city>")
def city_timeseries(city: str):
    """
    Time series for one city.

    Either a relative window (?days=7, default) or an explicit, validated
    range (?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD) -- the explicit range
    wins when both are supplied. See GET /api/env/capabilities for what date
    range each provider can actually populate.
    """
    from datetime import datetime, timezone, timedelta

    try:
        start_date, end_date = parse_date_range(request.args)
        days = parse_positive_int(request.args, "days", default=7, maximum=400)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if start_date and end_date:
        since = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
        until = datetime.combine(end_date, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
        query = {"city": city, "timestamp": {"$gte": since, "$lt": until}}
    else:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        query = {"city": city, "timestamp": {"$gte": since}}

    docs = list(mongo.db.env_snapshots.find(query).sort("timestamp", 1))
    return jsonify([_serialize(d) for d in docs]), 200


@bp.get("/capabilities")
def capabilities():
    """Static provider date-range limits (see PROVIDER_CAPABILITIES above)."""
    return jsonify(PROVIDER_CAPABILITIES), 200

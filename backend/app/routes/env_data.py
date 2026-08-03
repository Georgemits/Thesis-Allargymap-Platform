"""
Routes:
  GET /api/env/latest          — latest snapshot per city
  GET /api/env/city/<city>     — time series for one city (?days=7)

Each returned document is the full env_snapshots document (see
backend/app/models/env_snapshot.py) -- pollen (grains/m3 + Google UPI) and
air_quality (including `dust`, CAMS-sourced via Open-Meteo) are exposed
together, unfiltered, alongside weather.
"""
from flask import Blueprint, jsonify, request
from bson import ObjectId
from ..extensions import mongo

bp = Blueprint("env_data", __name__)


def _serialize(doc: dict) -> dict:
    doc["_id"] = str(doc["_id"])
    if "timestamp" in doc:
        doc["timestamp"] = doc["timestamp"].isoformat()
    return doc


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
    from datetime import datetime, timezone, timedelta
    days = int(request.args.get("days", 7))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    docs = list(
        mongo.db.env_snapshots
        .find({"city": city, "timestamp": {"$gte": since}})
        .sort("timestamp", 1)
    )
    return jsonify([_serialize(d) for d in docs]), 200

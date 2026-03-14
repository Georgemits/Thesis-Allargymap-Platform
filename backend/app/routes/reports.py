"""
Routes:
  POST /api/reports          — submit a symptom report
  GET  /api/reports          — list reports (optional ?city=&limit=)
  GET  /api/reports/heatmap  — GeoJSON FeatureCollection for Leaflet heatmap
"""
from flask import Blueprint, jsonify, request
from bson import ObjectId
from ..extensions import mongo
from ..models.report import build_report

bp = Blueprint("reports", __name__)


def _serialize(doc: dict) -> dict:
    doc["_id"] = str(doc["_id"])
    if "timestamp" in doc:
        doc["timestamp"] = doc["timestamp"].isoformat()
    return doc


@bp.post("/")
def submit_report():
    data = request.get_json(silent=True) or {}
    required = ["user_id", "lon", "lat", "symptoms"]
    missing = [f for f in required if f not in data]
    if missing:
        return jsonify({"error": f"Missing fields: {missing}"}), 400

    doc = build_report(
        user_id=data["user_id"],
        lon=data["lon"],
        lat=data["lat"],
        symptoms=data["symptoms"],
        city=data.get("city", ""),
        notes=data.get("notes", ""),
    )
    result = mongo.db.reports.insert_one(doc)
    return jsonify({"inserted_id": str(result.inserted_id)}), 201


@bp.get("/")
def list_reports():
    city = request.args.get("city")
    limit = min(int(request.args.get("limit", 100)), 500)
    query = {"city": city} if city else {}
    docs = list(mongo.db.reports.find(query).sort("timestamp", -1).limit(limit))
    return jsonify([_serialize(d) for d in docs]), 200


@bp.get("/heatmap")
def heatmap_geojson():
    """Return GeoJSON FeatureCollection for all reports (last 30 days by default)."""
    from datetime import datetime, timezone, timedelta
    days = int(request.args.get("days", 30))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    docs = list(mongo.db.reports.find(
        {"timestamp": {"$gte": since}},
        {"location": 1, "overall_severity": 1, "_id": 0}
    ))
    features = [
        {
            "type": "Feature",
            "geometry": d["location"],
            "properties": {"severity": d.get("overall_severity", 0)},
        }
        for d in docs
    ]
    return jsonify({"type": "FeatureCollection", "features": features}), 200

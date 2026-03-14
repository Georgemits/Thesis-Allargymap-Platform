from flask import Blueprint, jsonify

bp = Blueprint("health", __name__)


@bp.get("/health")
def health_check():
    return jsonify({"status": "ok"}), 200

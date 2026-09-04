"""Allergy profile routes.

Routes:
  GET    /api/profiles/allergens  -- the allergen catalogue (public, no identity)
  GET    /api/profiles/me         -- read this participant's allergy profile
  PUT    /api/profiles/me         -- create or replace it
  DELETE /api/profiles/me         -- delete it, keeping the participant

The catalogue endpoint exists so the profile page never hard-codes the
allergen list. Adding an allergen to `app.models.allergy_profile.ALLERGENS`
then makes it appear in the UI, in validation and in the correlation engine at
once, instead of in three places that can disagree.

`PUT` rather than `POST`: the page submits the participant's complete answer
and the stored map is replaced by it, which is what makes un-ticking an
allergen possible. Sending the same body twice leaves the same state.
"""

from flask import Blueprint, g, jsonify, request

from ..extensions import mongo
from ..models.allergy_profile import (
    COLLECTION_NAME,
    SEVERITY_LEVELS,
    build_profile_upsert,
    catalogue,
    validate_allergens,
)
from ..models.user import build_upsert
from ..utils.identity import require_device_id
from ..utils.serialization import serialize_doc

bp = Blueprint("profiles", __name__)

#: Datetime fields on an allergy_profiles document.
PROFILE_DATETIME_FIELDS = ("created_at", "updated_at")


def _serialize(doc: dict) -> dict:
    """Make an allergy profile JSON-safe, timestamps in explicit UTC."""
    return serialize_doc(doc, datetime_fields=PROFILE_DATETIME_FIELDS)


@bp.get("/allergens")
def list_allergens():
    """Return the allergens a participant can declare, and the severity scale.

    Public: it describes what the platform measures, not anything about a
    participant, so it needs no identity and sending one would only leak which
    device is looking at the page.

    Returns:
        200 with ``{"allergens": [...], "severity_levels": {...}}``.
    """
    return jsonify({
        "allergens": catalogue(),
        "severity_levels": {str(k): v for k, v in SEVERITY_LEVELS.items()},
    }), 200


@bp.get("/me")
@require_device_id
def read_profile():
    """Return this participant's allergy profile.

    Returns:
        200 with ``{"profile": {...}}``, or 404 when none has been saved yet --
        the normal state for a first visit, which the page renders as an empty
        form rather than an error.
    """
    doc = mongo.db[COLLECTION_NAME].find_one({"device_id": g.device_id})
    if doc is None:
        return jsonify({"error": "No allergy profile for this device."}), 404
    return jsonify({"profile": _serialize(doc)}), 200


@bp.put("/me")
@require_device_id
def save_profile():
    """Create or replace this participant's allergy profile.

    Body:
        allergens (dict): allergen key -> severity 0-3. Severity 0 is dropped;
            an empty object is accepted and means "reacts to nothing declared".

    Returns:
        200 with ``{"profile": {...}, "created": bool}``, or 400 with an
        explanatory message when an allergen key or severity is invalid.
    """
    data = request.get_json(silent=True) or {}
    try:
        allergens = validate_allergens(data.get("allergens"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    result = mongo.db[COLLECTION_NAME].update_one(
        {"device_id": g.device_id},
        build_profile_upsert(g.device_id, allergens),
        upsert=True,
    )

    # Saving a profile is the clearest signal of an active participant there
    # is, so register the device if this is its first contact.
    mongo.db.users.update_one(
        {"device_id": g.device_id},
        build_upsert(g.device_id),
        upsert=True,
    )

    doc = mongo.db[COLLECTION_NAME].find_one({"device_id": g.device_id})
    return jsonify({
        "profile": _serialize(doc),
        "created": result.upserted_id is not None,
    }), 200


@bp.delete("/me")
@require_device_id
def delete_profile():
    """Delete this participant's allergy profile, keeping the participant.

    Narrower than ``DELETE /api/users/me``: the identity record and the
    symptom reports are left alone, so someone can clear a profile they filled
    in wrongly without erasing their contribution to the dataset.

    Returns:
        200 with ``{"deleted": n}``.
    """
    result = mongo.db[COLLECTION_NAME].delete_one({"device_id": g.device_id})
    return jsonify({"deleted": result.deleted_count}), 200

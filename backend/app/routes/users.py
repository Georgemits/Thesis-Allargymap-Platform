"""Participant identity routes.

Routes:
  POST   /api/users/me   -- register the calling device, or refresh last_seen
  GET    /api/users/me   -- read the calling device's identity record
  DELETE /api/users/me   -- erase this participant (right to erasure)

Every route here identifies the caller solely from the ``X-Device-Id``
header; there is no session, no cookie and no login. See `app.utils.identity`
for why, and for the validation the header goes through before any of these
views run.

There is no route that takes a device identifier as a path or query
parameter, and that omission is deliberate: the identifier is a bearer
credential, so ``/api/users/<device_id>`` would put it in access logs and let
one participant address another's record by editing a URL. The API can only
ever speak about "me".
"""

from flask import Blueprint, g, jsonify, request

from ..extensions import mongo
from ..models.user import build_upsert
from ..utils.identity import require_device_id
from ..utils.serialization import serialize_doc

bp = Blueprint("users", __name__)

#: Collection holding the participant's allergy profile, keyed by the same
#: device_id. Named here so that erasure covers it.
PROFILE_COLLECTION = "allergy_profiles"

#: Datetime fields on a users document, for JSON rendering with an explicit
#: UTC offset (see app.utils.serialization).
USER_DATETIME_FIELDS = ("created_at", "last_seen_at")


def _serialize(doc: dict) -> dict:
    """Make a users document JSON-safe, timestamps in explicit UTC."""
    return serialize_doc(doc, datetime_fields=USER_DATETIME_FIELDS)


@bp.post("/me")
@require_device_id
def register_me():
    """Register the calling device, or refresh its last-seen time.

    Idempotent: the first call inserts the identity record, every later call
    only updates ``last_seen_at``. The client may therefore call this on every
    page load without special-casing "am I registered yet".

    Body (optional):
        alias (str): A short participant-chosen label such as "phone". Omit
            the field entirely to leave any existing alias untouched.

    Returns:
        200 with ``{"user": {...}, "created": bool}``.
    """
    data = request.get_json(silent=True) or {}
    alias = data.get("alias")  # absent -> None -> existing alias preserved

    result = mongo.db.users.update_one(
        {"device_id": g.device_id},
        build_upsert(g.device_id, alias=alias),
        upsert=True,
    )
    doc = mongo.db.users.find_one({"device_id": g.device_id})
    return jsonify({"user": _serialize(doc), "created": result.upserted_id is not None}), 200


@bp.get("/me")
@require_device_id
def read_me():
    """Return the calling device's identity record.

    Returns:
        200 with ``{"user": {...}}``, or 404 when this device has never
        registered -- which is the normal answer for a browser whose storage
        was cleared, not an error condition the client should hide.
    """
    doc = mongo.db.users.find_one({"device_id": g.device_id})
    if doc is None:
        return jsonify({"error": "Unknown device."}), 404
    return jsonify({"user": _serialize(doc)}), 200


@bp.delete("/me")
@require_device_id
def erase_me():
    """Erase this participant: identity record, allergy profile, and links.

    The identity record and the allergy profile are deleted outright. Symptom
    reports are, by default, **anonymised rather than deleted**: the
    ``user_id`` field is unset, which severs the link to the participant while
    leaving the observation itself in the dataset. A report is a measurement
    of the environment at a place and time; removing it would silently rewrite
    results already published from that data, and once the link is gone the
    remaining document says nothing about who submitted it.

    A participant who wants the observations gone as well can say so
    explicitly.

    Query params:
        delete_reports (str): ``true`` to delete the reports outright instead
            of anonymising them.

    Returns:
        200 with a per-collection count of what the call changed.
    """
    delete_reports = request.args.get("delete_reports", "").strip().lower() in ("1", "true", "yes")

    user_result = mongo.db.users.delete_one({"device_id": g.device_id})
    profile_result = mongo.db[PROFILE_COLLECTION].delete_many({"device_id": g.device_id})

    if delete_reports:
        reports_deleted = mongo.db.reports.delete_many({"user_id": g.device_id}).deleted_count
        reports_anonymized = 0
    else:
        reports_deleted = 0
        reports_anonymized = mongo.db.reports.update_many(
            {"user_id": g.device_id},
            {"$unset": {"user_id": ""}},
        ).modified_count

    return jsonify({
        "deleted": {
            "user": user_result.deleted_count,
            "profile": profile_result.deleted_count,
            "reports_deleted": reports_deleted,
            "reports_anonymized": reports_anonymized,
        }
    }), 200

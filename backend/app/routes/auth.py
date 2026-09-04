"""Account routes (optional sign-in over the anonymous device identity).

Routes:
  POST /api/auth/register  -- attach an account to the calling device
  POST /api/auth/login     -- exchange username + password for that device id
  GET  /api/auth/status    -- does the calling device have an account?

The model here is unusual enough to be worth stating plainly. The thing that
authorises every request in this platform is the **device id** (see
`app.utils.identity`); an account does not replace it, it *recovers* it:

* **Register** attaches a username and password hash to the user document of
  the device that is calling. Everything that device contributed anonymously
  -- symptom reports, an allergy profile -- therefore stays with the person,
  instead of being stranded under an identity they can no longer reach.
* **Login** looks the account up and hands back its `device_id`, which the
  client then uses as its own identity. That is what makes a profile follow
  someone to a second device or survive clearing their browser.
* **Logout** is purely client-side: the browser forgets the id and generates a
  fresh anonymous one. There is no server-side session to end.

Consequences worth being honest about, all documented in the thesis:

* The `device_id` is a long-lived bearer credential. There is no session
  expiry, so signing out matters on a shared computer.
* There is no mail server behind this platform, so a forgotten password cannot
  be reset and the account is unrecoverable. That is why the profile transfer
  code still exists alongside accounts.
* Login is not rate-limited, so this design would need a throttle before any
  real-world deployment.
"""

from flask import Blueprint, g, jsonify, request
from pymongo.errors import DuplicateKeyError
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import mongo
from ..models.user import (
    build_upsert,
    normalize_username,
    public_user,
    validate_password,
)
from ..utils.identity import require_device_id
from ..utils.serialization import serialize_doc

from datetime import datetime, timezone

bp = Blueprint("auth", __name__)

#: Datetime fields on a users document, for JSON rendering with an explicit
#: UTC offset (see app.utils.serialization).
USER_DATETIME_FIELDS = ("created_at", "last_seen_at", "account_created_at")

#: A hash of a password nobody has, checked when the username does not exist so
#: that a failed login costs the same time either way. Without it, "no such
#: user" would return measurably faster than "wrong password" and the endpoint
#: would quietly answer the question "is this username taken".
_DUMMY_PASSWORD_HASH = generate_password_hash("this-password-belongs-to-no-account")

#: Deliberately identical for both failure modes, for the same reason.
_LOGIN_FAILED = "Invalid username or password."


def _serialize(doc: dict) -> dict:
    """Render a users document for a response: public fields only, UTC stamps."""
    return serialize_doc(public_user(doc), datetime_fields=USER_DATETIME_FIELDS)


@bp.post("/register")
@require_device_id
def register():
    """Attach a username and password to the calling device.

    Body:
        username (str): 3-30 characters, letters/digits/`.`/`_`/`-`.
        password (str): at least 8 characters.

    Returns:
        201 with ``{"user": {...}}`` on success; 400 when the username or
        password is malformed; 409 when this device already has an account or
        the username is taken.
    """
    data = request.get_json(silent=True) or {}
    try:
        username = normalize_username(data.get("username"))
        password = validate_password(data.get("password"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    # Make sure the device has a user document, then look at it: a device that
    # already carries an account must sign out first, otherwise a second
    # registration would silently take over the first person's data.
    mongo.db.users.update_one(
        {"device_id": g.device_id}, build_upsert(g.device_id), upsert=True
    )
    existing = mongo.db.users.find_one({"device_id": g.device_id})
    if existing.get("username"):
        return jsonify({"error": "This device is already signed in. Sign out first."}), 409

    if mongo.db.users.find_one({"username": username}, {"_id": 1}):
        return jsonify({"error": "That username is taken."}), 409

    try:
        mongo.db.users.update_one(
            {"device_id": g.device_id},
            {"$set": {
                "username": username,
                "password_hash": generate_password_hash(password),
                "account_created_at": datetime.now(timezone.utc),
            }},
        )
    except DuplicateKeyError:
        # Two registrations for the same username raced past the check above;
        # the unique index is what actually decides, and it decided against us.
        return jsonify({"error": "That username is taken."}), 409

    doc = mongo.db.users.find_one({"device_id": g.device_id})
    return jsonify({"user": _serialize(doc)}), 201


@bp.post("/login")
def login():
    """Exchange a username and password for that account's device id.

    No ``X-Device-Id`` header is required: the whole point of signing in is to
    work from a browser that has no useful identity of its own yet.

    Body:
        username (str)
        password (str)

    Returns:
        200 with ``{"device_id": "...", "user": {...}}``. The `device_id` is
        the caller's identity from now on -- it is what goes in the
        `X-Device-Id` header of every later request.
        401 with a deliberately vague message on any failure.
    """
    data = request.get_json(silent=True) or {}
    raw_username = data.get("username")
    password = data.get("password")

    if not raw_username or not password:
        return jsonify({"error": _LOGIN_FAILED}), 401

    try:
        username = normalize_username(raw_username)
    except ValueError:
        # Malformed usernames cannot exist in the database, so this is a failed
        # login rather than a validation error -- answering "that is not a
        # valid username" here would help someone map the account space.
        check_password_hash(_DUMMY_PASSWORD_HASH, str(password))
        return jsonify({"error": _LOGIN_FAILED}), 401

    doc = mongo.db.users.find_one({"username": username})
    if doc is None or not doc.get("password_hash"):
        check_password_hash(_DUMMY_PASSWORD_HASH, str(password))
        return jsonify({"error": _LOGIN_FAILED}), 401

    if not check_password_hash(doc["password_hash"], str(password)):
        return jsonify({"error": _LOGIN_FAILED}), 401

    mongo.db.users.update_one(
        {"device_id": doc["device_id"]},
        {"$set": {"last_seen_at": datetime.now(timezone.utc)}},
    )
    doc = mongo.db.users.find_one({"device_id": doc["device_id"]})
    return jsonify({"device_id": doc["device_id"], "user": _serialize(doc)}), 200


@bp.get("/status")
@require_device_id
def status():
    """Report whether the calling device carries an account.

    Cheap enough for a page to call on load, and it never 404s: a device with
    no user document at all is simply not signed in.

    Returns:
        200 with ``{"signed_in": bool, "username": str | None}``.
    """
    doc = mongo.db.users.find_one({"device_id": g.device_id}, {"username": 1}) or {}
    username = doc.get("username")
    return jsonify({"signed_in": bool(username), "username": username}), 200

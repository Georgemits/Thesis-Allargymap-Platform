"""Participant document schema (stored in MongoDB collection: users).

One document per *device*, not per person: the platform deliberately holds no
account, no e-mail address and no password (see `app.utils.identity` for the
reasoning). The document exists so that symptom reports, an allergy profile
and personalised alerts can be tied to the same anonymous participant over
time, and so the alerting engine has a list of participants to evaluate.

Document shape::

    {
        "_id": ObjectId,
        "device_id": str,            # UUID v4, unique -- the identity itself
        "created_at": datetime,      # UTC, first time this device was seen
        "last_seen_at": datetime,    # UTC, most recent request from this device
        "alias": str,                # optional, participant-chosen label
        "username": str,             # optional account, unique, lower-case
        "password_hash": str,        # optional account, scrypt (werkzeug)
        "account_created_at": datetime,   # UTC, when the account was attached
        "schema_version": int,
    }

Accounts sit **on top of** the device identity rather than replacing it. A
participant can use the platform with no account at all; creating one attaches
a username and password hash to the device document they already have, so
everything they contributed anonymously stays theirs. Signing in on another
device hands that device the same `device_id`, which is what makes the profile
follow the person. The identity that authorises every request is therefore
always the `device_id` -- the account is the way to *recover* it, not a second
parallel notion of "who".

`password_hash` must never leave the API: use `public_user` for anything that
is serialised into a response.

The allergy profile itself (allergens and per-allergen severity) is **not**
embedded here; it lives in its own collection keyed by the same `device_id`,
so that a profile can be replaced or erased without touching the identity
record, and so the two can be indexed and queried independently.

MongoDB indexes to create at startup (see docker/mongo-init/init.js):
  - device_id (1), unique
  - last_seen_at (-1)
  - username (1), unique *and sparse* -- accounts are optional, and a plain
    unique index would treat every account-less document as sharing the value
    `null`, so the second anonymous participant would be rejected.
"""

import re
from datetime import datetime, timezone

#: Current shape of a users document. Bump when the shape changes in a way
#: that existing documents do not satisfy, so a migration can find them.
USER_SCHEMA_VERSION = 1

#: Upper bound on the participant-chosen label. The field is a convenience
#: for someone running the app on two devices ("phone", "laptop"); it is not
#: a name field, and the limit keeps it from becoming a free-text store of
#: personal data.
ALIAS_MAX_LENGTH = 40


def sanitize_alias(alias) -> str:
    """Reduce a client-supplied alias to a safe, bounded string.

    Args:
        alias: The raw value from the request body; any type, possibly None.

    Returns:
        The alias stripped of surrounding whitespace and truncated to
        `ALIAS_MAX_LENGTH` characters, or an empty string when absent.

    Examples:
        >>> sanitize_alias('  phone  ')
        'phone'
        >>> sanitize_alias(None)
        ''
    """
    if alias is None:
        return ""
    return str(alias).strip()[:ALIAS_MAX_LENGTH]


def build_user(device_id: str, alias=None, now: datetime = None) -> dict:
    """Return a complete users document ready for insertion.

    Args:
        device_id: Canonical device identifier (validate it with
            `app.utils.identity.normalize_device_id` before calling this).
        alias: Optional participant-chosen label.
        now: Creation instant; defaults to the current UTC time. Injectable so
            tests do not depend on the clock.

    Returns:
        The document to insert.
    """
    moment = now or datetime.now(timezone.utc)
    return {
        "device_id": str(device_id),
        "created_at": moment,
        "last_seen_at": moment,
        "alias": sanitize_alias(alias),
        "schema_version": USER_SCHEMA_VERSION,
    }


def build_upsert(device_id: str, alias=None, now: datetime = None) -> dict:
    """Return the update document for a register-or-touch upsert.

    Registration and "this participant is active again" are the same
    operation from the client's point of view, so both go through a single
    idempotent upsert. Two details matter:

    * `created_at` and `schema_version` go under ``$setOnInsert``, so a repeat
      call never rewrites the date a participant first joined -- which the
      evaluation chapter needs in order to describe the cohort.
    * `alias` is written only when the caller actually supplied one. Putting
      ``None`` in ``$set`` would silently blank an alias the participant had
      already chosen on every subsequent request that omitted the field.

    Args:
        device_id: Canonical device identifier.
        alias: New alias, or None to leave any existing alias untouched.
        now: Update instant; defaults to the current UTC time.

    Returns:
        A MongoDB update document to pass to
        ``users.update_one({'device_id': ...}, update, upsert=True)``.

    Examples:
        >>> update = build_upsert('3f2a9c1e-7b4d-4a6f-9e21-0c8d5b1a2f30')
        >>> 'alias' in update['$set']
        False
    """
    moment = now or datetime.now(timezone.utc)
    update = {
        "$set": {"last_seen_at": moment},
        "$setOnInsert": {
            "device_id": str(device_id),
            "created_at": moment,
            "schema_version": USER_SCHEMA_VERSION,
        },
    }

    if alias is None:
        # Never seen before? Then it starts empty; otherwise leave it alone.
        update["$setOnInsert"]["alias"] = ""
    else:
        update["$set"]["alias"] = sanitize_alias(alias)

    return update


# ── Accounts ──────────────────────────────────────────────────────────────

#: Usernames are stored lower-case and matched case-insensitively, so that
#: "George" and "george" cannot be two accounts -- a difference nobody would
#: notice while typing but that would silently split one person's data in two.
USERNAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{1,28}[a-z0-9])$")
USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 30

#: A floor, not a policy. There is no mail server behind this platform, so a
#: forgotten password cannot be reset -- documented as a known limitation
#: rather than papered over with a reset flow that could never send anything.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128

#: Fields that may be serialised into an API response. A whitelist rather than
#: a blacklist: a field added to the document later is invisible until someone
#: deliberately publishes it, which is the safe direction for a collection that
#: holds a password hash.
PUBLIC_USER_FIELDS = (
    "device_id",
    "created_at",
    "last_seen_at",
    "alias",
    "username",
    "account_created_at",
    "schema_version",
)


def normalize_username(raw) -> str:
    """Validate a username and return its canonical (lower-case) form.

    Args:
        raw: The value from the request body.

    Returns:
        The lower-cased username.

    Raises:
        ValueError: If it is missing, the wrong length, or contains anything
            other than letters, digits, dot, underscore or hyphen (and does not
            start or end with punctuation). Callers should return HTTP 400.

    Examples:
        >>> normalize_username('  George.M  ')
        'george.m'
    """
    if raw is None:
        raise ValueError("Username is required.")

    candidate = str(raw).strip().lower()
    if not (USERNAME_MIN_LENGTH <= len(candidate) <= USERNAME_MAX_LENGTH):
        raise ValueError(
            f"Username must be {USERNAME_MIN_LENGTH}-{USERNAME_MAX_LENGTH} characters."
        )
    if not USERNAME_PATTERN.match(candidate):
        raise ValueError(
            "Username may contain letters, digits, dots, underscores and hyphens, "
            "and must start and end with a letter or digit."
        )
    return candidate


def validate_password(raw) -> str:
    """Check a password meets the minimum requirements and return it.

    The password is never stored or logged in this form -- the caller hashes it
    immediately.

    Args:
        raw: The value from the request body.

    Returns:
        The password unchanged (deliberately not trimmed: leading and trailing
        spaces are legitimate characters and stripping them would silently
        change what the participant typed).

    Raises:
        ValueError: If it is missing or outside the length bounds.
    """
    if raw is None or not isinstance(raw, str) or raw == "":
        raise ValueError("Password is required.")
    if len(raw) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters.")
    if len(raw) > PASSWORD_MAX_LENGTH:
        raise ValueError(f"Password must be at most {PASSWORD_MAX_LENGTH} characters.")
    return raw


def public_user(doc: dict) -> dict:
    """Return the publishable subset of a users document.

    Args:
        doc: A users document straight from MongoDB.

    Returns:
        A new dict containing only `PUBLIC_USER_FIELDS` that are present, plus
        a `has_account` flag so a client can branch without inspecting whether
        `username` happens to be there.

    Examples:
        >>> 'password_hash' in public_user({'device_id': 'x', 'password_hash': 'secret'})
        False
    """
    public = {field: doc[field] for field in PUBLIC_USER_FIELDS if field in doc}
    public["has_account"] = bool(doc.get("username"))
    return public

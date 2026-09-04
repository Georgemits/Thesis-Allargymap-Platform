"""Participant document schema (stored in MongoDB collection: users).

One document per *device*, not per person: the platform deliberately holds no
account, no e-mail address and no password (see `app.utils.identity` for the
reasoning). The document exists so that symptom reports, an allergy profile
and personalised alerts can be tied to the same anonymous participant over
time, and so the alerting engine has a list of participants to evaluate.

Document shape::

    {
        "_id": ObjectId,
        "device_id": str,            # UUID v4, unique -- the anonymous identity
        "created_at": datetime,      # UTC, first time this device was seen
        "last_seen_at": datetime,    # UTC, most recent request from this device
        "alias": str,                # optional, participant-chosen label
        "schema_version": int,
    }

The allergy profile itself (allergens and per-allergen severity) is **not**
embedded here; it lives in its own collection keyed by the same `device_id`,
so that a profile can be replaced or erased without touching the identity
record, and so the two can be indexed and queried independently.

MongoDB indexes to create at startup (see docker/mongo-init/init.js):
  - device_id (1), unique
  - last_seen_at (-1)
"""

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

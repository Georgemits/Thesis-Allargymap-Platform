"""Allergy profile schema (stored in MongoDB collection: allergy_profiles).

One profile per anonymous device: which allergens affect this participant, and
how badly. It is the input the correlation engine and the risk score are
personalised on -- without it the platform can only say "olive pollen is high
in Patras today", and not "this is a bad day *for you*".

Document shape::

    {
        "_id": ObjectId,
        "device_id": str,          # UUID v4, unique -- see app.utils.identity
        "allergens": {             # only allergens the participant reacts to
            "olive_pollen": 3,     # severity 1-3; absent means "not affected"
            "saharan_dust": 1,
        },
        "created_at": datetime,    # UTC
        "updated_at": datetime,    # UTC
        "schema_version": int,
    }

Kept in its own collection rather than embedded in the users document so that
a profile can be replaced or erased without touching the identity record, and
so both can be indexed and queried independently.

MongoDB indexes to create at startup (see docker/mongo-init/init.js):
  - device_id (1), unique

Why this particular list of allergens
-------------------------------------
Every allergen a participant can declare **must correspond to something the
platform actually measures**, otherwise a profile entry can never produce a
risk score or an alert -- the participant would tick a box and nothing would
ever come of it. Each entry below therefore carries the exact path into an
`env_snapshots` document (see `app.models.env_snapshot`) that the correlation
engine reads. House dust mite, pet dander and mould are the notable omissions:
they are clinically important, but they are indoor allergens that no
environmental feed in this system reports.
"""

from datetime import datetime, timezone

#: MongoDB collection holding these documents. Defined here and imported by
#: both routes/profiles.py and routes/users.py (erasure), so the name cannot
#: drift between the module that writes profiles and the one that deletes them.
COLLECTION_NAME = "allergy_profiles"

#: Current shape of an allergy_profiles document.
PROFILE_SCHEMA_VERSION = 1

#: Severity is a stable property of the participant ("how badly does olive
#: pollen affect you"), not a measurement of a moment, so it is a small ordinal
#: scale rather than the 0-10 VAS used for reporting symptoms. Four levels are
#: about as fine as anyone can answer consistently, and they double as the
#: weights 0..3 in the risk score.
SEVERITY_LEVELS = {
    0: "not affected",
    1: "mild",
    2: "moderate",
    3: "severe",
}

MIN_SEVERITY = 1  # 0 is stored as "absent from the document", not as a zero
MAX_SEVERITY = 3

#: The allergens a participant can declare, each mapped to the measurement it
#: is scored against. `paths` are dotted paths into an env_snapshots document,
#: most significant first. `season` is a typical Greek season, shown in the UI
#: as a hint only -- the actual in-season decision comes from the data.
ALLERGENS = {
    "olive_pollen": {
        "label": "Olive pollen",
        "group": "pollen",
        "paths": ["pollen.olive_pollen"],
        "unit": "grains/m³",
        "season": "April – June",
    },
    "grass_pollen": {
        "label": "Grass pollen",
        "group": "pollen",
        "paths": ["pollen.grass_pollen"],
        "unit": "grains/m³",
        "season": "April – July",
    },
    "birch_pollen": {
        "label": "Birch pollen",
        "group": "pollen",
        "paths": ["pollen.birch_pollen"],
        "unit": "grains/m³",
        "season": "March – May",
    },
    "alder_pollen": {
        "label": "Alder pollen",
        "group": "pollen",
        "paths": ["pollen.alder_pollen"],
        "unit": "grains/m³",
        "season": "January – March",
    },
    "ragweed_pollen": {
        "label": "Ragweed pollen (ambrosia)",
        "group": "pollen",
        "paths": ["pollen.ragweed_pollen"],
        "unit": "grains/m³",
        "season": "August – October",
    },
    "mugwort_pollen": {
        "label": "Mugwort pollen (artemisia)",
        "group": "pollen",
        "paths": ["pollen.mugwort_pollen"],
        "unit": "grains/m³",
        "season": "July – September",
    },
    "saharan_dust": {
        "label": "Saharan dust",
        "group": "particles",
        "paths": ["air_quality.dust"],
        "unit": "μg/m³",
        "season": "Episodic, mostly spring and summer",
    },
    "particulates": {
        "label": "Airborne particulates (PM10 / PM2.5)",
        "group": "particles",
        "paths": ["air_quality.pm10", "air_quality.pm2_5"],
        "unit": "μg/m³",
        "season": "Year-round, higher in winter",
    },
}


def catalogue() -> list:
    """Return the allergen catalogue in a form the frontend can render.

    Served by ``GET /api/profiles/allergens`` so the page never hard-codes the
    list: adding an allergen here makes it appear in the UI, in validation and
    in the correlation engine at once.

    Returns:
        A list of dicts, each with `key`, `label`, `group`, `unit` and
        `season`. The measurement `paths` are deliberately left out -- they are
        a backend implementation detail of no use to the browser.
    """
    return [
        {
            "key": key,
            "label": meta["label"],
            "group": meta["group"],
            "unit": meta["unit"],
            "season": meta["season"],
        }
        for key, meta in ALLERGENS.items()
    ]


def measurement_paths(allergen_key: str) -> list:
    """Return the env_snapshots paths an allergen is scored against.

    Args:
        allergen_key: A key of `ALLERGENS`.

    Returns:
        Dotted paths into an env_snapshots document, most significant first.

    Raises:
        KeyError: If the allergen is unknown.
    """
    return list(ALLERGENS[allergen_key]["paths"])


def validate_allergens(raw) -> dict:
    """Validate a submitted allergen map and return it in stored form.

    Severity 0 ("not affected") is dropped rather than stored: absence *is* the
    representation, which keeps the document to what the participant actually
    reacts to and lets the correlation engine iterate the map directly.

    Args:
        raw: The `allergens` object from the request body -- expected to be a
            mapping of allergen key to integer severity.

    Returns:
        A mapping of known allergen key to severity in 1..3, possibly empty
        (a participant is allowed to react to nothing).

    Raises:
        ValueError: On anything that is not such a mapping -- an unknown
            allergen key, or a severity that is not an integer in 0..3.
            Callers should surface this as HTTP 400.

    Examples:
        >>> validate_allergens({'olive_pollen': 3, 'grass_pollen': 0})
        {'olive_pollen': 3}
    """
    if raw is None:
        raise ValueError("Missing 'allergens'.")
    if not isinstance(raw, dict):
        raise ValueError("'allergens' must be an object of allergen -> severity.")

    unknown = sorted(set(raw) - set(ALLERGENS))
    if unknown:
        raise ValueError(
            f"Unknown allergen(s): {', '.join(unknown)}. "
            f"Known: {', '.join(sorted(ALLERGENS))}."
        )

    cleaned = {}
    for key, value in raw.items():
        # bool is an int subclass in Python; True would silently become 1.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"Severity for '{key}' must be an integer 0-{MAX_SEVERITY}.")
        if value < 0 or value > MAX_SEVERITY:
            raise ValueError(f"Severity for '{key}' must be between 0 and {MAX_SEVERITY}.")
        if value >= MIN_SEVERITY:
            cleaned[key] = value

    return cleaned


def build_profile_upsert(device_id: str, allergens: dict, now: datetime = None) -> dict:
    """Return the update document for saving a profile.

    The allergen map is **replaced**, not merged: the page always submits the
    participant's complete answer, and merging would make un-ticking an
    allergen impossible.

    Args:
        device_id: Canonical device identifier.
        allergens: Already validated by `validate_allergens`.
        now: Update instant; defaults to the current UTC time.

    Returns:
        A MongoDB update document for
        ``allergy_profiles.update_one({'device_id': ...}, update, upsert=True)``.
    """
    moment = now or datetime.now(timezone.utc)
    return {
        "$set": {
            "allergens": dict(allergens),
            "updated_at": moment,
        },
        "$setOnInsert": {
            "device_id": str(device_id),
            "created_at": moment,
            "schema_version": PROFILE_SCHEMA_VERSION,
        },
    }

"""Serialisation helpers shared by the API routes.

The one job here is timestamps. MongoDB stores BSON dates as UTC, and pymongo
hands them back as *naive* `datetime` objects. Calling `.isoformat()` on those
produces a string with no offset (``2026-08-03T00:00:00``), and browsers parse
a date-time string without an offset as **local** time. The API would then
describe a UTC instant while every client silently reinterpreted it in its own
timezone -- for a Greek deployment, a two to three hour error that never raises
anything.

`iso_utc` closes that gap by always emitting an explicit offset.
"""

from datetime import datetime, timezone


def iso_utc(value: datetime | None) -> str | None:
    """Render a datetime as an ISO-8601 string with an explicit UTC offset.

    Naive values are assumed to be UTC, which matches how pymongo returns BSON
    dates. Aware values are converted to UTC rather than trusted as-is, so a
    single response can never mix offsets.

    Args:
        value: The datetime to render, or None.

    Returns:
        An ISO-8601 string ending in ``+00:00``, or None if `value` was None.

    Examples:
        >>> iso_utc(datetime(2026, 8, 3, 0, 0))
        '2026-08-03T00:00:00+00:00'
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat()


def serialize_doc(doc: dict, *, datetime_fields: tuple[str, ...] = ("timestamp",)) -> dict:
    """Make one MongoDB document JSON-safe, in place.

    Converts ``_id`` to a string and renders the named datetime fields with
    `iso_utc`. Fields that are absent, or already strings, are left alone so
    the helper is safe to apply to documents from different collections.

    Args:
        doc: The document to convert.
        datetime_fields: Names of the fields holding datetimes.

    Returns:
        The same dictionary, mutated and returned for convenience.
    """
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    for field in datetime_fields:
        if isinstance(doc.get(field), datetime):
            doc[field] = iso_utc(doc[field])
    return doc

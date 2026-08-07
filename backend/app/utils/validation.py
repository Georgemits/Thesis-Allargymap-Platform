"""Shared request-parameter validation helpers for the AllergyMap API."""

from __future__ import annotations

from datetime import date, datetime

DATE_FORMAT = "%Y-%m-%d"
DEFAULT_MAX_RANGE_DAYS = 400  # generous sanity bound, not a provider limit


def parse_date_range(
    args,
    max_range_days: int = DEFAULT_MAX_RANGE_DAYS,
) -> tuple[date, date] | tuple[None, None]:
    """
    Parse optional `start_date` / `end_date` query params (YYYY-MM-DD each).

    Returns (None, None) when neither parameter is supplied -- callers should
    fall back to their own default (e.g. a relative `?days=` window).

    Raises ValueError (caller should catch and return HTTP 400) when:
      - only one of the two is supplied,
      - either value is not a valid YYYY-MM-DD date,
      - end_date is before start_date,
      - the range spans more than `max_range_days` days.

    This function only validates *shape* (a real calendar range); it does not
    know about provider-specific forecast windows -- see
    GET /api/env/capabilities for those (Google Pollen: ~5-day forecast only;
    Open-Meteo: 7-day forecast, 92-day past-days, full weather archive).
    """
    start_raw = args.get("start_date")
    end_raw = args.get("end_date")

    if not start_raw and not end_raw:
        return None, None
    if not start_raw or not end_raw:
        raise ValueError("Both start_date and end_date are required together (YYYY-MM-DD).")

    try:
        start = datetime.strptime(start_raw, DATE_FORMAT).date()
        end = datetime.strptime(end_raw, DATE_FORMAT).date()
    except ValueError:
        raise ValueError("start_date/end_date must be in YYYY-MM-DD format.")

    if end < start:
        raise ValueError("end_date must not be before start_date.")
    if (end - start).days > max_range_days:
        raise ValueError(f"Date range too large (max {max_range_days} days).")

    return start, end


def parse_positive_int(args, name: str, default: int, maximum: int | None = None) -> int:
    """
    Parse an optional positive-integer query param (e.g. ?days=7, ?limit=100).

    Returns `default` when the param is absent. When `maximum` is given the
    value is clamped to it rather than rejected, so an over-large request
    still succeeds with a capped result set.

    Raises ValueError (caller should catch and return HTTP 400) when the value
    is not an integer or is not >= 1 -- without this, a stray `?days=abc`
    would surface as an unhandled 500.
    """
    raw = args.get(name)
    if raw is None or raw == "":
        return default

    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer.")

    if value < 1:
        raise ValueError(f"{name} must be >= 1.")

    return min(value, maximum) if maximum is not None else value

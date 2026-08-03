"""
pollen_source.py — source-agnostic pollen abstraction for the AllergyMap platform.

Normalizes the two pollen providers into a single `env_snapshots` schema so the
rest of the pipeline (mongo_importer.py, the backend API, the ML predictor) never
has to know which provider produced a given reading:

  - google      : Google Maps Platform Pollen API (google_pollen_fetcher.py).
                   PRIMARY source. Daily Universal Pollen Index (UPI, 0-5) per
                   plant, forecast only (today .. today+4, hard 5-day cap).
  - open_meteo  : Open-Meteo air-quality API (open_meteo_fetcher.py).
                   FALLBACK + historical/archive source. Hourly grains/m3
                   concentration per plant, any date range (forecast, past
                   days, or full archive).

Selection rule (pollen_source="google" is the default):
  1. Try Google for each date present in the batch being imported.
  2. Any date outside Google's forecast window, or any failure calling the
     Google API (missing key, HTTP error, quota), falls back to Open-Meteo
     automatically for that date. Historical imports (source of the batch is
     `--mode past`/`--mode historical`) fall back for every row, since those
     dates are always outside Google's window.
  3. pollen_source="open_meteo" skips Google entirely (explicit opt-out).

Scale mapping (documented, not converted):
    Open-Meteo `pollen` fields are a continuous hourly concentration in
    grains/m3, modelled by the CAMS European pollen forecast (via Open-Meteo's
    air-quality API). Google's `pollen_upi` fields are a discrete daily
    category (0=None, 1=Very Low, 2=Low, 3=Moderate, 4=High, 5=Very High) per
    plant, computed by Google's own undisclosed model. Google publishes no
    official formula mapping UPI buckets to grains/m3, and the two are
    measured on different cadences (hourly vs daily) and likely different
    underlying pollen models. Converting one into the other would fabricate
    precision that doesn't exist, so AllergyMap stores both values side by
    side on every env_snapshots document instead -- this side-by-side view is
    itself a thesis-relevant comparison point (see README).

Resulting fields added to each env_snapshots document:
    "pollen"                       : unchanged, existing grains/m3 dict (always
                                      populated from Open-Meteo when available).
    "pollen_upi"                   : {"overall": {...}, "plants": {...}} from
                                      Google, or None when unavailable/skipped.
    "pollen_source"                : "google" | "open_meteo"
    "pollen_source_fallback_reason": present only when pollen_source ==
                                      "open_meteo" *because* Google was tried
                                      and failed/was out of range.
"""

from __future__ import annotations

from google_pollen_fetcher import fetch_forecast, load_api_key, parse_daily_info

# Must stay in sync with POLLEN_COLS in mongo_importer.py / AQI_VARS in
# open_meteo_fetcher.py (not imported directly, to avoid a circular import
# with mongo_importer, which imports this module).
OPEN_METEO_POLLEN_KEYS = [
    "alder_pollen",
    "birch_pollen",
    "grass_pollen",
    "mugwort_pollen",
    "olive_pollen",
    "ragweed_pollen",
]

VALID_SOURCES = ("google", "open_meteo")


# ---------------------------------------------------------------------------
# Pure normalizers (unit-testable without any HTTP call)
# ---------------------------------------------------------------------------

def normalize_open_meteo(pollen_row: dict | None) -> dict:
    """
    Return the `pollen` sub-document: all six Open-Meteo plant keys, present
    with a value of None for any key missing from `pollen_row`.
    """
    pollen_row = pollen_row or {}
    return {key: pollen_row.get(key) for key in OPEN_METEO_POLLEN_KEYS}


def normalize_google_day(day: dict) -> dict:
    """
    Return the `pollen_upi` sub-document from one entry of
    google_pollen_fetcher.parse_daily_info(), i.e. a dict shaped
    {"date": ..., "types": {...}, "plants": {...}}.
    """
    return {"overall": day.get("types", {}), "plants": day.get("plants", {})}


def build_pollen_fields(
    open_meteo_row: dict | None,
    google_day: dict | None,
    fallback_reason: str | None = None,
) -> dict:
    """
    Combine one Open-Meteo hourly reading and (optionally) one Google daily
    UPI reading into the three/four fields stored on an env_snapshots doc.

    Pass google_day=None to record an Open-Meteo-only (fallback) document;
    pass fallback_reason to explain *why* Google wasn't used.
    """
    fields = {
        "pollen": normalize_open_meteo(open_meteo_row),
        "pollen_upi": normalize_google_day(google_day) if google_day is not None else None,
        "pollen_source": "google" if google_day is not None else "open_meteo",
    }
    if google_day is None and fallback_reason:
        fields["pollen_source_fallback_reason"] = fallback_reason
    return fields


# ---------------------------------------------------------------------------
# Batch enrichment (used by mongo_importer.py)
# ---------------------------------------------------------------------------

def enrich_docs_with_pollen_source(
    docs: list[dict],
    source: str = "google",
    api_key: str | None = None,
) -> list[dict]:
    """
    Attach pollen_upi / pollen_source (/ pollen_source_fallback_reason) to
    each env_snapshots document in `docs`, in place, and return `docs`.

    Each doc must already have "city", "latitude", "longitude", "timestamp"
    (datetime), and "pollen" (Open-Meteo grains/m3 dict) -- i.e. the shape
    produced by mongo_importer.row_to_document().

    source="google" (default): groups docs by city and makes at most one
    Google Pollen API call per city, then maps each doc's calendar date onto
    that city's daily UPI forecast. Docs whose date falls outside the
    returned window, or every doc for a city whose Google call fails, fall
    back to Open-Meteo automatically.

    source="open_meteo": skip Google entirely (used for explicit historical
    imports, where every date is guaranteed to be outside Google's window
    anyway).
    """
    if source not in VALID_SOURCES:
        raise ValueError(f"Unknown pollen_source: {source!r} (expected one of {VALID_SOURCES})")

    if source == "open_meteo":
        for doc in docs:
            doc.update(build_pollen_fields(doc.get("pollen"), None))
        return docs

    api_key = api_key or load_api_key()
    if not api_key:
        for doc in docs:
            doc.update(
                build_pollen_fields(
                    doc.get("pollen"), None,
                    fallback_reason="GOOGLE_POLLEN_API_KEY not set",
                )
            )
        return docs

    docs_by_city: dict[str, list[dict]] = {}
    for doc in docs:
        docs_by_city.setdefault(doc["city"], []).append(doc)

    for city, city_docs in docs_by_city.items():
        lat = city_docs[0]["latitude"]
        lon = city_docs[0]["longitude"]

        try:
            raw = fetch_forecast(lat, lon, api_key)
            daily_by_date = {d["date"]: d for d in parse_daily_info(raw)}
        except Exception as exc:
            reason = f"Google Pollen API error: {exc}"
            for doc in city_docs:
                doc.update(build_pollen_fields(doc.get("pollen"), None, fallback_reason=reason))
            continue

        for doc in city_docs:
            doc_date = doc["timestamp"].date().isoformat()
            google_day = daily_by_date.get(doc_date)
            if google_day is not None:
                doc.update(build_pollen_fields(doc.get("pollen"), google_day))
            else:
                doc.update(
                    build_pollen_fields(
                        doc.get("pollen"), None,
                        fallback_reason="date outside Google's 5-day forecast window",
                    )
                )

    return docs

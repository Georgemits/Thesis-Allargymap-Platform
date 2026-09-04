"""Correlation engine: reported symptoms against measured environment.

This is the thesis' central analysis (supervisor TODO #2). A symptom report is
a person saying "I feel like this, here, now"; an environmental snapshot is a
model saying "this is what the air was like, in this city, at this hour". The
engine joins the two and asks whether they move together -- overall, per city,
and per participant.

How the join works
------------------
A report carries coordinates and a timestamp. Snapshots exist per city, hourly.
So each report is matched to the snapshot for its city whose timestamp is
closest, and only if that is within `DEFAULT_MATCH_WINDOW_HOURS`; a report with
no snapshot near it in time is dropped rather than matched to whatever exists,
which would silently pair an August symptom with a May measurement.

Reports carry a free-text `city` that a participant may have typed, mistyped or
left blank, so the city used for matching is derived from the coordinates
(`app.utils.geo.nearest_city`) -- the coordinates are the reliable field.

Exposure lag
------------
Allergic symptoms follow exposure rather than accompanying it: a person who
walked through high pollen in the morning may report in the evening. Every
analysis therefore takes a `lag_hours`, and matches a report against conditions
that many hours *earlier*. Running the same analysis at several lags and
reporting which one is strongest is itself a result, and one the evaluation
chapter can show.

What it deliberately does not do
--------------------------------
It does not claim causation, and it refuses to report a coefficient computed
from fewer than `MIN_SAMPLES` pairs (see `app.services.statistics`). It also
returns the number of tests it ran: correlating a dozen variables against seven
symptoms means dozens of hypotheses, among which one "significant" result is
expected by chance, and a reader cannot judge a p-value without knowing that.
"""

from __future__ import annotations

from bisect import bisect_left
from datetime import datetime, timedelta, timezone

from ..models.allergy_profile import ALLERGENS
from ..models.report import VAS_FIELDS
from ..utils.geo import nearest_city
from .statistics import (
    MIN_SAMPLES,
    correlation_p_value,
    describe_strength,
    mean,
    pearson,
    quantile,
    spearman,
)

#: How far in time a snapshot may be from a report and still describe it.
#: Snapshots are hourly, so three hours tolerates a gap in the collection
#: without stretching to a different part of the day.
DEFAULT_MATCH_WINDOW_HOURS = 3

#: Lags worth scanning when looking for the strongest response, in hours.
LAG_CANDIDATES = (0, 3, 6, 12, 24)

#: Minimum observations in one cell of a combination table before its mean is
#: shown. Lower than MIN_SAMPLES because a cell mean is a descriptive figure,
#: not an inferential claim -- but five is still the floor for saying anything.
MIN_CELL_SAMPLES = 5

#: The environmental variables the engine analyses, each with its path into an
#: env_snapshots document (see app.models.env_snapshot).
ENV_VARIABLES = {
    "olive_pollen":   {"label": "Olive pollen",      "path": "pollen.olive_pollen",   "unit": "grains/m³", "group": "pollen"},
    "grass_pollen":   {"label": "Grass pollen",      "path": "pollen.grass_pollen",   "unit": "grains/m³", "group": "pollen"},
    "birch_pollen":   {"label": "Birch pollen",      "path": "pollen.birch_pollen",   "unit": "grains/m³", "group": "pollen"},
    "alder_pollen":   {"label": "Alder pollen",      "path": "pollen.alder_pollen",   "unit": "grains/m³", "group": "pollen"},
    "ragweed_pollen": {"label": "Ragweed pollen",    "path": "pollen.ragweed_pollen", "unit": "grains/m³", "group": "pollen"},
    "mugwort_pollen": {"label": "Mugwort pollen",    "path": "pollen.mugwort_pollen", "unit": "grains/m³", "group": "pollen"},
    "dust":           {"label": "Saharan dust",      "path": "air_quality.dust",      "unit": "μg/m³",     "group": "particles"},
    "pm10":           {"label": "PM10",              "path": "air_quality.pm10",      "unit": "μg/m³",     "group": "particles"},
    "pm2_5":          {"label": "PM2.5",             "path": "air_quality.pm2_5",     "unit": "μg/m³",     "group": "particles"},
    "temperature":    {"label": "Temperature",       "path": "weather.temperature_2m", "unit": "°C",       "group": "weather"},
    "humidity":       {"label": "Relative humidity", "path": "weather.relative_humidity_2m", "unit": "%",  "group": "weather"},
}

#: Variables that modify a response rather than cause one. The supervisor's
#: brief asks specifically for allergen *combinations with* temperature and
#: humidity, which is what the combination table crosses.
MODIFIER_VARIABLES = ("temperature", "humidity")

#: Everything a report can be correlated against: the six symptoms plus the
#: computed overall severity.
SYMPTOM_KEYS = VAS_FIELDS + ("overall_severity",)


def _variable_for_path(path: str):
    """Return the ENV_VARIABLES key measuring `path`, or None."""
    for key, meta in ENV_VARIABLES.items():
        if meta["path"] == path:
            return key
    return None


def _build_allergen_variable_map() -> dict:
    """Map each declarable allergen to the variables that measure it.

    Built by matching the paths in the allergen catalogue against the paths in
    `ENV_VARIABLES`, so the two cannot drift apart: an allergen whose
    measurement this engine does not analyse fails at import rather than
    silently producing no results for whoever declared it.

    Raises:
        RuntimeError: If an allergen names a path no variable measures.
    """
    mapping = {}
    for allergen, meta in ALLERGENS.items():
        keys = [_variable_for_path(path) for path in meta["paths"]]
        missing = [path for path, key in zip(meta["paths"], keys) if key is None]
        if missing:
            raise RuntimeError(
                f"Allergen '{allergen}' is measured by {missing}, which "
                "app.services.correlation.ENV_VARIABLES does not analyse."
            )
        mapping[allergen] = keys
    return mapping


#: allergen key -> the ENV_VARIABLES keys measuring it, most significant first.
ALLERGEN_VARIABLES = _build_allergen_variable_map()


def as_utc(value):
    """Return a datetime as timezone-aware UTC.

    pymongo hands back naive datetimes for BSON dates, which are UTC; treating
    them as local time would shift every match by the timezone offset and, in
    Greece, quietly pair a report with the wrong part of the day.

    Args:
        value: A datetime, or None.

    Returns:
        An aware UTC datetime, or None.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_path(document, path: str):
    """Read a dotted path out of a nested document.

    Args:
        document: The document, e.g. an env_snapshots record.
        path: Dotted path such as ``"pollen.olive_pollen"``.

    Returns:
        The value, or None if any step is missing or the value is null. Nulls
        are expected and meaningful here -- the collector stores None for a
        variable a provider had no data for, and such an observation must be
        dropped from a correlation rather than counted as zero.

    Examples:
        >>> get_path({'pollen': {'olive_pollen': 4.0}}, 'pollen.olive_pollen')
        4.0
    """
    current = document
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def report_city(report: dict) -> str:
    """Return the city a report should be matched against.

    Derived from the coordinates rather than trusting the free-text `city`
    field, which a participant may have mistyped or left empty.

    Args:
        report: A reports document.

    Returns:
        A city name from `app.utils.geo.GREEK_CITIES`, or "" when the report
        carries no usable coordinates.
    """
    coordinates = get_path(report, "location.coordinates")
    if not isinstance(coordinates, (list, tuple)) or len(coordinates) != 2:
        return str(report.get("city") or "")
    lon, lat = coordinates
    try:
        return nearest_city(float(lat), float(lon))
    except (TypeError, ValueError):
        return str(report.get("city") or "")


def symptom_value(report: dict, symptom: str):
    """Return one symptom score from a report.

    Args:
        report: A reports document.
        symptom: A key of `SYMPTOM_KEYS`.

    Returns:
        The score as a float, or None when the report does not carry it.
    """
    if symptom == "overall_severity":
        value = report.get("overall_severity")
    else:
        value = get_path(report, f"symptoms.{symptom}")
    return None if value is None else float(value)


def index_snapshots(snapshots) -> dict:
    """Group snapshots by city and sort them by time, ready for matching.

    Args:
        snapshots: env_snapshots documents.

    Returns:
        ``{city: (sorted_times, sorted_docs)}``, so a match is a binary search
        rather than a scan of every snapshot per report.
    """
    by_city = {}
    for snapshot in snapshots:
        city = snapshot.get("city") or ""
        moment = as_utc(snapshot.get("timestamp"))
        if not city or moment is None:
            continue
        by_city.setdefault(city, []).append((moment, snapshot))

    index = {}
    for city, entries in by_city.items():
        entries.sort(key=lambda pair: pair[0])
        index[city] = ([e[0] for e in entries], [e[1] for e in entries])
    return index


def nearest_snapshot(index: dict, city: str, when: datetime, window_hours: float):
    """Find the snapshot for `city` closest to `when`, within `window_hours`.

    Args:
        index: The structure returned by `index_snapshots`.
        city: City name.
        when: The instant to match, timezone-aware.
        window_hours: Maximum acceptable distance in time.

    Returns:
        ``(snapshot, gap_hours)``, or ``(None, None)`` when the city is unknown
        or nothing falls inside the window.
    """
    if city not in index or when is None:
        return None, None

    times, docs = index[city]
    position = bisect_left(times, when)

    best = None
    best_gap = None
    for candidate in (position - 1, position):
        if 0 <= candidate < len(times):
            gap = abs((times[candidate] - when).total_seconds()) / 3600.0
            if best_gap is None or gap < best_gap:
                best, best_gap = docs[candidate], gap

    if best is None or best_gap > window_hours:
        return None, None
    return best, best_gap


def pair_observations(reports, snapshots, lag_hours: float = 0.0,
                      window_hours: float = DEFAULT_MATCH_WINDOW_HOURS) -> list:
    """Join symptom reports to the environmental conditions they were made in.

    Args:
        reports: reports documents.
        snapshots: env_snapshots documents.
        lag_hours: Match each report against conditions this many hours
            *before* it was submitted. 0 means "at the same time".
        window_hours: How far a snapshot may be from that instant.

    Returns:
        A list of dicts with `report`, `snapshot`, `city`, `gap_hours` and
        `timestamp`. Reports with no coordinates, no timestamp or no snapshot
        near them in time are omitted -- the count of what survived is the `n`
        every downstream result reports.
    """
    index = index_snapshots(snapshots)
    offset = timedelta(hours=lag_hours)

    pairs = []
    for report in reports:
        moment = as_utc(report.get("timestamp"))
        if moment is None:
            continue
        city = report_city(report)
        if not city:
            continue

        snapshot, gap = nearest_snapshot(index, city, moment - offset, window_hours)
        if snapshot is None:
            continue

        pairs.append({
            "report": report,
            "snapshot": snapshot,
            "city": city,
            "gap_hours": round(gap, 2),
            "timestamp": moment,
        })

    return pairs


def paired_series(pairs, variable_key: str, symptom: str):
    """Extract the two aligned samples for one variable and one symptom.

    Args:
        pairs: Output of `pair_observations`.
        variable_key: A key of `ENV_VARIABLES`.
        symptom: A key of `SYMPTOM_KEYS`.

    Returns:
        ``(environment_values, symptom_values)`` with pairs dropped wherever
        either side is missing, so both lists stay aligned and the same length.
    """
    path = ENV_VARIABLES[variable_key]["path"]

    environment = []
    symptoms = []
    for pair in pairs:
        env_value = get_path(pair["snapshot"], path)
        symptom_score = symptom_value(pair["report"], symptom)
        if env_value is None or symptom_score is None:
            continue
        try:
            environment.append(float(env_value))
        except (TypeError, ValueError):
            continue
        symptoms.append(symptom_score)

    return environment, symptoms


def correlate_variable(pairs, variable_key: str, symptom: str,
                       min_samples: int = MIN_SAMPLES) -> dict:
    """Correlate one environmental variable against one symptom.

    Args:
        pairs: Output of `pair_observations`.
        variable_key: A key of `ENV_VARIABLES`.
        symptom: A key of `SYMPTOM_KEYS`.
        min_samples: Below this many pairs no coefficient is reported.

    Returns:
        A dict with the variable's identity, `n`, `pearson`, `spearman`,
        `p_value`, `strength` and `insufficient_data`. When there is too little
        data the coefficients are None and `insufficient_data` is True -- the
        result still carries `n`, because "we looked and had 6 observations" is
        itself worth reporting.
    """
    meta = ENV_VARIABLES[variable_key]
    environment, symptoms = paired_series(pairs, variable_key, symptom)
    n = len(environment)

    if n < min_samples:
        return {
            "variable": variable_key,
            "label": meta["label"],
            "unit": meta["unit"],
            "group": meta["group"],
            "symptom": symptom,
            "n": n,
            "pearson": None,
            "spearman": None,
            "p_value": None,
            "strength": "none",
            "insufficient_data": True,
            "min_samples": min_samples,
        }

    r = pearson(environment, symptoms)
    rho = spearman(environment, symptoms)

    return {
        "variable": variable_key,
        "label": meta["label"],
        "unit": meta["unit"],
        "group": meta["group"],
        "symptom": symptom,
        "n": n,
        "pearson": None if r is None else round(r, 4),
        "spearman": None if rho is None else round(rho, 4),
        "p_value": None if r is None else round(correlation_p_value(r, n), 6),
        "strength": describe_strength(rho if rho is not None else r),
        "insufficient_data": False,
        "min_samples": min_samples,
    }


def correlate_all(pairs, symptom: str = "overall_severity", variables=None,
                  min_samples: int = MIN_SAMPLES) -> dict:
    """Correlate every environmental variable against one symptom.

    Args:
        pairs: Output of `pair_observations`.
        symptom: A key of `SYMPTOM_KEYS`.
        variables: Restrict to these `ENV_VARIABLES` keys; None means all.
        min_samples: Passed through to `correlate_variable`.

    Returns:
        A dict with `symptom`, `pairs` (how many joined observations went in),
        `tests_run` and `results`, sorted strongest first with the
        under-powered ones last.
    """
    keys = list(variables) if variables else list(ENV_VARIABLES)
    results = [correlate_variable(pairs, key, symptom, min_samples) for key in keys]

    def sort_key(result):
        if result["insufficient_data"]:
            return (1, 0.0)
        strongest = result["spearman"] if result["spearman"] is not None else result["pearson"]
        return (0, -abs(strongest or 0.0))

    results.sort(key=sort_key)

    return {
        "symptom": symptom,
        "pairs": len(pairs),
        "tests_run": sum(1 for r in results if not r["insufficient_data"]),
        "results": results,
    }


def scan_lags(reports, snapshots, symptom: str = "overall_severity",
              variable_key: str = None, lags=LAG_CANDIDATES,
              window_hours: float = DEFAULT_MATCH_WINDOW_HOURS) -> list:
    """Repeat the analysis at several exposure lags.

    Args:
        reports: reports documents.
        snapshots: env_snapshots documents.
        symptom: A key of `SYMPTOM_KEYS`.
        variable_key: Restrict to one variable, or None for all of them.
        lags: Lags in hours to try.
        window_hours: Matching tolerance.

    Returns:
        One `correlate_all` result per lag, each tagged with its `lag_hours`.
        Comparing them is how the analysis argues for a response delay rather
        than assuming one.
    """
    scans = []
    for lag in lags:
        pairs = pair_observations(reports, snapshots, lag_hours=lag, window_hours=window_hours)
        summary = correlate_all(
            pairs, symptom, [variable_key] if variable_key else None
        )
        summary["lag_hours"] = lag
        scans.append(summary)
    return scans


def tercile_bands(values):
    """Split a sample into low / medium / high at its own terciles.

    Fixed clinical thresholds would be invented: there is no agreed "high olive
    pollen" figure in grains/m³ that this thesis could cite, and picking one
    would decide the result in advance. Data-driven terciles instead describe
    the range that was actually observed, and the cut points are reported
    alongside the table so the reader can see them.

    Args:
        values: The sample.

    Returns:
        ``(low_cut, high_cut)``, or None when the sample is too small or so
        concentrated that the two cuts coincide (a pollen that was zero all
        summer cannot be split into three bands).
    """
    clean = sorted(v for v in values if v is not None)
    if len(clean) < 3:
        return None

    low_cut = quantile(clean, 1 / 3)
    high_cut = quantile(clean, 2 / 3)
    if low_cut is None or high_cut is None or low_cut >= high_cut:
        return None
    return low_cut, high_cut


def band_of(value, cuts) -> str:
    """Label a value low / medium / high against tercile cut points."""
    low_cut, high_cut = cuts
    if value <= low_cut:
        return "low"
    if value <= high_cut:
        return "medium"
    return "high"


BAND_ORDER = ("low", "medium", "high")


def combination_table(pairs, variable_key: str, modifier_key: str,
                      symptom: str = "overall_severity",
                      min_cell_samples: int = MIN_CELL_SAMPLES) -> dict:
    """Mean symptom severity per (allergen band × modifier band) cell.

    This is the supervisor's "combinations of allergens with temperature and
    humidity": a correlation coefficient collapses the whole relationship into
    one number, and cannot show that high pollen matters mainly when it is also
    hot. A 3×3 table of means can.

    Args:
        pairs: Output of `pair_observations`.
        variable_key: The allergen variable, a key of `ENV_VARIABLES`.
        modifier_key: The conditioning variable, usually temperature or
            humidity.
        symptom: A key of `SYMPTOM_KEYS`.
        min_cell_samples: Cells with fewer observations report their count but
            no mean.

    Returns:
        A dict with both variables' identities, the tercile cut points used,
        and `cells`: one entry per band combination with `n` and
        `mean_severity`. `available` is False when the data cannot be banded at
        all, with `reason` saying which side failed.
    """
    variable_meta = ENV_VARIABLES[variable_key]
    modifier_meta = ENV_VARIABLES[modifier_key]

    rows = []
    for pair in pairs:
        env_value = get_path(pair["snapshot"], variable_meta["path"])
        mod_value = get_path(pair["snapshot"], modifier_meta["path"])
        score = symptom_value(pair["report"], symptom)
        if env_value is None or mod_value is None or score is None:
            continue
        try:
            rows.append((float(env_value), float(mod_value), score))
        except (TypeError, ValueError):
            continue

    header = {
        "variable": variable_key,
        "variable_label": variable_meta["label"],
        "variable_unit": variable_meta["unit"],
        "modifier": modifier_key,
        "modifier_label": modifier_meta["label"],
        "modifier_unit": modifier_meta["unit"],
        "symptom": symptom,
        "n": len(rows),
        "min_cell_samples": min_cell_samples,
    }

    variable_cuts = tercile_bands([row[0] for row in rows])
    modifier_cuts = tercile_bands([row[1] for row in rows])
    if variable_cuts is None or modifier_cuts is None:
        which = variable_key if variable_cuts is None else modifier_key
        return {
            **header,
            "available": False,
            "reason": (
                f"Not enough spread in '{which}' to split it into bands "
                "(too few observations, or the value barely varied)."
            ),
            "cells": [],
        }

    buckets = {}
    for env_value, mod_value, score in rows:
        cell = (band_of(env_value, variable_cuts), band_of(mod_value, modifier_cuts))
        buckets.setdefault(cell, []).append(score)

    cells = []
    for variable_band in BAND_ORDER:
        for modifier_band in BAND_ORDER:
            scores = buckets.get((variable_band, modifier_band), [])
            enough = len(scores) >= min_cell_samples
            cells.append({
                "variable_band": variable_band,
                "modifier_band": modifier_band,
                "n": len(scores),
                "mean_severity": round(mean(scores), 2) if enough else None,
            })

    return {
        **header,
        "available": True,
        "variable_cuts": [round(variable_cuts[0], 3), round(variable_cuts[1], 3)],
        "modifier_cuts": [round(modifier_cuts[0], 3), round(modifier_cuts[1], 3)],
        "cells": cells,
    }


def variables_for_profile(profile: dict) -> list:
    """Return the environmental variables a participant's profile points at.

    Args:
        profile: An allergy_profiles document, or None.

    Returns:
        `ENV_VARIABLES` keys for the allergens declared, always followed by
        temperature and humidity -- the modifiers the analysis conditions on,
        which are relevant regardless of what someone reacts to. Order is
        preserved and duplicates removed.
    """
    keys = []
    for allergen in (profile or {}).get("allergens", {}):
        for variable in ALLERGEN_VARIABLES.get(allergen, []):
            if variable not in keys:
                keys.append(variable)
    for modifier in MODIFIER_VARIABLES:
        if modifier not in keys:
            keys.append(modifier)
    return keys

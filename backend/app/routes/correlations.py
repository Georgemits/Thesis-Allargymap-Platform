"""Correlation routes: reported symptoms against measured environment.

Routes:
  GET /api/correlations               -- every variable against one symptom
  GET /api/correlations/lags          -- the same analysis at several exposure lags
  GET /api/correlations/combinations  -- allergen band × temperature/humidity band
  GET /api/correlations/me            -- restricted to this participant's allergens

The analysis itself lives in `app.services.correlation`, which knows nothing
about Flask or MongoDB. These routes only fetch documents, pass them in, and
serialise what comes back -- so the thesis' central claim can be tested without
a database, and the same functions can later be called by the alerting job.

Everything here is a read. Results are computed per request rather than cached:
the dataset is tens of thousands of snapshots and a query takes well under a
second, and a stale cached correlation would be far more expensive to notice.
"""

from datetime import datetime, timedelta, timezone

from flask import Blueprint, g, jsonify, request

from ..extensions import mongo
from ..models.allergy_profile import COLLECTION_NAME as PROFILE_COLLECTION
from ..services.correlation import (
    DEFAULT_MATCH_WINDOW_HOURS,
    ENV_VARIABLES,
    LAG_CANDIDATES,
    MODIFIER_VARIABLES,
    SYMPTOM_KEYS,
    combination_table,
    correlate_all,
    pair_observations,
    scan_lags,
    variables_for_profile,
)
from ..utils.identity import require_device_id
from ..utils.validation import parse_positive_int

bp = Blueprint("correlations", __name__)

#: Default analysis window. The environmental dataset starts in late May 2026,
#: so ninety days covers essentially all of it without asking for everything.
DEFAULT_WINDOW_DAYS = 90
MAX_WINDOW_DAYS = 400

#: An exposure lag beyond three days stops being a lag and starts being a
#: different question ("does last week's pollen matter?"), which this analysis
#: is not set up to answer honestly.
MAX_LAG_HOURS = 72

#: Only the fields the engine reads, so a 90-day query does not haul the whole
#: pollen_upi subdocument out of MongoDB for nothing.
SNAPSHOT_PROJECTION = {
    "_id": 0, "city": 1, "timestamp": 1,
    "pollen": 1, "air_quality": 1, "weather": 1,
}
REPORT_PROJECTION = {
    "_id": 0, "user_id": 1, "timestamp": 1, "city": 1,
    "location": 1, "symptoms": 1, "overall_severity": 1,
}


def _parse_symptom(args) -> str:
    """Validate the ?symptom= parameter."""
    symptom = args.get("symptom", "overall_severity")
    if symptom not in SYMPTOM_KEYS:
        raise ValueError(f"symptom must be one of: {', '.join(SYMPTOM_KEYS)}.")
    return symptom


def _parse_variable(args, name: str, default=None):
    """Validate a parameter naming an environmental variable."""
    value = args.get(name, default)
    if value is None:
        raise ValueError(f"{name} is required.")
    if value not in ENV_VARIABLES:
        raise ValueError(f"{name} must be one of: {', '.join(sorted(ENV_VARIABLES))}.")
    return value


def _parse_lag(args) -> float:
    """Validate the ?lag_hours= parameter."""
    raw = args.get("lag_hours")
    if raw is None or raw == "":
        return 0.0
    try:
        lag = float(raw)
    except (TypeError, ValueError):
        raise ValueError("lag_hours must be a number.")
    if lag < 0 or lag > MAX_LAG_HOURS:
        raise ValueError(f"lag_hours must be between 0 and {MAX_LAG_HOURS}.")
    return lag


def _load(days: int, lag_hours: float, city: str = None, user_id: str = None):
    """Fetch the reports and snapshots one analysis needs.

    Snapshots are fetched from further back than the reports by the lag plus
    the matching window, so a report at the very start of the period can still
    find the earlier conditions it is supposed to be matched against.

    Args:
        days: Length of the analysis window.
        lag_hours: Exposure lag the analysis will apply.
        city: Restrict reports to one city, or None for all.
        user_id: Restrict reports to one participant, or None for all.

    Returns:
        ``(reports, snapshots)``.
    """
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    snapshot_since = since - timedelta(hours=lag_hours + DEFAULT_MATCH_WINDOW_HOURS)

    report_query = {"timestamp": {"$gte": since}}
    if city:
        report_query["city"] = city
    if user_id:
        report_query["user_id"] = user_id

    reports = list(mongo.db.reports.find(report_query, REPORT_PROJECTION))
    snapshots = list(
        mongo.db.env_snapshots.find(
            {"timestamp": {"$gte": snapshot_since}}, SNAPSHOT_PROJECTION
        )
    )
    return reports, snapshots


@bp.get("/")
@bp.get("")   # both /api/correlations and /api/correlations/ , so a client
              # never eats a 308 redirect (and a preflight for it) on the
              # blueprint's own root.
def correlations():
    """Correlate every environmental variable against one symptom.

    Query params:
        symptom (str): One of `SYMPTOM_KEYS`; default `overall_severity`.
        days (int): Analysis window, default 90.
        city (str): Restrict to reports from one city.
        lag_hours (float): Match against conditions this many hours earlier.

    Returns:
        200 with the `correlate_all` summary plus the parameters it ran under.
        Results carrying `insufficient_data` are the normal answer early in a
        deployment, not an error.
    """
    try:
        symptom = _parse_symptom(request.args)
        lag_hours = _parse_lag(request.args)
        days = parse_positive_int(request.args, "days", DEFAULT_WINDOW_DAYS, MAX_WINDOW_DAYS)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    city = request.args.get("city") or None
    reports, snapshots = _load(days, lag_hours, city=city)
    pairs = pair_observations(reports, snapshots, lag_hours=lag_hours)

    summary = correlate_all(pairs, symptom)
    summary.update({
        "days": days,
        "city": city,
        "lag_hours": lag_hours,
        "reports_considered": len(reports),
        "match_window_hours": DEFAULT_MATCH_WINDOW_HOURS,
    })
    return jsonify(summary), 200


@bp.get("/lags")
def lag_scan():
    """Run the analysis at several exposure lags and return all of them.

    Symptoms follow exposure rather than accompanying it, so the lag at which a
    relationship is strongest is a finding in itself. Comparing lags is also
    the honest way to present one: reporting only the best lag, chosen after
    the fact, would be selecting the result.

    Query params:
        symptom (str), days (int), city (str), variable (str, optional).

    Returns:
        200 with `scans`, one summary per lag in `LAG_CANDIDATES`.
    """
    try:
        symptom = _parse_symptom(request.args)
        days = parse_positive_int(request.args, "days", DEFAULT_WINDOW_DAYS, MAX_WINDOW_DAYS)
        variable = None
        if request.args.get("variable"):
            variable = _parse_variable(request.args, "variable")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    city = request.args.get("city") or None
    reports, snapshots = _load(days, max(LAG_CANDIDATES), city=city)

    return jsonify({
        "symptom": symptom,
        "variable": variable,
        "days": days,
        "city": city,
        "lags_tested": list(LAG_CANDIDATES),
        "scans": scan_lags(reports, snapshots, symptom, variable),
    }), 200


@bp.get("/combinations")
def combinations():
    """Mean symptom severity per allergen band × modifier band.

    Query params:
        variable (str): The allergen variable. Required.
        modifier (str): Temperature or humidity; default `temperature`.
        symptom (str), days (int), city (str), lag_hours (float).

    Returns:
        200 with the `combination_table` result, or 400 on a bad parameter.
    """
    try:
        variable = _parse_variable(request.args, "variable")
        modifier = _parse_variable(request.args, "modifier", MODIFIER_VARIABLES[0])
        symptom = _parse_symptom(request.args)
        lag_hours = _parse_lag(request.args)
        days = parse_positive_int(request.args, "days", DEFAULT_WINDOW_DAYS, MAX_WINDOW_DAYS)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    city = request.args.get("city") or None
    reports, snapshots = _load(days, lag_hours, city=city)
    pairs = pair_observations(reports, snapshots, lag_hours=lag_hours)

    table = combination_table(pairs, variable, modifier, symptom)
    table.update({"days": days, "city": city, "lag_hours": lag_hours})
    return jsonify(table), 200


@bp.get("/me")
@require_device_id
def my_correlations():
    """The analysis restricted to the allergens this participant declared.

    Returns two views, and the difference between them matters:

    * `personal` -- computed from this participant's own reports only. It is
      what the thesis means by personalised, and it is empty until they have
      reported enough times.
    * `population` -- the same variables across everyone's reports. It is what
      the risk score falls back on for a participant who has just signed up,
      and it never becomes meaningless the way a two-report personal analysis
      would.

    Query params:
        symptom (str), days (int), lag_hours (float).

    Returns:
        200 with both views and the profile they were derived from.
    """
    try:
        symptom = _parse_symptom(request.args)
        lag_hours = _parse_lag(request.args)
        days = parse_positive_int(request.args, "days", DEFAULT_WINDOW_DAYS, MAX_WINDOW_DAYS)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    profile = mongo.db[PROFILE_COLLECTION].find_one({"device_id": g.device_id})
    variables = variables_for_profile(profile)

    reports, snapshots = _load(days, lag_hours)
    mine = [r for r in reports if r.get("user_id") == g.device_id]

    personal = correlate_all(
        pair_observations(mine, snapshots, lag_hours=lag_hours), symptom, variables
    )
    population = correlate_all(
        pair_observations(reports, snapshots, lag_hours=lag_hours), symptom, variables
    )

    return jsonify({
        "allergens": sorted((profile or {}).get("allergens", {})),
        "variables": variables,
        "symptom": symptom,
        "days": days,
        "lag_hours": lag_hours,
        "my_reports": len(mine),
        "personal": personal,
        "population": population,
    }), 200

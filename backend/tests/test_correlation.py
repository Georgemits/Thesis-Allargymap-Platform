"""Unit tests for app.services.correlation (pure, no Mongo).

The documents built here are fixtures shaped like real ones, not data: they
exist to prove the joining, banding and guard rails behave, never to produce a
finding.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.correlation import (  # noqa: E402
    ALLERGEN_VARIABLES,
    ENV_VARIABLES,
    SYMPTOM_KEYS,
    band_of,
    combination_table,
    correlate_all,
    correlate_variable,
    get_path,
    index_snapshots,
    nearest_snapshot,
    pair_observations,
    paired_series,
    report_city,
    symptom_value,
    tercile_bands,
    variables_for_profile,
)
from app.models.allergy_profile import ALLERGENS  # noqa: E402

ATHENS = (23.7275, 37.9838)  # GeoJSON order: lon, lat
T0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def snapshot(hours, olive=10.0, temp=25.0, humidity=50.0, city="Athens"):
    """An env_snapshots-shaped fixture `hours` after T0."""
    return {
        "city": city,
        "timestamp": T0 + timedelta(hours=hours),
        "pollen": {"olive_pollen": olive, "grass_pollen": 0.0},
        "air_quality": {"dust": 5.0, "pm10": 20.0, "pm2_5": 8.0},
        "weather": {"temperature_2m": temp, "relative_humidity_2m": humidity},
    }


def report(hours, severity=5, lon=ATHENS[0], lat=ATHENS[1], user="device-a"):
    """A reports-shaped fixture `hours` after T0."""
    return {
        "user_id": user,
        "timestamp": T0 + timedelta(hours=hours),
        "city": "",  # deliberately blank: the coordinates are the reliable field
        "location": {"type": "Point", "coordinates": [lon, lat]},
        "symptoms": {"sneezing": severity, "runny_nose": severity},
        "overall_severity": severity,
    }


class TestCatalogueConsistency(unittest.TestCase):
    def test_every_declarable_allergen_is_analysed(self):
        # The guard that keeps the profile form and the engine in step: an
        # allergen someone can tick but the engine ignores would silently never
        # produce a result for them.
        self.assertEqual(set(ALLERGEN_VARIABLES), set(ALLERGENS))
        for allergen, variables in ALLERGEN_VARIABLES.items():
            self.assertTrue(variables, allergen)
            for variable in variables:
                self.assertIn(variable, ENV_VARIABLES)

    def test_symptoms_include_the_computed_overall(self):
        self.assertIn("overall_severity", SYMPTOM_KEYS)
        self.assertIn("sneezing", SYMPTOM_KEYS)


class TestGetPath(unittest.TestCase):
    def test_reads_a_nested_value(self):
        self.assertEqual(get_path(snapshot(0), "pollen.olive_pollen"), 10.0)

    def test_missing_path_is_none(self):
        self.assertIsNone(get_path(snapshot(0), "pollen.ragweed_pollen"))
        self.assertIsNone(get_path(snapshot(0), "nope.at.all"))

    def test_null_value_stays_none(self):
        # The collector stores None where a provider had no data; such an
        # observation must be dropped, never counted as a zero reading.
        doc = {"pollen": {"olive_pollen": None}}
        self.assertIsNone(get_path(doc, "pollen.olive_pollen"))


class TestReportCity(unittest.TestCase):
    def test_derived_from_coordinates_not_the_typed_field(self):
        doc = report(0)
        doc["city"] = "Athnes"  # a plausible typo
        self.assertEqual(report_city(doc), "Athens")

    def test_falls_back_to_the_typed_city_without_coordinates(self):
        doc = report(0)
        doc["location"] = {}
        doc["city"] = "Patras"
        self.assertEqual(report_city(doc), "Patras")


class TestMatching(unittest.TestCase):
    def setUp(self):
        self.snapshots = [snapshot(h) for h in range(0, 24)]
        self.index = index_snapshots(self.snapshots)

    def test_picks_the_closest_snapshot_in_time(self):
        found, gap = nearest_snapshot(self.index, "Athens", T0 + timedelta(hours=5, minutes=10), 3)
        self.assertEqual(found["timestamp"], T0 + timedelta(hours=5))
        self.assertAlmostEqual(gap, 10 / 60, places=6)

    def test_nothing_inside_the_window_is_no_match(self):
        found, _ = nearest_snapshot(self.index, "Athens", T0 + timedelta(days=9), 3)
        self.assertIsNone(found)

    def test_unknown_city_is_no_match(self):
        self.assertEqual(nearest_snapshot(self.index, "Rhodes", T0, 3), (None, None))

    def test_naive_timestamps_are_treated_as_utc(self):
        # pymongo returns naive datetimes; reading them as local time would
        # shift every match by the Greek offset.
        naive = [dict(s, timestamp=s["timestamp"].replace(tzinfo=None)) for s in self.snapshots]
        pairs = pair_observations([report(3)], naive)
        self.assertEqual(len(pairs), 1)
        self.assertLess(pairs[0]["gap_hours"], 0.01)


class TestPairing(unittest.TestCase):
    def test_lag_matches_earlier_conditions(self):
        snapshots = [snapshot(h, olive=float(h)) for h in range(0, 24)]
        pairs = pair_observations([report(10)], snapshots, lag_hours=6)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(get_path(pairs[0]["snapshot"], "pollen.olive_pollen"), 4.0)

    def test_reports_without_a_nearby_snapshot_are_dropped(self):
        pairs = pair_observations([report(0), report(500)], [snapshot(0)])
        self.assertEqual(len(pairs), 1)

    def test_reports_without_a_timestamp_are_dropped(self):
        broken = report(0)
        del broken["timestamp"]
        self.assertEqual(pair_observations([broken], [snapshot(0)]), [])

    def test_series_stay_aligned_when_a_value_is_missing(self):
        snapshots = [snapshot(0), dict(snapshot(1), pollen={"olive_pollen": None})]
        pairs = pair_observations([report(0), report(1)], snapshots, window_hours=0.5)
        env, sym = paired_series(pairs, "olive_pollen", "overall_severity")
        self.assertEqual(len(env), len(sym))
        self.assertEqual(len(env), 1)


class TestCorrelate(unittest.TestCase):
    def _linear_pairs(self, count=40):
        snapshots = [snapshot(h, olive=float(h)) for h in range(count)]
        reports = [report(h, severity=min(10, h // 4)) for h in range(count)]
        return pair_observations(reports, snapshots, window_hours=0.5)

    def test_reports_a_strong_relationship_when_there_is_one(self):
        result = correlate_variable(self._linear_pairs(), "olive_pollen", "overall_severity")
        self.assertFalse(result["insufficient_data"])
        self.assertGreater(result["spearman"], 0.9)
        self.assertLess(result["p_value"], 0.001)
        self.assertEqual(result["strength"], "strong")

    def test_refuses_to_report_a_coefficient_from_too_few_pairs(self):
        # The guard that matters most: "r = 0.87 (n = 4)" reads as a finding.
        result = correlate_variable(self._linear_pairs(count=6), "olive_pollen", "overall_severity")
        self.assertTrue(result["insufficient_data"])
        self.assertIsNone(result["pearson"])
        self.assertIsNone(result["p_value"])
        self.assertEqual(result["n"], 6)

    def test_constant_variable_yields_no_coefficient(self):
        result = correlate_variable(self._linear_pairs(), "grass_pollen", "overall_severity")
        self.assertIsNone(result["spearman"])

    def test_summary_counts_only_the_powered_tests(self):
        summary = correlate_all(self._linear_pairs(), "overall_severity")
        self.assertEqual(summary["pairs"], 40)
        self.assertEqual(
            summary["tests_run"],
            sum(1 for r in summary["results"] if not r["insufficient_data"]),
        )

    def test_strongest_result_comes_first(self):
        summary = correlate_all(self._linear_pairs(), "overall_severity")
        self.assertEqual(summary["results"][0]["variable"], "olive_pollen")
        self.assertTrue(summary["results"][-1]["insufficient_data"]
                        or abs(summary["results"][-1]["spearman"] or 0)
                        <= abs(summary["results"][0]["spearman"]))


class TestBanding(unittest.TestCase):
    def test_terciles_split_the_observed_range(self):
        cuts = tercile_bands([float(v) for v in range(9)])
        self.assertIsNotNone(cuts)
        self.assertLess(cuts[0], cuts[1])
        self.assertEqual(band_of(0.0, cuts), "low")
        self.assertEqual(band_of(8.0, cuts), "high")

    def test_a_flat_variable_cannot_be_banded(self):
        # An out-of-season pollen is zero for months; three bands would be a
        # fiction.
        self.assertIsNone(tercile_bands([0.0] * 50))

    def test_too_few_values_cannot_be_banded(self):
        self.assertIsNone(tercile_bands([1.0, 2.0]))


class TestCombinationTable(unittest.TestCase):
    def _pairs(self):
        snapshots = []
        reports = []
        for h in range(60):
            olive = float(h % 10)
            temp = 15.0 + (h % 6) * 3
            snapshots.append(snapshot(h, olive=olive, temp=temp))
            # Severity rises with pollen, and more so when it is hot.
            reports.append(report(h, severity=min(10, int(olive * (1 + (temp > 24))))))
        return pair_observations(reports, snapshots, window_hours=0.5)

    def test_produces_a_full_three_by_three_grid(self):
        table = combination_table(self._pairs(), "olive_pollen", "temperature")
        self.assertTrue(table["available"])
        self.assertEqual(len(table["cells"]), 9)
        self.assertEqual(len(table["variable_cuts"]), 2)

    def test_small_cells_report_their_count_but_no_mean(self):
        table = combination_table(self._pairs(), "olive_pollen", "temperature",
                                  min_cell_samples=1000)
        self.assertTrue(all(cell["mean_severity"] is None for cell in table["cells"]))
        self.assertTrue(any(cell["n"] > 0 for cell in table["cells"]))

    def test_unbandable_variable_says_so_instead_of_failing(self):
        table = combination_table(self._pairs(), "grass_pollen", "temperature")
        self.assertFalse(table["available"])
        self.assertIn("grass_pollen", table["reason"])
        self.assertEqual(table["cells"], [])


class TestVariablesForProfile(unittest.TestCase):
    def test_expands_allergens_and_always_adds_the_modifiers(self):
        variables = variables_for_profile({"allergens": {"saharan_dust": 2}})
        self.assertEqual(variables, ["dust", "temperature", "humidity"])

    def test_particulates_expand_to_both_measurements(self):
        variables = variables_for_profile({"allergens": {"particulates": 1}})
        self.assertEqual(variables[:2], ["pm10", "pm2_5"])

    def test_no_profile_still_yields_the_modifiers(self):
        self.assertEqual(variables_for_profile(None), ["temperature", "humidity"])


class TestSymptomValue(unittest.TestCase):
    def test_reads_a_named_symptom_and_the_overall(self):
        doc = report(0, severity=7)
        self.assertEqual(symptom_value(doc, "sneezing"), 7.0)
        self.assertEqual(symptom_value(doc, "overall_severity"), 7.0)

    def test_missing_symptom_is_none(self):
        self.assertIsNone(symptom_value(report(0), "wheezing"))


if __name__ == "__main__":
    unittest.main()

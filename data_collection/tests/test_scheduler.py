"""Unit tests for the collection scheduler.

Every test is offline: no HTTP call, no MongoDB connection, no API key. The
network-facing pieces (`fetch_locations`, `import_dataframe`) are patched.
"""

import json
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

import scheduler  # noqa: E402

ATHENS = ZoneInfo("Europe/Athens")


def _base_config(**overrides) -> dict:
    """Return a valid configuration, with `overrides` applied on top."""
    config = json.loads(json.dumps(scheduler.DEFAULT_CONFIG))
    config.update(overrides)
    return config


class DeepMergeTests(unittest.TestCase):
    def test_merges_nested_dicts_without_dropping_siblings(self):
        base = {"a": 1, "pass": {"enabled": True, "days": 3}}
        merged = scheduler._deep_merge(base, {"pass": {"days": 7}})
        self.assertEqual(merged["pass"], {"enabled": True, "days": 7})

    def test_does_not_mutate_inputs(self):
        base = {"pass": {"days": 3}}
        scheduler._deep_merge(base, {"pass": {"days": 7}})
        self.assertEqual(base["pass"]["days"], 3)


class ClockTimeTests(unittest.TestCase):
    def test_parses_valid_times(self):
        self.assertEqual(scheduler._parse_clock_time("06:00"), (6, 0))
        self.assertEqual(scheduler._parse_clock_time("23:59"), (23, 59))

    def test_rejects_malformed_and_out_of_range(self):
        for bad in ("6", "06-00", "24:00", "06:60", "", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    scheduler._parse_clock_time(bad)


class NextRunTimeTests(unittest.TestCase):
    def test_picks_the_next_time_later_today(self):
        now = datetime(2026, 8, 25, 7, 30, tzinfo=ATHENS)
        nxt = scheduler.next_run_time(now, ["06:00", "18:00"])
        self.assertEqual(nxt, datetime(2026, 8, 25, 18, 0, tzinfo=ATHENS))

    def test_rolls_over_to_tomorrow_once_all_times_have_passed(self):
        now = datetime(2026, 8, 25, 19, 0, tzinfo=ATHENS)
        nxt = scheduler.next_run_time(now, ["06:00", "18:00"])
        self.assertEqual(nxt, datetime(2026, 8, 26, 6, 0, tzinfo=ATHENS))

    def test_a_time_equal_to_now_is_scheduled_for_tomorrow_not_re_run(self):
        now = datetime(2026, 8, 25, 6, 0, tzinfo=ATHENS)
        nxt = scheduler.next_run_time(now, ["06:00"])
        self.assertEqual(nxt, datetime(2026, 8, 26, 6, 0, tzinfo=ATHENS))


class ValidateConfigTests(unittest.TestCase):
    def test_accepts_the_shipped_defaults(self):
        scheduler.validate_config(_base_config())

    def test_rejects_unknown_timezone(self):
        with self.assertRaises(ValueError):
            scheduler.validate_config(_base_config(timezone="Mars/Olympus"))

    def test_rejects_empty_schedule(self):
        with self.assertRaises(ValueError):
            scheduler.validate_config(_base_config(run_at=[]))

    def test_rejects_unknown_city(self):
        with self.assertRaises(ValueError) as ctx:
            scheduler.validate_config(_base_config(cities=["Athens", "Atlantis"]))
        self.assertIn("Atlantis", str(ctx.exception))

    def test_rejects_reanalysis_window_beyond_open_meteo_limit(self):
        config = _base_config()
        config["reanalysis_pass"]["days"] = 120
        with self.assertRaises(ValueError):
            scheduler.validate_config(config)

    def test_rejects_unknown_pollen_source(self):
        config = _base_config()
        config["forecast_pass"]["pollen_source"] = "guesswork"
        with self.assertRaises(ValueError):
            scheduler.validate_config(config)


class LoadConfigTests(unittest.TestCase):
    def test_missing_file_falls_back_to_defaults(self):
        config = scheduler.load_config(Path("/nonexistent/collector_config.json"))
        self.assertEqual(config["timezone"], scheduler.DEFAULT_CONFIG["timezone"])

    def test_partial_file_keeps_unspecified_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text(json.dumps({"run_at": ["09:00"]}))
            config = scheduler.load_config(path)
        self.assertEqual(config["run_at"], ["09:00"])
        self.assertEqual(config["reanalysis_pass"]["days"],
                         scheduler.DEFAULT_CONFIG["reanalysis_pass"]["days"])

    def test_invalid_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text("{nope}")
            with self.assertRaises(ValueError):
                scheduler.load_config(path)


class ResolveLocationsTests(unittest.TestCase):
    def test_null_cities_means_every_city(self):
        self.assertEqual(len(scheduler.resolve_locations(_base_config(cities=None))),
                         len(scheduler.GREEK_LOCATIONS))

    def test_explicit_subset_is_honoured(self):
        locations = scheduler.resolve_locations(_base_config(cities=["Athens", "Patras"]))
        self.assertEqual(sorted(locations), ["Athens", "Patras"])


class PruneOutputTests(unittest.TestCase):
    def test_removes_only_files_older_than_the_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old, fresh = out / "old_combined_1.csv", out / "fresh_combined_2.csv"
            old.write_text("x")
            fresh.write_text("x")
            stale = time.time() - 30 * 86400
            import os
            os.utime(old, (stale, stale))

            removed = scheduler.prune_output(out, retention_days=14)

        self.assertEqual(removed, 1)

    def test_zero_retention_disables_pruning(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "a_combined_1.csv").write_text("x")
            self.assertEqual(scheduler.prune_output(out, retention_days=0), 0)
            self.assertEqual(len(list(out.glob("*.csv"))), 1)


class RedactUriTests(unittest.TestCase):
    def test_credentials_are_stripped(self):
        redacted = scheduler._redact_uri("mongodb://user:pw@cluster.example/allergymap")
        self.assertNotIn("pw", redacted)
        self.assertIn("cluster.example", redacted)

    def test_uri_without_credentials_is_unchanged(self):
        uri = "mongodb://mongo:27017/allergymap"
        self.assertEqual(scheduler._redact_uri(uri), uri)


class RunPassTests(unittest.TestCase):
    """`run_pass` is the seam between fetching and persistence — patch both."""

    def setUp(self):
        self.frame = pd.DataFrame({"city": ["Athens"], "datetime": ["2026-08-25T00:00"]})

    def test_dry_run_fetches_but_never_writes(self):
        with mock.patch.object(scheduler, "fetch_locations", return_value=self.frame), \
             mock.patch.object(scheduler, "import_dataframe") as importer:
            affected = scheduler.run_pass(
                label="t", locations={"Athens": {"latitude": 1.0, "longitude": 2.0}},
                mode="forecast", pollen_source="google", output_dir=Path("/tmp"),
                mongo_uri="mongodb://x/y", dry_run=True,
            )
        importer.assert_not_called()
        self.assertEqual(affected, 0)

    def test_empty_fetch_skips_the_import(self):
        with mock.patch.object(scheduler, "fetch_locations", return_value=pd.DataFrame()), \
             mock.patch.object(scheduler, "import_dataframe") as importer:
            affected = scheduler.run_pass(
                label="t", locations={}, mode="forecast", pollen_source="google",
                output_dir=Path("/tmp"), mongo_uri="mongodb://x/y",
            )
        importer.assert_not_called()
        self.assertEqual(affected, 0)

    def test_successful_pass_returns_the_importer_count(self):
        with mock.patch.object(scheduler, "fetch_locations", return_value=self.frame), \
             mock.patch.object(scheduler, "import_dataframe", return_value=42):
            affected = scheduler.run_pass(
                label="t", locations={"Athens": {"latitude": 1.0, "longitude": 2.0}},
                mode="forecast", pollen_source="google", output_dir=Path("/tmp"),
                mongo_uri="mongodb://x/y",
            )
        self.assertEqual(affected, 42)


class FetchLocationsTests(unittest.TestCase):
    def test_one_failing_city_does_not_abort_the_others(self):
        good = pd.DataFrame({"city": ["Patras"]})

        def fake(name, lat, lon, mode, days, output_dir):
            if name == "Athens":
                raise RuntimeError("API down")
            return good

        with mock.patch.object(scheduler, "run_for_location", side_effect=fake):
            frame = scheduler.fetch_locations(
                {"Athens": {"latitude": 1.0, "longitude": 2.0},
                 "Patras": {"latitude": 3.0, "longitude": 4.0}},
                mode="forecast", output_dir=Path("/tmp"),
            )

        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0]["city"], "Patras")


class RunCycleTests(unittest.TestCase):
    def test_both_passes_run_and_their_counts_are_summed(self):
        config = _base_config()
        with mock.patch.object(scheduler, "run_pass", side_effect=[10, 5]) as run_pass, \
             mock.patch.object(scheduler, "prune_output", return_value=0):
            total = scheduler.run_cycle(config, "mongodb://x/y")
        self.assertEqual(total, 15)
        self.assertEqual([c.kwargs["label"] for c in run_pass.call_args_list],
                         ["forecast", "reanalysis"])

    def test_disabled_reanalysis_pass_is_skipped(self):
        config = _base_config()
        config["reanalysis_pass"]["enabled"] = False
        with mock.patch.object(scheduler, "run_pass", return_value=7) as run_pass, \
             mock.patch.object(scheduler, "prune_output", return_value=0):
            total = scheduler.run_cycle(config, "mongodb://x/y")
        self.assertEqual(total, 7)
        self.assertEqual(run_pass.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

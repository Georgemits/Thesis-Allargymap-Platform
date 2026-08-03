"""Unit tests for pollen_source.py -- the normalizer + fallback logic.

All HTTP calls are mocked; no network access or real API key is required.
Run with:  python -m unittest discover -s data_collection/tests
"""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pollen_source as ps  # noqa: E402


class TestNormalizeOpenMeteo(unittest.TestCase):
    def test_fills_missing_keys_with_none(self):
        row = {"grass_pollen": 12.5, "olive_pollen": 3.0}
        result = ps.normalize_open_meteo(row)
        self.assertEqual(result["grass_pollen"], 12.5)
        self.assertEqual(result["olive_pollen"], 3.0)
        self.assertIsNone(result["birch_pollen"])
        self.assertEqual(set(result.keys()), set(ps.OPEN_METEO_POLLEN_KEYS))

    def test_none_input_returns_all_none(self):
        result = ps.normalize_open_meteo(None)
        self.assertTrue(all(v is None for v in result.values()))


class TestNormalizeGoogleDay(unittest.TestCase):
    def test_extracts_overall_and_plants(self):
        day = {
            "date": "2026-08-03",
            "types": {"grass": {"value": 3, "category": "Moderate", "in_season": True}},
            "plants": {"olive": {"value": 4, "category": "High", "in_season": True}},
        }
        result = ps.normalize_google_day(day)
        self.assertEqual(result["overall"]["grass"]["value"], 3)
        self.assertEqual(result["plants"]["olive"]["category"], "High")


class TestBuildPollenFields(unittest.TestCase):
    def test_google_available_keeps_both_values(self):
        fields = ps.build_pollen_fields(
            {"olive_pollen": 5.5},
            {"date": "2026-08-03", "types": {}, "plants": {"olive": {"value": 4, "category": "High", "in_season": True}}},
        )
        self.assertEqual(fields["pollen_source"], "google")
        self.assertEqual(fields["pollen"]["olive_pollen"], 5.5)
        self.assertEqual(fields["pollen_upi"]["plants"]["olive"]["value"], 4)
        self.assertNotIn("pollen_source_fallback_reason", fields)

    def test_fallback_when_google_day_is_none(self):
        fields = ps.build_pollen_fields({"olive_pollen": 5.5}, None, fallback_reason="out of window")
        self.assertEqual(fields["pollen_source"], "open_meteo")
        self.assertIsNone(fields["pollen_upi"])
        self.assertEqual(fields["pollen_source_fallback_reason"], "out of window")

    def test_fallback_without_reason_omits_field(self):
        fields = ps.build_pollen_fields({}, None)
        self.assertNotIn("pollen_source_fallback_reason", fields)


class TestEnrichDocsWithPollenSource(unittest.TestCase):
    def _doc(self, city, day, hour=12):
        return {
            "city": city,
            "latitude": 37.98,
            "longitude": 23.73,
            "timestamp": datetime(2026, 8, day, hour, tzinfo=timezone.utc),
            "pollen": {"olive_pollen": 5.5},
        }

    def test_open_meteo_source_skips_google_entirely(self):
        docs = [self._doc("Athens", 3)]
        with patch("pollen_source.fetch_forecast") as mock_fetch:
            result = ps.enrich_docs_with_pollen_source(docs, source="open_meteo")
        mock_fetch.assert_not_called()
        self.assertEqual(result[0]["pollen_source"], "open_meteo")
        self.assertIsNone(result[0]["pollen_upi"])

    def test_missing_api_key_falls_back(self):
        docs = [self._doc("Athens", 3)]
        with patch("pollen_source.load_api_key", return_value=""):
            result = ps.enrich_docs_with_pollen_source(docs, source="google")
        self.assertEqual(result[0]["pollen_source"], "open_meteo")
        self.assertIn("GOOGLE_POLLEN_API_KEY", result[0]["pollen_source_fallback_reason"])

    def test_date_within_window_uses_google(self):
        docs = [self._doc("Athens", 3)]
        raw = {
            "dailyInfo": [
                {
                    "date": {"year": 2026, "month": 8, "day": 3},
                    "pollenTypeInfo": [{"code": "GRASS", "inSeason": True, "indexInfo": {"value": 2, "category": "Low"}}],
                    "plantInfo": [{"code": "OLIVE", "inSeason": True, "indexInfo": {"value": 4, "category": "High"}}],
                }
            ]
        }
        with patch("pollen_source.load_api_key", return_value="fake-key"), \
             patch("pollen_source.fetch_forecast", return_value=raw) as mock_fetch:
            result = ps.enrich_docs_with_pollen_source(docs, source="google")
        mock_fetch.assert_called_once()
        self.assertEqual(result[0]["pollen_source"], "google")
        self.assertEqual(result[0]["pollen_upi"]["plants"]["olive"]["value"], 4)

    def test_date_outside_window_falls_back(self):
        docs = [self._doc("Athens", 20)]  # far outside the 5-day mock window
        raw = {
            "dailyInfo": [
                {
                    "date": {"year": 2026, "month": 8, "day": 3},
                    "pollenTypeInfo": [],
                    "plantInfo": [],
                }
            ]
        }
        with patch("pollen_source.load_api_key", return_value="fake-key"), \
             patch("pollen_source.fetch_forecast", return_value=raw):
            result = ps.enrich_docs_with_pollen_source(docs, source="google")
        self.assertEqual(result[0]["pollen_source"], "open_meteo")
        self.assertIn("outside Google's", result[0]["pollen_source_fallback_reason"])

    def test_google_http_error_falls_back_for_whole_city(self):
        docs = [self._doc("Athens", 3), self._doc("Athens", 4)]
        with patch("pollen_source.load_api_key", return_value="fake-key"), \
             patch("pollen_source.fetch_forecast", side_effect=ConnectionError("boom")):
            result = ps.enrich_docs_with_pollen_source(docs, source="google")
        self.assertTrue(all(d["pollen_source"] == "open_meteo" for d in result))
        self.assertTrue(all("Google Pollen API error" in d["pollen_source_fallback_reason"] for d in result))

    def test_unknown_source_raises(self):
        with self.assertRaises(ValueError):
            ps.enrich_docs_with_pollen_source([self._doc("Athens", 3)], source="bogus")


if __name__ == "__main__":
    unittest.main()

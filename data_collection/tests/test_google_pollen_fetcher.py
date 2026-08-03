"""Unit tests for google_pollen_fetcher.py. HTTP calls are mocked."""

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import google_pollen_fetcher as gpf  # noqa: E402

SAMPLE_RAW_RESPONSE = {
    "regionCode": "GR",
    "dailyInfo": [
        {
            "date": {"year": 2026, "month": 8, "day": 3},
            "pollenTypeInfo": [
                {"code": "GRASS", "displayName": "Grass", "inSeason": True,
                 "indexInfo": {"value": 3, "category": "Moderate"}},
                {"code": "TREE", "displayName": "Tree", "inSeason": False,
                 "indexInfo": {"value": 0, "category": "None"}},
            ],
            "plantInfo": [
                {"code": "OLIVE", "displayName": "Olive", "inSeason": True,
                 "indexInfo": {"value": 4, "category": "High"}},
                {"code": "OAK", "displayName": "Oak", "inSeason": False,
                 "indexInfo": {"value": 0, "category": "None"}},
            ],
        },
        {
            "date": {"year": 2026, "month": 8, "day": 4},
            "pollenTypeInfo": [],
            "plantInfo": [],
        },
    ],
}


class TestFetchForecast(unittest.TestCase):
    def test_raises_without_api_key(self):
        with self.assertRaises(ValueError):
            gpf.fetch_forecast(37.98, 23.73, api_key="")

    def test_clamps_days_to_max(self):
        mock_resp = Mock()
        mock_resp.json.return_value = SAMPLE_RAW_RESPONSE
        mock_resp.raise_for_status = Mock()
        with patch("google_pollen_fetcher.requests.get", return_value=mock_resp) as mock_get:
            gpf.fetch_forecast(37.98, 23.73, api_key="fake-key", days=99)
        called_params = mock_get.call_args.kwargs["params"]
        self.assertEqual(called_params["days"], gpf.MAX_FORECAST_DAYS)
        self.assertEqual(called_params["key"], "fake-key")

    def test_propagates_http_error(self):
        mock_resp = Mock()
        mock_resp.raise_for_status.side_effect = gpf.requests.HTTPError("403 Forbidden")
        with patch("google_pollen_fetcher.requests.get", return_value=mock_resp):
            with self.assertRaises(gpf.requests.HTTPError):
                gpf.fetch_forecast(37.98, 23.73, api_key="bad-key")


class TestParseDailyInfo(unittest.TestCase):
    def test_flattens_days_types_and_plants(self):
        days = gpf.parse_daily_info(SAMPLE_RAW_RESPONSE)
        self.assertEqual(len(days), 2)
        self.assertEqual(days[0]["date"], "2026-08-03")
        self.assertEqual(days[0]["types"]["grass"]["value"], 3)
        self.assertEqual(days[0]["types"]["grass"]["category"], "Moderate")
        # OLIVE maps onto our normalized "olive" key via PLANT_CODE_MAP
        self.assertEqual(days[0]["plants"]["olive"]["value"], 4)
        # OAK has no mapping -> kept under its own lowercase code, not dropped
        self.assertIn("oak", days[0]["plants"])
        self.assertEqual(days[0]["plants"]["oak"]["value"], 0)

    def test_empty_day_produces_empty_dicts(self):
        days = gpf.parse_daily_info(SAMPLE_RAW_RESPONSE)
        self.assertEqual(days[1]["types"], {})
        self.assertEqual(days[1]["plants"], {})

    def test_no_daily_info_returns_empty_list(self):
        self.assertEqual(gpf.parse_daily_info({}), [])


class TestBuildDataframe(unittest.TestCase):
    def test_one_row_per_day_with_flattened_columns(self):
        days = gpf.parse_daily_info(SAMPLE_RAW_RESPONSE)
        df = gpf.build_dataframe(days, "Athens", 37.98, 23.73)
        self.assertEqual(len(df), 2)
        self.assertIn("upi_type_grass", df.columns)
        self.assertIn("upi_olive", df.columns)
        self.assertIn("upi_olive_category", df.columns)
        self.assertEqual(df.iloc[0]["upi_olive"], 4)
        self.assertEqual(df.iloc[0]["location"], "Athens")


class TestLoadApiKey(unittest.TestCase):
    def test_reads_from_environment(self):
        with patch.dict("os.environ", {"GOOGLE_POLLEN_API_KEY": "env-key"}, clear=False):
            with patch("google_pollen_fetcher.load_dotenv"):
                self.assertEqual(gpf.load_api_key(), "env-key")

    def test_returns_empty_string_when_unset(self):
        env = {k: v for k, v in __import__("os").environ.items() if k != "GOOGLE_POLLEN_API_KEY"}
        with patch.dict("os.environ", env, clear=True):
            with patch("google_pollen_fetcher.load_dotenv"):
                self.assertEqual(gpf.load_api_key(), "")


if __name__ == "__main__":
    unittest.main()

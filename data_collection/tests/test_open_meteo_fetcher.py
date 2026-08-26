"""Unit tests for open_meteo_fetcher.py. HTTP calls are mocked."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import open_meteo_fetcher as omf  # noqa: E402

SAMPLE_WEATHER = {
    "latitude": 37.98, "longitude": 23.73,
    "hourly": {
        "time": ["2026-08-03T00:00", "2026-08-03T01:00"],
        "temperature_2m": [28.1, 27.5],
        "relative_humidity_2m": [55, 58],
    },
}

SAMPLE_AQI = {
    "latitude": 37.98, "longitude": 23.73,
    "hourly": {
        "time": ["2026-08-03T00:00", "2026-08-03T01:00"],
        "olive_pollen": [12.0, 10.5],
        "dust": [3.2, 2.9],
    },
}


class TestFetchHelpers(unittest.TestCase):
    def test_fetch_forecast_calls_both_endpoints(self):
        with patch("open_meteo_fetcher._get", side_effect=[SAMPLE_WEATHER, SAMPLE_AQI]) as mock_get:
            raw = omf.fetch_forecast(37.98, 23.73, forecast_days=3)
        self.assertEqual(mock_get.call_count, 2)
        first_call_url = mock_get.call_args_list[0].args[0]
        second_call_url = mock_get.call_args_list[1].args[0]
        self.assertEqual(first_call_url, omf.FORECAST_WEATHER_API)
        self.assertEqual(second_call_url, omf.FORECAST_AQI_API)
        self.assertEqual(raw["weather"], SAMPLE_WEATHER)
        self.assertEqual(raw["air_quality"], SAMPLE_AQI)

    def test_fetch_past_days_clamps_aqi_to_92(self):
        with patch("open_meteo_fetcher._get", side_effect=[SAMPLE_WEATHER, SAMPLE_AQI]) as mock_get:
            omf.fetch_past_days(37.98, 23.73, days=200)
        weather_params = mock_get.call_args_list[0].args[1]
        aqi_params = mock_get.call_args_list[1].args[1]
        self.assertEqual(weather_params["past_days"], 200)
        self.assertEqual(aqi_params["past_days"], 92)

    def test_fetch_historical_has_no_air_quality(self):
        with patch("open_meteo_fetcher._get", return_value=SAMPLE_WEATHER):
            raw = omf.fetch_historical(37.98, 23.73, "2024-01-01", "2024-01-31")
        self.assertIsNone(raw["air_quality"])
        self.assertEqual(raw["weather"], SAMPLE_WEATHER)


class TestBuildDataframes(unittest.TestCase):
    def test_merges_weather_and_aqi_on_datetime(self):
        raw = {"weather": SAMPLE_WEATHER, "air_quality": SAMPLE_AQI}
        weather_df, aqi_df, combined_df = omf.build_dataframes(raw, "Athens")
        self.assertEqual(len(weather_df), 2)
        self.assertEqual(len(aqi_df), 2)
        self.assertEqual(len(combined_df), 2)
        self.assertIn("temperature_2m", combined_df.columns)
        self.assertIn("olive_pollen", combined_df.columns)
        self.assertIn("dust", combined_df.columns)
        self.assertTrue((combined_df["location"] == "Athens").all())

    def test_historical_mode_falls_back_to_weather_only(self):
        raw = {"weather": SAMPLE_WEATHER, "air_quality": None}
        weather_df, aqi_df, combined_df = omf.build_dataframes(raw, "Athens")
        self.assertTrue(aqi_df.empty)
        self.assertEqual(len(combined_df), len(weather_df))


class TestCliValidation(unittest.TestCase):
    def test_unknown_city_is_rejected_before_any_request(self):
        self.assertNotIn("Atlantis", omf.GREEK_LOCATIONS)


class UtcNormalisationTests(unittest.TestCase):
    """Open-Meteo answers in local time; everything downstream must be UTC.

    Regression guard for a bug found on 2026-08-26: `utc_offset_seconds` was
    ignored, so naive Athens local times were stored labelled as UTC and every
    record sat 2-3 hours away from the truth.
    """

    #: +3h, i.e. Europe/Athens in summer, exactly as the live API returns it.
    OFFSET = 10800

    def _response(self, **extra_keys):
        response = {
            "latitude": 37.98,
            "longitude": 23.73,
            "utc_offset_seconds": self.OFFSET,
            "timezone": "Europe/Athens",
            "hourly": {
                "time": ["2026-08-03T00:00", "2026-08-03T01:00"],
                "temperature_2m": [28.1, 27.5],
            },
        }
        response.update(extra_keys)
        return response

    def test_datetime_column_is_shifted_back_to_utc(self):
        df = omf._hourly_to_df(self._response(), "Athens")
        self.assertEqual(str(df["datetime"].iloc[0]), "2026-08-02 21:00:00")
        self.assertEqual(str(df["datetime"].iloc[1]), "2026-08-02 22:00:00")

    def test_local_time_is_preserved_alongside(self):
        df = omf._hourly_to_df(self._response(), "Athens")
        self.assertEqual(str(df["datetime_local"].iloc[0]), "2026-08-03 00:00:00")

    def test_missing_offset_is_treated_as_utc(self):
        response = self._response()
        del response["utc_offset_seconds"]
        df = omf._hourly_to_df(response, "Athens")
        self.assertEqual(str(df["datetime"].iloc[0]), "2026-08-03 00:00:00")

    def test_null_offset_is_treated_as_utc(self):
        df = omf._hourly_to_df(self._response(utc_offset_seconds=None), "Athens")
        self.assertEqual(str(df["datetime"].iloc[0]), "2026-08-03 00:00:00")

    def test_winter_offset_is_two_hours(self):
        response = self._response(utc_offset_seconds=7200)
        response["hourly"]["time"] = ["2026-01-15T00:00", "2026-01-15T01:00"]
        df = omf._hourly_to_df(response, "Athens")
        self.assertEqual(str(df["datetime"].iloc[0]), "2026-01-14 22:00:00")

    def test_merge_keeps_a_single_local_column(self):
        weather = self._response()
        aqi = self._response()
        aqi["hourly"] = {
            "time": ["2026-08-03T00:00", "2026-08-03T01:00"],
            "olive_pollen": [12.0, 10.5],
        }
        _, _, combined = omf.build_dataframes(
            {"weather": weather, "air_quality": aqi}, "Athens"
        )
        local_columns = [c for c in combined.columns if c.startswith("datetime_local")]
        self.assertEqual(local_columns, ["datetime_local"])
        self.assertEqual(len(combined), 2)


if __name__ == "__main__":
    unittest.main()

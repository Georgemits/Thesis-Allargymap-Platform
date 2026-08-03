"""Unit tests for mongo_importer.py's row -> document conversion.

Covers task-3 verification: African/Saharan dust (CAMS, via Open-Meteo) must
survive into the air_quality sub-document alongside pollen.
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mongo_importer as mi  # noqa: E402


class TestRowToDocument(unittest.TestCase):
    def _row(self, **overrides):
        base = {
            "datetime": pd.Timestamp("2026-08-03T12:00:00"),
            "location": "Athens",
            "latitude": 37.9838,
            "longitude": 23.7275,
            "temperature_2m": 29.5,
            "olive_pollen": 6.0,
            "dust": 14.2,
            "pm10": 22.1,
            "european_aqi": 45,
        }
        base.update(overrides)
        return pd.Series(base)

    def test_dust_lands_in_air_quality(self):
        doc = mi.row_to_document(self._row())
        self.assertEqual(doc["air_quality"]["dust"], 14.2)
        self.assertEqual(doc["air_quality"]["pm10"], 22.1)
        self.assertEqual(doc["air_quality"]["european_aqi"], 45)

    def test_pollen_and_weather_also_split_correctly(self):
        doc = mi.row_to_document(self._row())
        self.assertEqual(doc["pollen"]["olive_pollen"], 6.0)
        self.assertEqual(doc["weather"]["temperature_2m"], 29.5)
        # dust must not leak into the pollen sub-document
        self.assertNotIn("dust", doc["pollen"])

    def test_missing_dust_column_is_simply_absent(self):
        row = self._row()
        row = row.drop("dust")
        doc = mi.row_to_document(row)
        self.assertNotIn("dust", doc["air_quality"])

    def test_nan_dust_becomes_none(self):
        doc = mi.row_to_document(self._row(dust=float("nan")))
        self.assertIsNone(doc["air_quality"]["dust"])

    def test_location_is_geojson_point_lon_lat_order(self):
        doc = mi.row_to_document(self._row())
        self.assertEqual(doc["location"]["type"], "Point")
        self.assertEqual(doc["location"]["coordinates"], [23.7275, 37.9838])


if __name__ == "__main__":
    unittest.main()

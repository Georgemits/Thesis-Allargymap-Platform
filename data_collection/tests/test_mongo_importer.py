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


class FlattenForSetTests(unittest.TestCase):
    """Partial sources must merge, not overwrite.

    Regression guard for a hazard found on 2026-08-26: the archive API returns
    weather only, so `row_to_document` produced `pollen: {}`, and a whole
    sub-document `$set` would have wiped 92 days of collected pollen.
    """

    def test_measurement_groups_become_dotted_paths(self):
        flat = mi.flatten_for_set({"city": "Athens", "weather": {"temperature_2m": 28.1}})
        self.assertEqual(flat, {"city": "Athens", "weather.temperature_2m": 28.1})

    def test_empty_group_contributes_nothing(self):
        flat = mi.flatten_for_set({"city": "Athens", "pollen": {}, "air_quality": {}})
        self.assertEqual(flat, {"city": "Athens"})
        self.assertNotIn("pollen", flat)

    def test_a_weather_only_row_never_mentions_pollen(self):
        doc = {
            "city": "Athens",
            "weather": {"temperature_2m": 28.1, "relative_humidity_2m": 55},
            "pollen": {},
            "air_quality": {},
        }
        flat = mi.flatten_for_set(doc)
        self.assertFalse([k for k in flat if k.startswith(("pollen", "air_quality"))])
        self.assertIn("weather.temperature_2m", flat)

    def test_non_group_dicts_are_set_whole(self):
        point = {"type": "Point", "coordinates": [23.73, 37.98]}
        flat = mi.flatten_for_set({"location": point})
        self.assertEqual(flat, {"location": point})

    def test_null_values_are_preserved_not_dropped(self):
        flat = mi.flatten_for_set({"weather": {"uv_index": None}})
        self.assertEqual(flat, {"weather.uv_index": None})


class BuildUpsertUpdateTests(unittest.TestCase):
    """Nulls must never overwrite a value another pass supplied.

    Regression guard for data loss seen on 2026-08-26: an archive weather fill
    blanked olive pollen for 6000 hours, because
    `pollen_source.normalize_open_meteo({})` returns all six plant keys
    explicitly set to None and dotted-path `$set` wrote them faithfully.
    """

    def test_real_values_go_to_set(self):
        update = mi.build_upsert_update({"city": "Athens", "weather": {"temperature_2m": 19.7}})
        self.assertEqual(update["$set"], {"city": "Athens", "weather.temperature_2m": 19.7})

    def test_nulls_go_to_set_on_insert_only(self):
        update = mi.build_upsert_update({"city": "Athens", "pollen": {"olive_pollen": None}})
        self.assertNotIn("pollen.olive_pollen", update["$set"])
        self.assertEqual(update["$setOnInsert"], {"pollen.olive_pollen": None})

    def test_a_weather_only_row_cannot_blank_existing_pollen(self):
        """The exact shape that caused the loss: weather values, pollen all None."""
        doc = {
            "city": "Athens",
            "weather": {"temperature_2m": 19.7, "relative_humidity_2m": 61},
            "pollen": {k: None for k in
                       ("alder_pollen", "birch_pollen", "grass_pollen",
                        "mugwort_pollen", "olive_pollen", "ragweed_pollen")},
            "pollen_upi": None,
        }
        update = mi.build_upsert_update(doc)
        self.assertFalse([k for k in update["$set"] if k.startswith("pollen")])
        self.assertEqual(len(update["$setOnInsert"]), 7)

    def test_a_brand_new_hour_still_gets_the_full_field_shape(self):
        doc = {"city": "Athens", "weather": {"temperature_2m": 19.7, "uv_index": None}}
        update = mi.build_upsert_update(doc)
        written = set(update["$set"]) | set(update["$setOnInsert"])
        self.assertEqual(written, {"city", "weather.temperature_2m", "weather.uv_index"})

    def test_set_and_set_on_insert_never_share_a_path(self):
        doc = {"weather": {"a": 1, "b": None}, "pollen": {"a": None, "b": 2}}
        update = mi.build_upsert_update(doc)
        self.assertFalse(set(update["$set"]) & set(update["$setOnInsert"]))

    def test_a_row_with_no_nulls_omits_set_on_insert(self):
        update = mi.build_upsert_update({"city": "Athens", "weather": {"temperature_2m": 19.7}})
        self.assertNotIn("$setOnInsert", update)


if __name__ == "__main__":
    unittest.main()

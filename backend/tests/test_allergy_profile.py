"""Unit tests for app.models.allergy_profile (pure, no Mongo)."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.allergy_profile import (  # noqa: E402
    ALLERGENS,
    MAX_SEVERITY,
    PROFILE_SCHEMA_VERSION,
    build_profile_upsert,
    catalogue,
    measurement_paths,
    validate_allergens,
)

DEVICE_ID = "3f2a9c1e-7b4d-4a6f-9e21-0c8d5b1a2f30"
T0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


class TestCatalogue(unittest.TestCase):
    def test_every_allergen_is_offered(self):
        self.assertEqual({e["key"] for e in catalogue()}, set(ALLERGENS))

    def test_measurement_paths_are_not_exposed_to_the_client(self):
        # They are a backend detail; publishing them invites a frontend to
        # start reading env_snapshots itself.
        for entry in catalogue():
            self.assertNotIn("paths", entry)

    def test_every_allergen_maps_to_a_real_measurement(self):
        # The point of the list: a participant must never be able to declare
        # something the platform cannot score them against. These are the
        # documented sections of an env_snapshots document.
        for key in ALLERGENS:
            paths = measurement_paths(key)
            self.assertTrue(paths, f"{key} has no measurement path")
            for path in paths:
                section, _, field = path.partition(".")
                self.assertIn(section, {"pollen", "air_quality", "weather"})
                self.assertTrue(field, f"{path} is not a dotted path")


class TestValidateAllergens(unittest.TestCase):
    def test_valid_map_passes_through(self):
        self.assertEqual(
            validate_allergens({"olive_pollen": 3, "saharan_dust": 1}),
            {"olive_pollen": 3, "saharan_dust": 1},
        )

    def test_zero_severity_is_dropped_not_stored(self):
        self.assertEqual(validate_allergens({"olive_pollen": 3, "grass_pollen": 0}),
                         {"olive_pollen": 3})

    def test_empty_map_is_allowed(self):
        self.assertEqual(validate_allergens({}), {})

    def test_unknown_allergen_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            validate_allergens({"dog_dander": 2})
        self.assertIn("dog_dander", str(ctx.exception))

    def test_out_of_range_severity_is_rejected(self):
        for value in (-1, MAX_SEVERITY + 1, 99):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_allergens({"olive_pollen": value})

    def test_non_integer_severity_is_rejected(self):
        for value in ("3", 2.5, None, [3], True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_allergens({"olive_pollen": value})

    def test_missing_or_non_object_payload_is_rejected(self):
        for raw in (None, [], "olive_pollen", 3):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    validate_allergens(raw)


class TestBuildProfileUpsert(unittest.TestCase):
    def test_allergens_are_replaced_wholesale(self):
        # Merging would make un-ticking an allergen impossible: the page always
        # submits the complete answer.
        update = build_profile_upsert(DEVICE_ID, {"olive_pollen": 2}, now=T0)
        self.assertEqual(update["$set"]["allergens"], {"olive_pollen": 2})
        self.assertEqual(update["$set"]["updated_at"], T0)

    def test_creation_metadata_is_insert_only(self):
        update = build_profile_upsert(DEVICE_ID, {}, now=T0)
        self.assertEqual(update["$setOnInsert"]["created_at"], T0)
        self.assertEqual(update["$setOnInsert"]["device_id"], DEVICE_ID)
        self.assertEqual(update["$setOnInsert"]["schema_version"], PROFILE_SCHEMA_VERSION)
        self.assertNotIn("created_at", update["$set"])

    def test_stored_map_is_a_copy(self):
        # A shared reference would let a later mutation of the caller's dict
        # silently change what was queued for the database.
        allergens = {"olive_pollen": 1}
        update = build_profile_upsert(DEVICE_ID, allergens, now=T0)
        allergens["grass_pollen"] = 3
        self.assertEqual(update["$set"]["allergens"], {"olive_pollen": 1})


if __name__ == "__main__":
    unittest.main()

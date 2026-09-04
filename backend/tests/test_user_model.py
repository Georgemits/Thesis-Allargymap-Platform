"""Unit tests for app.models.user (pure document builders, no Mongo)."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.user import (  # noqa: E402
    ALIAS_MAX_LENGTH,
    USER_SCHEMA_VERSION,
    build_upsert,
    build_user,
    sanitize_alias,
)

DEVICE_ID = "3f2a9c1e-7b4d-4a6f-9e21-0c8d5b1a2f30"
T0 = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 9, 4, 18, 30, tzinfo=timezone.utc)


class TestSanitizeAlias(unittest.TestCase):
    def test_strips_whitespace(self):
        self.assertEqual(sanitize_alias("  phone  "), "phone")

    def test_none_becomes_empty_string(self):
        self.assertEqual(sanitize_alias(None), "")

    def test_truncated_to_the_limit(self):
        self.assertEqual(len(sanitize_alias("x" * 500)), ALIAS_MAX_LENGTH)


class TestBuildUser(unittest.TestCase):
    def test_has_the_documented_shape(self):
        doc = build_user(DEVICE_ID, alias="phone", now=T0)
        self.assertEqual(
            set(doc),
            {"device_id", "created_at", "last_seen_at", "alias", "schema_version"},
        )
        self.assertEqual(doc["device_id"], DEVICE_ID)
        self.assertEqual(doc["created_at"], T0)
        self.assertEqual(doc["last_seen_at"], T0)
        self.assertEqual(doc["alias"], "phone")
        self.assertEqual(doc["schema_version"], USER_SCHEMA_VERSION)


class TestBuildUpsert(unittest.TestCase):
    def test_last_seen_is_always_set(self):
        self.assertEqual(build_upsert(DEVICE_ID, now=T1)["$set"]["last_seen_at"], T1)

    def test_created_at_is_insert_only(self):
        # A repeat call must not rewrite the date the participant joined --
        # the evaluation chapter describes the cohort from that field.
        update = build_upsert(DEVICE_ID, now=T1)
        self.assertEqual(update["$setOnInsert"]["created_at"], T1)
        self.assertNotIn("created_at", update["$set"])

    def test_absent_alias_never_blanks_an_existing_one(self):
        # The null-overwrite failure mode: a $set of None on every touch would
        # erase a label the participant had already chosen.
        update = build_upsert(DEVICE_ID, alias=None, now=T1)
        self.assertNotIn("alias", update["$set"])
        self.assertEqual(update["$setOnInsert"]["alias"], "")

    def test_supplied_alias_is_written_and_sanitized(self):
        update = build_upsert(DEVICE_ID, alias="  laptop  ", now=T1)
        self.assertEqual(update["$set"]["alias"], "laptop")
        self.assertNotIn("alias", update["$setOnInsert"])

    def test_device_id_is_insert_only(self):
        # It is the query key; rewriting it on every touch would be a no-op at
        # best and a way to corrupt the unique index at worst.
        update = build_upsert(DEVICE_ID, now=T1)
        self.assertEqual(update["$setOnInsert"]["device_id"], DEVICE_ID)
        self.assertNotIn("device_id", update["$set"])


if __name__ == "__main__":
    unittest.main()

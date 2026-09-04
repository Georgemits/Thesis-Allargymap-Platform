"""Unit tests for app.models.user (pure document builders, no Mongo)."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.user import (  # noqa: E402
    ALIAS_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    USER_SCHEMA_VERSION,
    build_upsert,
    build_user,
    normalize_username,
    public_user,
    sanitize_alias,
    validate_password,
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


class TestNormalizeUsername(unittest.TestCase):
    def test_lower_cased_and_trimmed(self):
        # "George" and "george" must not become two accounts holding half of
        # one person's data each.
        self.assertEqual(normalize_username("  George.M  "), "george.m")

    def test_allowed_punctuation(self):
        for name in ("geo_mits", "geo.mits", "geo-mits", "geo123"):
            with self.subTest(name=name):
                self.assertEqual(normalize_username(name), name)

    def test_rejects_bad_length(self):
        for name in (None, "", "ab", "x" * 31):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    normalize_username(name)

    def test_rejects_illegal_characters_and_edges(self):
        for name in ("geo mits", "geo@mits", ".george", "george-", "γιώργος"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    normalize_username(name)


class TestValidatePassword(unittest.TestCase):
    def test_accepts_a_long_enough_password(self):
        self.assertEqual(validate_password("correct horse"), "correct horse")

    def test_surrounding_spaces_are_preserved(self):
        # Trimming would silently change what the participant typed, and they
        # would then fail to sign in with the password they thought they set.
        self.assertEqual(validate_password("  spaces  "), "  spaces  ")

    def test_rejects_short_missing_or_non_string(self):
        for value in (None, "", "x" * (PASSWORD_MIN_LENGTH - 1), 12345678):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_password(value)

    def test_rejects_absurdly_long(self):
        with self.assertRaises(ValueError):
            validate_password("x" * 500)


class TestPublicUser(unittest.TestCase):
    def _doc(self):
        return {
            "_id": "abc",
            "device_id": DEVICE_ID,
            "created_at": T0,
            "last_seen_at": T1,
            "alias": "phone",
            "username": "george",
            "password_hash": "scrypt:32768:8:1$deadbeef",
            "schema_version": USER_SCHEMA_VERSION,
        }

    def test_password_hash_never_survives(self):
        self.assertNotIn("password_hash", public_user(self._doc()))

    def test_unknown_fields_are_dropped(self):
        # Whitelist, not blacklist: a field added to the document later stays
        # invisible until someone deliberately publishes it.
        doc = self._doc()
        doc["internal_note"] = "secret"
        self.assertNotIn("internal_note", public_user(doc))

    def test_keeps_the_public_fields(self):
        public = public_user(self._doc())
        self.assertEqual(public["device_id"], DEVICE_ID)
        self.assertEqual(public["username"], "george")
        self.assertTrue(public["has_account"])

    def test_has_account_is_false_when_anonymous(self):
        doc = self._doc()
        del doc["username"]
        del doc["password_hash"]
        self.assertFalse(public_user(doc)["has_account"])

    def test_does_not_mutate_the_input(self):
        doc = self._doc()
        public_user(doc)
        self.assertIn("password_hash", doc)


if __name__ == "__main__":
    unittest.main()

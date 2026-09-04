"""Unit tests for app.utils.identity (pure + a throwaway Flask app, no Mongo)."""

import sys
import unittest
import uuid
from pathlib import Path

from flask import Flask, g, jsonify

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.utils.identity import (  # noqa: E402
    DEVICE_ID_HEADER,
    is_valid_device_id,
    normalize_device_id,
    require_device_id,
)

VALID = "3f2a9c1e-7b4d-4a6f-9e21-0c8d5b1a2f30"


class TestNormalizeDeviceId(unittest.TestCase):
    def test_canonical_value_is_returned_unchanged(self):
        self.assertEqual(normalize_device_id(VALID), VALID)

    def test_whitespace_and_case_are_normalized(self):
        self.assertEqual(normalize_device_id(f"  {VALID.upper()} "), VALID)

    def test_unhyphenated_form_is_accepted(self):
        self.assertEqual(normalize_device_id(VALID.replace("-", "")), VALID)

    def test_missing_value_is_rejected(self):
        for raw in (None, "", "   "):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    normalize_device_id(raw)

    def test_arbitrary_strings_are_rejected(self):
        # The whole model rests on identifiers being unguessable, so a
        # client must not be able to name itself "admin" or "1".
        for raw in ("admin", "1", "user@example.com", "not-a-uuid", "../../etc/passwd"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    normalize_device_id(raw)

    def test_other_uuid_versions_are_rejected(self):
        # v1 embeds a MAC address and a timestamp: linkable to a machine,
        # and predictable enough to guess a neighbouring identifier.
        with self.assertRaises(ValueError):
            normalize_device_id(str(uuid.uuid1()))

    def test_generated_uuid4_round_trips(self):
        for _ in range(20):
            value = str(uuid.uuid4())
            self.assertEqual(normalize_device_id(value), value)


class TestIsValidDeviceId(unittest.TestCase):
    def test_true_for_uuid4(self):
        self.assertTrue(is_valid_device_id(VALID))

    def test_false_instead_of_raising(self):
        for raw in (None, "", "admin", str(uuid.uuid1())):
            with self.subTest(raw=raw):
                self.assertFalse(is_valid_device_id(raw))


class TestRequireDeviceIdDecorator(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)

        @app.get("/probe")
        @require_device_id
        def probe():
            return jsonify({"device_id": g.device_id})

        self.client = app.test_client()

    def test_valid_header_reaches_the_view(self):
        res = self.client.get("/probe", headers={DEVICE_ID_HEADER: VALID.upper()})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["device_id"], VALID)

    def test_missing_header_is_a_400(self):
        res = self.client.get("/probe")
        self.assertEqual(res.status_code, 400)
        self.assertIn("error", res.get_json())

    def test_malformed_header_is_a_400_not_a_500(self):
        res = self.client.get("/probe", headers={DEVICE_ID_HEADER: "admin"})
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()

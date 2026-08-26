"""Unit tests for app/utils/serialization.py.

Regression guard for a bug found on 2026-08-26: the API emitted naive ISO
strings for UTC instants, and browsers parse an offset-less date-time as local
time -- a silent two to three hour error for a Greek deployment.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.utils.serialization import iso_utc, serialize_doc  # noqa: E402

ATHENS_SUMMER = timezone(timedelta(hours=3))


class IsoUtcTests(unittest.TestCase):
    def test_naive_input_is_labelled_utc(self):
        self.assertEqual(iso_utc(datetime(2026, 8, 3, 0, 0)), "2026-08-03T00:00:00+00:00")

    def test_output_always_carries_an_explicit_offset(self):
        rendered = iso_utc(datetime(2026, 8, 3, 0, 0))
        self.assertTrue(rendered.endswith("+00:00"))

    def test_aware_input_is_converted_not_relabelled(self):
        aware = datetime(2026, 8, 3, 0, 0, tzinfo=ATHENS_SUMMER)
        self.assertEqual(iso_utc(aware), "2026-08-02T21:00:00+00:00")

    def test_none_passes_through(self):
        self.assertIsNone(iso_utc(None))


class SerializeDocTests(unittest.TestCase):
    def test_object_id_becomes_a_string(self):
        doc = serialize_doc({"_id": 12345, "timestamp": datetime(2026, 8, 3)})
        self.assertEqual(doc["_id"], "12345")

    def test_timestamp_is_rendered_with_an_offset(self):
        doc = serialize_doc({"_id": 1, "timestamp": datetime(2026, 8, 3, 6, 30)})
        self.assertEqual(doc["timestamp"], "2026-08-03T06:30:00+00:00")

    def test_custom_datetime_fields_are_honoured(self):
        doc = serialize_doc(
            {"_id": 1, "forecast_timestamp": datetime(2026, 8, 3), "generated_at": datetime(2026, 8, 2)},
            datetime_fields=("forecast_timestamp", "generated_at"),
        )
        self.assertEqual(doc["forecast_timestamp"], "2026-08-03T00:00:00+00:00")
        self.assertEqual(doc["generated_at"], "2026-08-02T00:00:00+00:00")

    def test_missing_and_non_datetime_fields_are_left_alone(self):
        doc = serialize_doc({"_id": 1, "timestamp": "already-a-string", "city": "Athens"})
        self.assertEqual(doc["timestamp"], "already-a-string")
        self.assertEqual(doc["city"], "Athens")

    def test_document_without_an_id_is_accepted(self):
        self.assertEqual(serialize_doc({"city": "Patras"}), {"city": "Patras"})


if __name__ == "__main__":
    unittest.main()

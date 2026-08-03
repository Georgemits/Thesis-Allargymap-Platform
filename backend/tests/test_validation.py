"""Unit tests for app.utils.validation.parse_date_range."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.utils.validation import parse_date_range  # noqa: E402


class FakeArgs(dict):
    """Minimal stand-in for Flask's request.args (a MultiDict)."""

    def get(self, key, default=None):
        return super().get(key, default)


class TestParseDateRange(unittest.TestCase):
    def test_no_params_returns_none_none(self):
        self.assertEqual(parse_date_range(FakeArgs()), (None, None))

    def test_valid_range(self):
        start, end = parse_date_range(FakeArgs(start_date="2026-08-01", end_date="2026-08-05"))
        self.assertEqual(start.isoformat(), "2026-08-01")
        self.assertEqual(end.isoformat(), "2026-08-05")

    def test_only_start_date_raises(self):
        with self.assertRaises(ValueError):
            parse_date_range(FakeArgs(start_date="2026-08-01"))

    def test_only_end_date_raises(self):
        with self.assertRaises(ValueError):
            parse_date_range(FakeArgs(end_date="2026-08-05"))

    def test_bad_format_raises(self):
        with self.assertRaises(ValueError):
            parse_date_range(FakeArgs(start_date="08/01/2026", end_date="08/05/2026"))

    def test_end_before_start_raises(self):
        with self.assertRaises(ValueError):
            parse_date_range(FakeArgs(start_date="2026-08-05", end_date="2026-08-01"))

    def test_same_day_is_valid(self):
        start, end = parse_date_range(FakeArgs(start_date="2026-08-01", end_date="2026-08-01"))
        self.assertEqual(start, end)

    def test_range_too_large_raises(self):
        with self.assertRaises(ValueError):
            parse_date_range(FakeArgs(start_date="2020-01-01", end_date="2026-08-01"), max_range_days=400)

    def test_range_within_custom_max_is_valid(self):
        start, end = parse_date_range(
            FakeArgs(start_date="2026-08-01", end_date="2026-08-06"), max_range_days=5
        )
        self.assertEqual((end - start).days, 5)


if __name__ == "__main__":
    unittest.main()

"""Unit tests for app.routes.predictions._filter_by_date_range (pure, no Mongo)."""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.routes.predictions import _filter_by_date_range  # noqa: E402


class TestFilterByDateRange(unittest.TestCase):
    def _docs(self):
        return [
            {"forecast_timestamp": "2026-08-01T00:00:00", "predicted_value": 1.0},
            {"forecast_timestamp": "2026-08-03T12:00:00", "predicted_value": 2.0},
            {"forecast_timestamp": "2026-08-07T23:00:00", "predicted_value": 3.0},
        ]

    def test_no_range_returns_all(self):
        result = _filter_by_date_range(self._docs(), None, None)
        self.assertEqual(len(result), 3)

    def test_range_keeps_only_matching_days(self):
        result = _filter_by_date_range(self._docs(), date(2026, 8, 2), date(2026, 8, 5))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["predicted_value"], 2.0)

    def test_boundary_dates_are_inclusive(self):
        result = _filter_by_date_range(self._docs(), date(2026, 8, 1), date(2026, 8, 7))
        self.assertEqual(len(result), 3)

    def test_range_outside_data_returns_empty(self):
        result = _filter_by_date_range(self._docs(), date(2026, 9, 1), date(2026, 9, 5))
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()

"""Unit tests for app.services.statistics (pure arithmetic, no dependencies).

The reference values below were produced by `scipy.stats` during development
and are pinned here as constants, so the test suite verifies this
implementation against an established one without SciPy being installed to run
it -- or being a dependency of the deployed backend.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.statistics import (  # noqa: E402
    correlation_p_value,
    describe_strength,
    mean,
    pearson,
    quantile,
    rank,
    regularized_incomplete_beta,
    spearman,
)

# A pollen-like sample (many zeros, long tail) against a 0-10 symptom score.
POLLEN = [12.0, 4.0, 0.0, 33.0, 21.0, 8.0, 0.0, 45.0, 17.0, 2.0]
SYMPTOM = [7.0, 3.0, 1.0, 9.0, 6.0, 4.0, 0.0, 10.0, 5.0, 2.0]
SCIPY_PEARSON = 0.9316293229760522
SCIPY_SPEARMAN = 0.960490759014502
SCIPY_P_VALUE = 8.797771351355961e-05


class TestPearson(unittest.TestCase):
    def test_matches_the_scipy_reference(self):
        self.assertAlmostEqual(pearson(POLLEN, SYMPTOM), SCIPY_PEARSON, places=12)

    def test_perfect_positive_and_negative(self):
        self.assertAlmostEqual(pearson([1, 2, 3, 4], [2, 4, 6, 8]), 1.0, places=12)
        self.assertAlmostEqual(pearson([1, 2, 3, 4], [8, 6, 4, 2]), -1.0, places=12)

    def test_constant_sample_is_undefined_not_a_crash(self):
        # Routine here: a pollen that is zero all summer has no variance, and
        # a constant cannot correlate with anything.
        self.assertIsNone(pearson([0, 0, 0, 0], [1, 2, 3, 4]))

    def test_too_few_pairs(self):
        self.assertIsNone(pearson([1.0], [2.0]))
        self.assertIsNone(pearson([], []))

    def test_mismatched_lengths_raise(self):
        with self.assertRaises(ValueError):
            pearson([1, 2, 3], [1, 2])

    def test_stays_inside_the_valid_range(self):
        for r in (pearson([1, 2, 3], [1, 2, 3]), pearson([1, 2, 3], [3, 2, 1])):
            self.assertLessEqual(abs(r), 1.0)


class TestRank(unittest.TestCase):
    def test_ties_share_their_average_rank(self):
        self.assertEqual(rank([10, 20, 20, 30]), [1.0, 2.5, 2.5, 4.0])

    def test_all_tied(self):
        self.assertEqual(rank([5, 5, 5]), [2.0, 2.0, 2.0])

    def test_preserves_input_order(self):
        self.assertEqual(rank([30, 10, 20]), [3.0, 1.0, 2.0])


class TestSpearman(unittest.TestCase):
    def test_matches_the_scipy_reference(self):
        self.assertAlmostEqual(spearman(POLLEN, SYMPTOM), SCIPY_SPEARMAN, places=12)

    def test_monotonic_but_curved_scores_higher_than_pearson(self):
        # The reason both are reported: dose-response flattens at the top of a
        # bounded symptom scale, so a real effect can be strongly monotonic and
        # only moderately linear.
        xs = [1, 2, 3, 4, 5]
        ys = [1, 2, 4, 8, 100]
        self.assertGreater(spearman(xs, ys), pearson(xs, ys))

    def test_perfect_monotonic_is_one(self):
        self.assertAlmostEqual(spearman([1, 5, 9, 20], [2, 3, 4, 99]), 1.0, places=12)


class TestIncompleteBeta(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(regularized_incomplete_beta(2.0, 3.0, 0.0), 0.0)
        self.assertEqual(regularized_incomplete_beta(2.0, 3.0, 1.0), 1.0)

    def test_symmetry_identity(self):
        # I_x(a,b) = 1 - I_(1-x)(b,a); the implementation switches branch at
        # (a+1)/(a+b+2), so this exercises both sides of that switch.
        for a, b, x in ((0.5, 4.0, 0.2), (7.0, 2.0, 0.8), (3.0, 3.0, 0.5)):
            with self.subTest(a=a, b=b, x=x):
                self.assertAlmostEqual(
                    regularized_incomplete_beta(a, b, x),
                    1.0 - regularized_incomplete_beta(b, a, 1.0 - x),
                    places=12,
                )

    def test_uniform_case_is_the_identity(self):
        # I_x(1,1) = x.
        for x in (0.1, 0.37, 0.9):
            self.assertAlmostEqual(regularized_incomplete_beta(1.0, 1.0, x), x, places=12)


class TestPValue(unittest.TestCase):
    def test_matches_the_scipy_reference(self):
        self.assertAlmostEqual(
            correlation_p_value(SCIPY_PEARSON, len(POLLEN)), SCIPY_P_VALUE, places=12
        )

    def test_no_correlation_is_certain_to_be_nothing(self):
        self.assertAlmostEqual(correlation_p_value(0.0, 50), 1.0, places=12)

    def test_perfect_correlation(self):
        self.assertEqual(correlation_p_value(1.0, 50), 0.0)

    def test_undefined_without_degrees_of_freedom(self):
        self.assertIsNone(correlation_p_value(0.9, 2))
        self.assertIsNone(correlation_p_value(None, 50))

    def test_same_coefficient_is_less_significant_in_a_smaller_sample(self):
        self.assertGreater(correlation_p_value(0.4, 12), correlation_p_value(0.4, 200))


class TestDescribeStrength(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(describe_strength(None), "none")
        self.assertEqual(describe_strength(0.05), "negligible")
        self.assertEqual(describe_strength(-0.2), "weak")
        self.assertEqual(describe_strength(0.4), "moderate")
        self.assertEqual(describe_strength(-0.8), "strong")


class TestMeanAndQuantile(unittest.TestCase):
    def test_mean(self):
        self.assertEqual(mean([1, 2, 3, 4]), 2.5)
        self.assertIsNone(mean([]))

    def test_quantile_interpolates_like_numpy(self):
        sample = [0.0, 1.0, 2.0, 3.0]
        self.assertAlmostEqual(quantile(sample, 0.5), 1.5, places=12)
        self.assertAlmostEqual(quantile(sample, 1 / 3), 1.0, places=12)

    def test_quantile_edges(self):
        self.assertIsNone(quantile([], 0.5))
        self.assertEqual(quantile([7.0], 0.9), 7.0)
        self.assertEqual(quantile([1.0, 2.0], 0.0), 1.0)
        self.assertEqual(quantile([1.0, 2.0], 1.0), 2.0)


if __name__ == "__main__":
    unittest.main()

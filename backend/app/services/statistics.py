"""Correlation statistics, computed from first principles.

The platform needs Pearson's *r*, Spearman's *rho* and the significance of
each, over samples of a few dozen to a few thousand pairs. That is well within
what a page of arithmetic can do, so it is done here rather than by importing
SciPy: the backend image stays light, the thesis can state the formula it
actually ran, and every step is directly testable. The implementation is
verified against ``scipy.stats`` on random data as part of development -- SciPy
is a check on this code, never a runtime dependency of it.

Nothing here knows about allergens, reports or MongoDB; it is arithmetic on
two lists of numbers.
"""

from __future__ import annotations

import math

#: Below this many paired observations a coefficient is not reported at all.
#: With a handful of points, r is dominated by whichever point happens to be
#: extreme -- publishing "r = 0.87 (n = 4)" would be worse than saying nothing,
#: because it reads as a finding.
MIN_SAMPLES = 20


def pearson(xs, ys):
    """Pearson product-moment correlation coefficient.

    Args:
        xs: First sample.
        ys: Second sample, the same length as `xs`.

    Returns:
        The coefficient in [-1, 1], or None when it is undefined: fewer than
        two pairs, or a sample with no variance at all (every reading
        identical, which is common for a pollen out of season -- a constant
        cannot correlate with anything).

    Raises:
        ValueError: If the two samples have different lengths.

    Examples:
        >>> round(pearson([1, 2, 3, 4], [2, 4, 6, 8]), 6)
        1.0
    """
    if len(xs) != len(ys):
        raise ValueError("pearson() needs two samples of the same length.")

    n = len(xs)
    if n < 2:
        return None

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    sxy = sxx = syy = 0.0
    for x, y in zip(xs, ys):
        dx = x - mean_x
        dy = y - mean_y
        sxy += dx * dy
        sxx += dx * dx
        syy += dy * dy

    if sxx <= 0.0 or syy <= 0.0:
        return None

    r = sxy / math.sqrt(sxx * syy)
    # Guard against a coefficient a hair outside [-1, 1] from rounding, which
    # would make the p-value computation take the square root of a negative.
    return max(-1.0, min(1.0, r))


def rank(values):
    """Return fractional ranks, tied values sharing their average rank.

    Ties are the normal case here, not an edge case: symptom scores are
    integers on a 0-10 scale and pollen readings are frequently zero, so a
    naive ordinal ranking would invent an ordering between equal values and
    quietly bias rho.

    Args:
        values: The sample to rank.

    Returns:
        A list of ranks, 1-based, in the order of the input.

    Examples:
        >>> rank([10, 20, 20, 30])
        [1.0, 2.5, 2.5, 4.0]
    """
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)

    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        average = (position + end) / 2.0 + 1.0
        for index in range(position, end + 1):
            ranks[order[index]] = average
        position = end + 1

    return ranks


def spearman(xs, ys):
    """Spearman rank correlation coefficient.

    Pearson's r computed on ranks. It measures whether the relationship is
    monotonic rather than straight-line, which matters here: symptom severity
    is a bounded ordinal scale and dose-response to an allergen tends to
    flatten at the top, so a real effect can be strongly monotonic and only
    moderately linear.

    Args:
        xs: First sample.
        ys: Second sample, the same length as `xs`.

    Returns:
        The coefficient in [-1, 1], or None when undefined.
    """
    if len(xs) != len(ys):
        raise ValueError("spearman() needs two samples of the same length.")
    if len(xs) < 2:
        return None
    return pearson(rank(xs), rank(ys))


def _log_beta_factor(a: float, b: float, x: float) -> float:
    """Log of x^a (1-x)^b / B(a, b), the prefactor of the continued fraction."""
    return (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )


def _beta_continued_fraction(a: float, b: float, x: float,
                             max_iterations: int = 300,
                             epsilon: float = 1e-15) -> float:
    """Lentz's algorithm for the continued fraction of the incomplete beta."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0

    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d

    for m in range(1, max_iterations + 1):
        m2 = 2 * m

        numerator = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + numerator * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + numerator / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c

        numerator = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + numerator * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + numerator / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta

        if abs(delta - 1.0) < epsilon:
            break

    return h


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b).

    Needed for the p-value below and nothing else. The continued fraction
    converges quickly for x below (a+1)/(a+b+2) and slowly above it, so the
    upper half is evaluated through the symmetry I_x(a,b) = 1 - I_(1-x)(b,a).

    Args:
        a: First shape parameter, > 0.
        b: Second shape parameter, > 0.
        x: Upper limit, in [0, 1].

    Returns:
        I_x(a, b) in [0, 1].
    """
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0

    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(_log_beta_factor(a, b, x)) * _beta_continued_fraction(a, b, x) / a
    return 1.0 - (
        math.exp(_log_beta_factor(b, a, 1.0 - x))
        * _beta_continued_fraction(b, a, 1.0 - x)
        / b
    )


def correlation_p_value(r: float, n: int):
    """Two-sided p-value for a correlation coefficient.

    Tests the null hypothesis that the true correlation is zero, via the usual
    transformation ``t = r * sqrt((n - 2) / (1 - r^2))`` on n-2 degrees of
    freedom. The tail probability of that t is exactly a regularized incomplete
    beta, which is why the function above exists::

        P(|T| > t) = I_{df / (df + t^2)}(df / 2, 1 / 2)

    Args:
        r: The coefficient, in [-1, 1].
        n: Number of paired observations it came from.

    Returns:
        The p-value in [0, 1], or None when it is undefined (fewer than three
        pairs, or no coefficient).

    Note:
        This is a single test. The engine runs one per environmental variable
        per symptom, so a p below 0.05 among dozens of tests is expected by
        chance alone -- `app.services.correlation` reports how many tests were
        run alongside the results so the reader can judge accordingly.

    Examples:
        >>> correlation_p_value(0.0, 30)
        1.0
    """
    if r is None:
        return None

    df = n - 2
    if df <= 0:
        return None
    if abs(r) >= 1.0:
        return 0.0

    t_squared = (r * r) * df / (1.0 - r * r)
    return regularized_incomplete_beta(df / 2.0, 0.5, df / (df + t_squared))


def describe_strength(r) -> str:
    """Label a coefficient's magnitude in words.

    Conventional social-science bands (Cohen), used only for display -- the
    number is what the analysis reports.

    Args:
        r: The coefficient, or None.

    Returns:
        One of "none", "negligible", "weak", "moderate" or "strong".
    """
    if r is None:
        return "none"
    magnitude = abs(r)
    if magnitude < 0.1:
        return "negligible"
    if magnitude < 0.3:
        return "weak"
    if magnitude < 0.5:
        return "moderate"
    return "strong"


def mean(values):
    """Arithmetic mean, or None for an empty sample."""
    values = list(values)
    return sum(values) / len(values) if values else None


def quantile(sorted_values, fraction: float):
    """Linear-interpolated quantile of an already sorted sample.

    Args:
        sorted_values: The sample, ascending.
        fraction: Quantile in [0, 1] (0.5 is the median).

    Returns:
        The interpolated value, or None for an empty sample.
    """
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]

    position = fraction * (len(sorted_values) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return sorted_values[int(position)]
    weight = position - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight

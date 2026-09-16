from __future__ import annotations

import pytest

from distressed_equity.rate_diagnostics import binomial_rate_diagnostic, wilson_interval


def test_wilson_interval_is_bounded_and_non_degenerate_for_one_of_one():
    low, high = wilson_interval(1, 1)
    assert 0.20 < low < 0.21
    assert high == pytest.approx(1.0)


def test_wilson_interval_for_zero_of_one_remains_wide():
    low, high = wilson_interval(0, 1)
    assert low == pytest.approx(0.0)
    assert 0.79 < high < 0.80


def test_binomial_diagnostic_flags_thin_point_estimates():
    diagnostic = binomial_rate_diagnostic(1.0, 1, small_sample_n=30)
    assert diagnostic.resolved_n == 1
    assert diagnostic.successes == 1
    assert diagnostic.rate == 1.0
    assert diagnostic.small_sample is True
    assert diagnostic.wilson_95_low < 0.21
    assert diagnostic.wilson_95_high == pytest.approx(1.0)
    assert "do not treat the point estimate as a precise probability" in diagnostic.warning


def test_threshold_is_resolved_denominator_based():
    diagnostic = binomial_rate_diagnostic(0.5, 30, small_sample_n=30)
    assert diagnostic.successes == 15
    assert diagnostic.small_sample is False
    assert diagnostic.warning is None
    assert diagnostic.wilson_95_low < 0.5 < diagnostic.wilson_95_high


def test_missing_rate_requires_zero_resolved_denominator():
    diagnostic = binomial_rate_diagnostic(None, 0)
    assert diagnostic.rate is None
    assert diagnostic.successes is None
    assert diagnostic.wilson_95_low is None
    assert diagnostic.wilson_95_high is None
    assert diagnostic.small_sample is True

    with pytest.raises(ValueError, match="rate cannot be None"):
        binomial_rate_diagnostic(None, 1)


def test_rate_and_denominator_must_imply_integer_success_count():
    with pytest.raises(ValueError, match="integer binomial success count"):
        binomial_rate_diagnostic(0.51, 3)

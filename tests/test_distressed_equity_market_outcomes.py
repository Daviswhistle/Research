from __future__ import annotations

from datetime import date

import pytest

from distressed_equity.market import PricePoint, SecurityIdentity
from distressed_equity.market_outcomes import (
    MarketOutcomeConfig,
    label_market_outcome,
    label_market_outcome_cohort,
)
from distressed_equity.replay import MarketDistressSeed


def _security(end_date=None):
    return SecurityIdentity(
        security_id="sec-1",
        symbol="OLD",
        name="Test Co",
        exchange="NYSE",
        asset_type="stock",
        start_date=date(2010, 1, 1),
        end_date=end_date,
        provider="fake",
    )


def _seed():
    security = _security()
    return MarketDistressSeed(
        security=security,
        analysis_date=date(2020, 12, 31),
        raw_price=PricePoint("sec-1", "OLD", date(2020, 12, 31), 5.0, False),
        adjusted_price=PricePoint("sec-1", "OLD", date(2020, 12, 31), 5.0, True),
        peak_adjusted_price=PricePoint("sec-1", "OLD", date(2019, 12, 31), 25.0, True),
        adjusted_drawdown_from_peak=0.8,
        lookback_start=date(2015, 12, 29),
    )


class OutcomeProvider:
    name = "outcome_fake"
    bulk_safe = True
    survivorship_safe_universe = True

    def __init__(self, *, end_date=None, points=()):
        self.security = _security(end_date=end_date)
        self.points = tuple(points)

    def universe(self, as_of):
        if self.security.start_date and as_of < self.security.start_date:
            return ()
        if self.security.end_date and as_of > self.security.end_date:
            return ()
        return (self.security,)

    def adjusted_history(self, security, start, end):
        return tuple(point for point in self.points if start <= point.date <= end)

    def price_on_or_before(self, security, as_of):
        rows = [point for point in self.points if point.date <= as_of]
        return max(rows, key=lambda item: item.date) if rows else None


def _points():
    return (
        PricePoint("sec-1", "OLD", date(2020, 12, 31), 5.0, True),
        PricePoint("sec-1", "NEW", date(2021, 12, 31), 10.0, True),
        PricePoint("sec-1", "NEW", date(2022, 6, 30), 20.0, True),
        PricePoint("sec-1", "NEW", date(2023, 12, 31), 15.0, True),
    )


def test_market_outcome_labels_same_permanent_security_and_price_multiples():
    outcome = label_market_outcome(
        OutcomeProvider(points=_points()),
        _seed(),
        date(2024, 1, 31),
    )
    assert outcome.same_security_active_12m is True
    assert outcome.adjusted_price_12m.date == date(2021, 12, 31)
    assert outcome.adjusted_price_multiple_12m == 2.0
    assert outcome.same_security_active_3y is True
    assert outcome.adjusted_price_3y.date == date(2023, 12, 31)
    assert outcome.adjusted_price_multiple_3y == 3.0
    assert outcome.max_adjusted_price_within_3y.date == date(2022, 6, 30)
    assert outcome.max_adjusted_price_multiple_within_3y == 4.0


def test_inactive_same_security_is_not_called_legal_common_failure():
    outcome = label_market_outcome(
        OutcomeProvider(end_date=date(2021, 6, 30), points=_points()),
        _seed(),
        date(2024, 1, 31),
    )
    assert outcome.same_security_active_12m is False
    assert outcome.adjusted_price_multiple_12m is None
    assert outcome.same_security_active_3y is False
    assert outcome.adjusted_price_multiple_3y is None
    # The observed path before exit may still have had a high price. This is kept separate.
    assert outcome.max_adjusted_price_multiple_within_3y == 4.0


def test_unmatured_horizons_remain_unknown_instead_of_false():
    outcome = label_market_outcome(
        OutcomeProvider(points=_points()),
        _seed(),
        date(2021, 6, 30),
    )
    assert outcome.same_security_active_12m is None
    assert outcome.adjusted_price_multiple_12m is None
    assert outcome.same_security_active_3y is None
    assert outcome.adjusted_price_multiple_3y is None
    assert outcome.max_adjusted_price_multiple_within_3y is None
    assert any("+12m" in warning for warning in outcome.warnings)
    assert any("+3y" in warning for warning in outcome.warnings)


def test_active_security_without_post_horizon_price_keeps_return_unresolved():
    points = (
        PricePoint("sec-1", "OLD", date(2020, 12, 31), 5.0, True),
        PricePoint("sec-1", "NEW", date(2021, 10, 1), 9.0, True),
    )
    outcome = label_market_outcome(
        OutcomeProvider(points=points),
        _seed(),
        date(2022, 3, 1),
        config=MarketOutcomeConfig(horizon_grace_days=31),
    )
    assert outcome.same_security_active_12m is True
    assert outcome.adjusted_price_12m is None
    assert outcome.adjusted_price_multiple_12m is None
    assert any("active at +12m" in warning for warning in outcome.warnings)


def test_survivorship_unsafe_provider_is_refused_for_outcome_membership():
    provider = OutcomeProvider(points=_points())
    provider.survivorship_safe_universe = False
    with pytest.raises(ValueError, match="survivorship-unsafe"):
        label_market_outcome(provider, _seed(), date(2024, 1, 31))


def test_outcome_cohort_reports_coverage_without_rebranding_membership_as_survival():
    cohort = label_market_outcome_cohort(
        OutcomeProvider(points=_points()),
        (_seed(),),
        date(2024, 1, 31),
    )
    assert cohort.case_count == 1
    assert cohort.resolved_12m_membership_count == 1
    assert cohort.active_12m_count == 1
    assert cohort.resolved_3y_membership_count == 1
    assert cohort.active_3y_count == 1
    assert cohort.resolved_3y_endpoint_multiple_count == 1
    assert cohort.resolved_3y_max_multiple_count == 1
    assert any("not a corporate-survival" in item for item in cohort.semantics)

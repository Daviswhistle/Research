from __future__ import annotations

from datetime import date

import pytest

from distressed_equity.credit_replay import JointDistressSeed, JointReplayRun
from distressed_equity.market import PricePoint, SecurityIdentity
from distressed_equity.market_base_rates import compare_market_outcome_base_rates
from distressed_equity.market_outcomes import MarketObservableOutcome, MarketOutcomeCohort
from distressed_equity.replay import MarketDistressSeed


def _equity(security_id: str, symbol: str):
    security = SecurityIdentity(
        security_id=security_id,
        symbol=symbol,
        name=symbol,
        start_date=date(2010, 1, 1),
        provider="csv",
    )
    current = PricePoint(security_id, symbol, date(2020, 12, 31), 5.0, True)
    return MarketDistressSeed(
        security=security,
        analysis_date=date(2020, 12, 31),
        raw_price=PricePoint(security_id, symbol, date(2020, 12, 31), 5.0, False),
        adjusted_price=current,
        peak_adjusted_price=PricePoint(security_id, symbol, date(2019, 12, 31), 25.0, True),
        adjusted_drawdown_from_peak=0.8,
        lookback_start=date(2015, 12, 29),
    )


def _joint_case(security_id: str, symbol: str, *, fresh: int, stress: bool):
    return JointDistressSeed(
        equity=_equity(security_id, symbol),
        linked_credit_instrument_count=max(fresh, 1),
        observed_credit_instrument_count=max(fresh, 1),
        fresh_credit_instrument_count=fresh,
        stale_credit_instrument_count=0 if fresh else 1,
        min_fresh_price_pct_par=50.0 if stress and fresh else None,
        max_fresh_yield_pct=20.0 if stress and fresh else None,
        max_fresh_spread_bps=1500.0 if stress and fresh else None,
        price_stress_instrument_count=1 if stress and fresh else 0,
        yield_stress_instrument_count=1 if stress and fresh else 0,
        spread_stress_instrument_count=1 if stress and fresh else 0,
        any_credit_stress=stress and fresh > 0,
        credit_evidence=(),
        warnings=(),
    )


def _outcome(
    security_id: str,
    symbol: str,
    *,
    active12: bool | None,
    active3: bool | None,
    endpoint3: float | None,
    max3: float | None,
):
    return MarketObservableOutcome(
        security_id=security_id,
        symbol_at_distress=symbol,
        analysis_date=date(2020, 12, 31),
        outcome_cutoff=date(2024, 1, 31),
        same_security_active_12m=active12,
        adjusted_price_12m=None,
        adjusted_price_multiple_12m=None,
        same_security_active_3y=active3,
        adjusted_price_3y=None,
        adjusted_price_multiple_3y=endpoint3,
        max_adjusted_price_within_3y=None,
        max_adjusted_price_multiple_within_3y=max3,
        warnings=(),
    )


def _runs():
    joint_cases = (
        _joint_case("s1", "A", fresh=1, stress=True),
        _joint_case("s2", "B", fresh=1, stress=True),
        _joint_case("s3", "C", fresh=1, stress=False),
        _joint_case("s4", "D", fresh=0, stress=False),
    )
    joint = JointReplayRun(
        equity_provider="csv",
        credit_provider="csv_bond_market",
        analysis_date=date(2020, 12, 31),
        equity_candidate_count=4,
        candidates_with_credit_links=4,
        candidates_with_fresh_credit=3,
        candidates_with_credit_stress=2,
        candidates=joint_cases,
        warnings=(),
    )
    outcomes = MarketOutcomeCohort(
        provider="csv",
        outcome_cutoff=date(2024, 1, 31),
        case_count=4,
        resolved_12m_membership_count=4,
        active_12m_count=3,
        resolved_3y_membership_count=4,
        active_3y_count=3,
        resolved_3y_endpoint_multiple_count=3,
        resolved_3y_max_multiple_count=4,
        outcomes=(
            _outcome("s1", "A", active12=True, active3=True, endpoint3=4.0, max3=5.0),
            _outcome("s2", "B", active12=False, active3=False, endpoint3=None, max3=2.0),
            _outcome("s3", "C", active12=True, active3=True, endpoint3=2.0, max3=3.5),
            _outcome("s4", "D", active12=True, active3=True, endpoint3=1.0, max3=1.5),
        ),
        semantics=(),
    )
    return joint, outcomes


def test_market_base_rates_keep_credit_coverage_groups_separate():
    joint, outcomes = _runs()
    comparison = compare_market_outcome_base_rates(joint, outcomes)
    groups = {group.group: group for group in comparison.groups}

    stress = groups["fresh_credit_stress"]
    assert stress.case_count == 2
    assert stress.resolved_12m_membership_count == 2
    assert stress.same_security_active_12m_rate == 0.5
    assert stress.same_security_active_3y_rate == 0.5
    assert stress.resolved_3y_endpoint_multiple_count == 1
    assert stress.three_x_3y_endpoint_rate == 1.0
    assert stress.median_3y_endpoint_multiple == 4.0
    assert stress.resolved_3y_max_multiple_count == 2
    assert stress.three_x_observed_within_3y_rate == 0.5
    assert stress.median_max_observed_multiple_within_3y == 3.5

    no_stress = groups["fresh_credit_no_stress"]
    assert no_stress.case_count == 1
    assert no_stress.same_security_active_12m_rate == 1.0
    assert no_stress.three_x_3y_endpoint_rate == 0.0
    assert no_stress.three_x_observed_within_3y_rate == 1.0

    no_fresh = groups["no_fresh_credit"]
    assert no_fresh.case_count == 1
    assert no_fresh.same_security_active_12m_rate == 1.0
    assert no_fresh.three_x_3y_endpoint_rate == 0.0
    assert any("coverage/liquidity" in item for item in comparison.semantics)


def test_market_base_rate_comparison_requires_exact_same_case_set():
    joint, outcomes = _runs()
    mismatched = MarketOutcomeCohort(
        provider=outcomes.provider,
        outcome_cutoff=outcomes.outcome_cutoff,
        case_count=3,
        resolved_12m_membership_count=3,
        active_12m_count=2,
        resolved_3y_membership_count=3,
        active_3y_count=2,
        resolved_3y_endpoint_multiple_count=2,
        resolved_3y_max_multiple_count=3,
        outcomes=outcomes.outcomes[:-1],
        semantics=(),
    )
    with pytest.raises(ValueError, match="same case set"):
        compare_market_outcome_base_rates(joint, mismatched)

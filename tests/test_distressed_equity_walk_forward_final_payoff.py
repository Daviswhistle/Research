from __future__ import annotations

from datetime import date
from pathlib import Path

from distressed_equity.credit_replay import JointDistressSeed, JointReplayRun
from distressed_equity.market import PricePoint, SecurityIdentity
from distressed_equity.replay import MarketDistressSeed
from distressed_equity.source_outcomes import CsvSourceBackedOutcomeIndex
from distressed_equity.survival_features import CsvSurvivalFeatureIndex
from distressed_equity.walk_forward_priors import build_walk_forward_priors


def _case(security_id: str, when: date) -> JointDistressSeed:
    security = SecurityIdentity(
        security_id=security_id,
        symbol=security_id,
        name=security_id,
        start_date=date(2000, 1, 1),
        provider="csv",
    )
    equity = MarketDistressSeed(
        security=security,
        analysis_date=when,
        raw_price=PricePoint(security_id, security_id, when, 5.0, False),
        adjusted_price=PricePoint(security_id, security_id, when, 5.0, True),
        peak_adjusted_price=PricePoint(security_id, security_id, date(when.year - 1, 12, 31), 25.0, True),
        adjusted_drawdown_from_peak=0.8,
        lookback_start=date(when.year - 5, 1, 1),
    )
    return JointDistressSeed(
        equity=equity,
        linked_credit_instrument_count=1,
        observed_credit_instrument_count=1,
        fresh_credit_instrument_count=1,
        stale_credit_instrument_count=0,
        min_fresh_price_pct_par=55.0,
        max_fresh_yield_pct=20.0,
        max_fresh_spread_bps=1500.0,
        price_stress_instrument_count=1,
        yield_stress_instrument_count=1,
        spread_stress_instrument_count=1,
        any_credit_stress=True,
        credit_evidence=(),
        warnings=(),
    )


def _run(when: date, *cases: JointDistressSeed) -> JointReplayRun:
    return JointReplayRun(
        equity_provider="csv",
        credit_provider="csv_bond_market",
        analysis_date=when,
        equity_candidate_count=len(cases),
        candidates_with_credit_links=len(cases),
        candidates_with_fresh_credit=len(cases),
        candidates_with_credit_stress=len(cases),
        candidates=tuple(cases),
        warnings=(),
    )


def test_final_payoff_known_before_three_year_horizon_can_enter_later_walk_forward_prior(tmp_path: Path):
    outcomes = tmp_path / "outcomes.csv"
    outcomes.write_text(
        "security_id,ticker,analysis_date,outcome_known_date,survived_12m,existing_common_survived_12m,"
        "normalized_within_3y,equity_multiple_3y,equity_multiple_3y_known_date,equity_multiple_3y_evidence_refs,"
        "final_shareholder_payoff_event_type,final_shareholder_payoff_event_date,final_shareholder_payoff_known_date,"
        "final_shareholder_payoff_multiple,final_shareholder_payoff_evidence_refs,evidence_refs,verification_status,"
        "verifier,verified_on,evidence_reopened\n"
        "A,A,2020-12-31,2021-06-30,,,,4.0,2021-06-30,SEC:A:PAYOFF,"
        "fixed_cash_acquisition_closed,2021-06-15,2021-06-30,4.0,SEC:A:PAYOFF,SEC:A:PAYOFF,"
        "verified,reviewer,2026-09-17,true\n",
        encoding="utf-8",
    )
    features = tmp_path / "features.csv"
    features.write_text(
        "security_id,analysis_date,evidence_known_date,liquidity_runway_months,nearest_maturity_months,"
        "covenant_headroom_pct,net_leverage,impairment_type,evidence_refs,verification_status,verifier,verified_on,"
        "evidence_reopened,notes\n",
        encoding="utf-8",
    )

    historical = _run(date(2020, 12, 31), _case("A", date(2020, 12, 31)))
    target = _run(date(2022, 12, 31), _case("B", date(2022, 12, 31)))
    prior_run = build_walk_forward_priors(
        target,
        (historical,),
        CsvSourceBackedOutcomeIndex(outcomes),
        CsvSurvivalFeatureIndex(features),
        dimensions=(),
    )

    assert prior_run.source_labels_known_by_target == 1
    prior = prior_run.priors[0].credit_group_prior
    assert prior.calibration_case_count == 1
    assert prior.source_labeled_case_count == 1
    assert prior.three_x_3y.resolved_n == 1
    assert prior.three_x_3y.successes == 1
    assert prior.three_x_3y.rate == 1.0

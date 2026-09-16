from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from distressed_equity.credit_replay import JointDistressSeed, JointReplayRun
from distressed_equity.market import PricePoint, SecurityIdentity
from distressed_equity.replay import MarketDistressSeed
from distressed_equity.source_outcomes import CsvSourceBackedOutcomeIndex
from distressed_equity.survival_features import CsvSurvivalFeatureIndex
from distressed_equity.walk_forward_priors import build_walk_forward_priors, walk_forward_prior_to_dict


FEATURE_FIELDS = [
    "security_id", "analysis_date", "evidence_known_date",
    "liquidity_runway_months", "nearest_maturity_months", "covenant_headroom_pct", "net_leverage",
    "impairment_type", "evidence_refs", "verification_status", "verifier", "verified_on",
    "evidence_reopened", "notes",
]

OUTCOME_FIELDS = [
    "security_id", "ticker", "analysis_date", "outcome_known_date",
    "survived_12m", "existing_common_survived_12m", "normalized_within_3y", "equity_multiple_3y",
    "industry_group", "impairment_type", "leverage_bucket", "evidence_refs",
    "verification_status", "verifier", "verified_on", "evidence_reopened", "notes",
]


def _write(path: Path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(",".join(fields) + "\n")
        for row in rows:
            handle.write(",".join(str(row.get(field, "")) for field in fields) + "\n")


def _joint_case(security_id: str, symbol: str, analysis_date: date, *, stress=True, fresh=1):
    security = SecurityIdentity(
        security_id=security_id,
        symbol=symbol,
        name=symbol,
        start_date=date(2000, 1, 1),
        provider="csv",
    )
    equity = MarketDistressSeed(
        security=security,
        analysis_date=analysis_date,
        raw_price=PricePoint(security_id, symbol, analysis_date, 5.0, False),
        adjusted_price=PricePoint(security_id, symbol, analysis_date, 5.0, True),
        peak_adjusted_price=PricePoint(security_id, symbol, date(analysis_date.year - 1, 12, 31), 25.0, True),
        adjusted_drawdown_from_peak=0.8,
        lookback_start=date(analysis_date.year - 5, 1, 1),
    )
    return JointDistressSeed(
        equity=equity,
        linked_credit_instrument_count=max(1, fresh),
        observed_credit_instrument_count=max(1, fresh),
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


def _run(analysis_date: date, *cases: JointDistressSeed):
    return JointReplayRun(
        equity_provider="csv",
        credit_provider="csv_bond_market",
        analysis_date=analysis_date,
        equity_candidate_count=len(cases),
        candidates_with_credit_links=len(cases),
        candidates_with_fresh_credit=sum(item.fresh_credit_instrument_count > 0 for item in cases),
        candidates_with_credit_stress=sum(item.any_credit_stress for item in cases),
        candidates=tuple(cases),
        warnings=(),
    )


def _feature(security_id: str, analysis_date: date, liquidity: float):
    return {
        "security_id": security_id,
        "analysis_date": analysis_date.isoformat(),
        "evidence_known_date": analysis_date.isoformat(),
        "liquidity_runway_months": str(liquidity),
        "nearest_maturity_months": "30",
        "covenant_headroom_pct": "15",
        "net_leverage": "5",
        "impairment_type": "temporary",
        "evidence_refs": f"SEC:{security_id}:{analysis_date}",
        "verification_status": "verified",
        "verifier": "reviewer",
        "verified_on": "2026-09-16",
        "evidence_reopened": "true",
        "notes": "",
    }


def _outcome(
    security_id: str,
    symbol: str,
    analysis_date: date,
    known: date,
    *,
    survived: bool,
    common: bool,
    normalized: bool,
    multiple: str = "",
):
    return {
        "security_id": security_id,
        "ticker": symbol,
        "analysis_date": analysis_date.isoformat(),
        "outcome_known_date": known.isoformat(),
        "survived_12m": str(survived).lower(),
        "existing_common_survived_12m": str(common).lower(),
        "normalized_within_3y": str(normalized).lower(),
        "equity_multiple_3y": multiple,
        "industry_group": "x",
        "impairment_type": "temporary",
        "leverage_bucket": "high",
        "evidence_refs": f"SEC:{security_id}:outcome",
        "verification_status": "verified",
        "verifier": "reviewer",
        "verified_on": date(2026, 9, 16).isoformat(),
        "evidence_reopened": "true",
        "notes": "",
    }


def test_walk_forward_prior_uses_only_historical_known_outcomes_and_deduped_episodes(tmp_path):
    a_2018 = _joint_case("A", "A", date(2018, 12, 31))
    a_2019 = _joint_case("A", "A", date(2019, 12, 31))  # same security, within 365d episode gap
    b_2019 = _joint_case("B", "B", date(2019, 12, 31))
    x_2021 = _joint_case("X", "X", date(2021, 12, 31))
    target_c = _joint_case("C", "C", date(2022, 12, 31))
    target_x = _joint_case("X", "X", date(2022, 12, 31))  # same ongoing episode as X-2021

    history = (
        _run(date(2018, 12, 31), a_2018),
        _run(date(2019, 12, 31), a_2019, b_2019),
        _run(date(2021, 12, 31), x_2021),
    )
    target = _run(date(2022, 12, 31), target_c, target_x)

    feature_path = tmp_path / "features.csv"
    _write(feature_path, FEATURE_FIELDS, [
        _feature("A", date(2018, 12, 31), 18),
        _feature("A", date(2019, 12, 31), 18),
        _feature("B", date(2019, 12, 31), 5),
        _feature("X", date(2021, 12, 31), 18),
        _feature("C", date(2022, 12, 31), 18),
        _feature("X", date(2022, 12, 31), 18),
    ])

    outcome_path = tmp_path / "outcomes.csv"
    _write(outcome_path, OUTCOME_FIELDS, [
        _outcome("A", "A", date(2018, 12, 31), date(2021, 12, 31), survived=True, common=True, normalized=True, multiple="4"),
        # Duplicate A-2019 exists in raw historical runs but its entire episode is collapsed before calibration.
        _outcome("A", "A", date(2019, 12, 31), date(2022, 1, 31), survived=False, common=False, normalized=False, multiple=""),
        _outcome("B", "B", date(2019, 12, 31), date(2021, 12, 31), survived=False, common=False, normalized=False, multiple=""),
        _outcome("X", "X", date(2021, 12, 31), date(2022, 12, 31), survived=False, common=False, normalized=False, multiple=""),
    ])

    priors = build_walk_forward_priors(
        target,
        history,
        CsvSourceBackedOutcomeIndex(outcome_path),
        CsvSurvivalFeatureIndex(feature_path),
        dimensions=("liquidity_runway",),
        episode_gap_days=365,
        small_sample_n=30,
    )
    assert priors.target_analysis_date == date(2022, 12, 31)
    assert priors.historical_case_count_before_episode_dedup == 4
    assert priors.historical_case_count_after_episode_dedup == 3
    assert priors.source_labels_known_by_target == 3

    by_security = {item.security_id: item for item in priors.priors}
    c = by_security["C"]
    # C can use A-2018, B-2019 and X-2021 because all are prior cases with labels known by C's T0.
    assert c.credit_group_prior.calibration_case_count == 3
    assert c.credit_group_prior.survived_12m.resolved_n == 3
    assert c.credit_group_prior.survived_12m.successes == 1
    assert c.credit_group_prior.survived_12m.rate == pytest.approx(1 / 3)
    assert c.credit_group_prior.survived_12m.small_sample is True

    c_liquidity = c.feature_priors[0]
    assert c_liquidity.basis == "liquidity_runway"
    assert c_liquidity.bucket == "12-<24m"
    assert c_liquidity.calibration_case_count == 2  # A-2018 + X-2021
    assert c_liquidity.survived_12m.resolved_n == 2
    assert c_liquidity.survived_12m.successes == 1
    assert c_liquidity.survived_12m.rate == 0.5

    x = by_security["X"]
    # X-2021 is excluded from X-2022 prior because it is the same security within the episode gap.
    assert x.credit_group_prior.calibration_case_count == 2
    assert "X|2021-12-31" not in x.credit_group_prior.case_keys
    assert x.feature_priors[0].calibration_case_count == 1  # only A-2018 matches 12-24m after X exclusion
    assert x.feature_priors[0].survived_12m.rate == 1.0

    payload = walk_forward_prior_to_dict(priors)
    c_payload = next(item for item in payload["priors"] if item["security_id"] == "C")
    diagnostic = c_payload["credit_group_prior"]["survived_12m"]
    assert diagnostic["resolved_n"] == 3
    assert diagnostic["successes"] == 1
    assert diagnostic["wilson_95_low"] < diagnostic["rate"] < diagnostic["wilson_95_high"]
    assert any("target-cohort outcomes are never used" in item for item in payload["semantics"])


def test_outcome_known_after_target_t0_is_not_available_to_prior(tmp_path):
    historical = _joint_case("A", "A", date(2019, 12, 31))
    target_case = _joint_case("B", "B", date(2022, 12, 31))
    history = (_run(date(2019, 12, 31), historical),)
    target = _run(date(2022, 12, 31), target_case)

    feature_path = tmp_path / "features.csv"
    _write(feature_path, FEATURE_FIELDS, [
        _feature("A", date(2019, 12, 31), 18),
        _feature("B", date(2022, 12, 31), 18),
    ])
    outcome_path = tmp_path / "outcomes.csv"
    _write(outcome_path, OUTCOME_FIELDS, [
        _outcome("A", "A", date(2019, 12, 31), date(2023, 1, 1), survived=True, common=True, normalized=True, multiple="4"),
    ])

    priors = build_walk_forward_priors(
        target,
        history,
        CsvSourceBackedOutcomeIndex(outcome_path),
        CsvSurvivalFeatureIndex(feature_path),
        dimensions=("liquidity_runway",),
    )
    prior = priors.priors[0].credit_group_prior
    assert prior.calibration_case_count == 1
    assert prior.source_labeled_case_count == 0
    assert prior.survived_12m.resolved_n == 0
    assert prior.survived_12m.rate is None


def test_historical_runs_must_strictly_precede_target_date(tmp_path):
    target_date = date(2022, 12, 31)
    target = _run(target_date, _joint_case("B", "B", target_date))
    invalid_history = (_run(target_date, _joint_case("A", "A", target_date)),)

    feature_path = tmp_path / "features.csv"
    _write(feature_path, FEATURE_FIELDS, [_feature("B", target_date, 18)])
    outcome_path = tmp_path / "outcomes.csv"
    _write(outcome_path, OUTCOME_FIELDS, [])

    with pytest.raises(ValueError, match="strictly precede target analysis date"):
        build_walk_forward_priors(
            target,
            invalid_history,
            CsvSourceBackedOutcomeIndex(outcome_path),
            CsvSurvivalFeatureIndex(feature_path),
            dimensions=("liquidity_runway",),
        )

from __future__ import annotations

from datetime import date
from pathlib import Path

from distressed_equity.credit_replay import JointDistressSeed, JointReplayRun
from distressed_equity.market import PricePoint, SecurityIdentity
from distressed_equity.replay import MarketDistressSeed
from distressed_equity.source_outcomes import CsvSourceBackedOutcomeIndex
from distressed_equity.survival_features import CsvSurvivalFeatureIndex
from distressed_equity.walk_forward_priors import build_walk_forward_priors


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


def _write(path: Path, fields, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(",".join(fields) + "\n")
        for row in rows:
            handle.write(",".join(str(row.get(field, "")) for field in fields) + "\n")


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
        peak_adjusted_price=PricePoint(security_id, security_id, date(when.year - 1, 1, 1), 25.0, True),
        adjusted_drawdown_from_peak=0.8,
        lookback_start=date(when.year - 5, 1, 1),
    )
    return JointDistressSeed(
        equity=equity,
        linked_credit_instrument_count=1,
        observed_credit_instrument_count=1,
        fresh_credit_instrument_count=1,
        stale_credit_instrument_count=0,
        min_fresh_price_pct_par=50.0,
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


def _feature(security_id: str, when: date, liquidity: float) -> dict[str, str]:
    return {
        "security_id": security_id,
        "analysis_date": when.isoformat(),
        "evidence_known_date": when.isoformat(),
        "liquidity_runway_months": str(liquidity),
        "nearest_maturity_months": "30",
        "covenant_headroom_pct": "15",
        "net_leverage": "5",
        "impairment_type": "temporary",
        "evidence_refs": f"SEC:{security_id}:{when}",
        "verification_status": "verified",
        "verifier": "reviewer",
        "verified_on": "2026-09-16",
        "evidence_reopened": "true",
        "notes": "",
    }


def _outcome(security_id: str, when: date, known: date, survived: bool) -> dict[str, str]:
    return {
        "security_id": security_id,
        "ticker": security_id,
        "analysis_date": when.isoformat(),
        "outcome_known_date": known.isoformat(),
        "survived_12m": str(survived).lower(),
        "existing_common_survived_12m": str(survived).lower(),
        "normalized_within_3y": "",
        "equity_multiple_3y": "",
        "industry_group": "x",
        "impairment_type": "temporary",
        "leverage_bucket": "high",
        "evidence_refs": f"SEC:{security_id}:OUTCOME",
        "verification_status": "verified",
        "verifier": "reviewer",
        "verified_on": "2026-09-16",
        "evidence_reopened": "true",
        "notes": "",
    }


def test_connected_observation_chain_is_one_episode_and_excluded_from_same_security_target(tmp_path: Path):
    b = _case("B", date(2017, 1, 1))
    a1 = _case("A", date(2018, 1, 1))
    a2 = _case("A", date(2018, 9, 1))
    a3 = _case("A", date(2019, 5, 1))
    target_a = _case("A", date(2020, 1, 1))

    history = (
        _run(date(2017, 1, 1), b),
        _run(date(2018, 1, 1), a1),
        _run(date(2018, 9, 1), a2),
        _run(date(2019, 5, 1), a3),
    )
    target = _run(date(2020, 1, 1), target_a)

    features_path = tmp_path / "features.csv"
    _write(features_path, FEATURE_FIELDS, [
        _feature("B", date(2017, 1, 1), 5),
        _feature("A", date(2018, 1, 1), 18),
        _feature("A", date(2020, 1, 1), 18),
    ])
    outcomes_path = tmp_path / "outcomes.csv"
    _write(outcomes_path, OUTCOME_FIELDS, [
        _outcome("B", date(2017, 1, 1), date(2018, 1, 1), False),
        _outcome("A", date(2018, 1, 1), date(2019, 1, 1), True),
    ])

    priors = build_walk_forward_priors(
        target,
        history,
        CsvSourceBackedOutcomeIndex(outcomes_path),
        CsvSurvivalFeatureIndex(features_path),
        dimensions=("liquidity_runway",),
        episode_gap_days=300,
    )

    assert priors.historical_case_count_before_episode_dedup == 4
    assert priors.historical_case_count_after_episode_dedup == 2
    prior = priors.priors[0].credit_group_prior
    # A's connected 2018-01 -> 2018-09 -> 2019-05 episode ends only 245 days
    # before target T0, so the entire A episode is excluded. Only B calibrates A's prior.
    assert prior.calibration_case_count == 1
    assert prior.case_keys == ("B|2017-01-01",)
    assert prior.survived_12m.resolved_n == 1
    assert prior.survived_12m.successes == 0
    assert prior.survived_12m.rate == 0.0

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from distressed_equity.credit_replay import JointDistressSeed, JointReplayRun
from distressed_equity.market import PricePoint, SecurityIdentity
from distressed_equity.replay import MarketDistressSeed
from distressed_equity.source_outcomes import CsvSourceBackedOutcomeIndex
from distressed_equity.survival_features import (
    CsvSurvivalFeatureIndex,
    compare_feature_strata_base_rates,
    covenant_headroom_bucket,
    liquidity_runway_bucket,
    nearest_maturity_bucket,
    net_leverage_bucket,
)


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


def _feature(security_id="s1", **updates):
    row = {
        "security_id": security_id,
        "analysis_date": "2020-12-31",
        "evidence_known_date": "2020-12-30",
        "liquidity_runway_months": "5",
        "nearest_maturity_months": "9",
        "covenant_headroom_pct": "-1",
        "net_leverage": "6.5",
        "impairment_type": "temporary",
        "evidence_refs": "SEC:10-Q:1;CREDIT:1",
        "verification_status": "verified",
        "verifier": "reviewer",
        "verified_on": "2026-09-16",
        "evidence_reopened": "true",
        "notes": "verified T0 features",
    }
    row.update(updates)
    return row


def _outcome(security_id="s1", ticker="A", **updates):
    row = {
        "security_id": security_id,
        "ticker": ticker,
        "analysis_date": "2020-12-31",
        "outcome_known_date": "2024-01-31",
        "survived_12m": "true",
        "existing_common_survived_12m": "true",
        "normalized_within_3y": "true",
        "equity_multiple_3y": "4",
        "industry_group": "x",
        "impairment_type": "temporary",
        "leverage_bucket": "high",
        "evidence_refs": "SEC:8-K:1",
        "verification_status": "verified",
        "verifier": "reviewer",
        "verified_on": "2024-02-01",
        "evidence_reopened": "true",
        "notes": "outcome",
    }
    row.update(updates)
    return row


def _joint_case(security_id: str, symbol: str, *, stress: bool, fresh: int = 1):
    security = SecurityIdentity(
        security_id=security_id,
        symbol=symbol,
        name=symbol,
        start_date=date(2010, 1, 1),
        provider="csv",
    )
    equity = MarketDistressSeed(
        security=security,
        analysis_date=date(2020, 12, 31),
        raw_price=PricePoint(security_id, symbol, date(2020, 12, 31), 5.0, False),
        adjusted_price=PricePoint(security_id, symbol, date(2020, 12, 31), 5.0, True),
        peak_adjusted_price=PricePoint(security_id, symbol, date(2019, 12, 31), 25.0, True),
        adjusted_drawdown_from_peak=0.8,
        lookback_start=date(2015, 12, 29),
    )
    return JointDistressSeed(
        equity=equity,
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


def _joint():
    cases = (
        _joint_case("s1", "A", stress=True),
        _joint_case("s2", "B", stress=True),
        _joint_case("s3", "C", stress=False),
        _joint_case("s4", "D", stress=False, fresh=0),
    )
    return JointReplayRun(
        equity_provider="csv",
        credit_provider="csv_bond_market",
        analysis_date=date(2020, 12, 31),
        equity_candidate_count=4,
        candidates_with_credit_links=4,
        candidates_with_fresh_credit=3,
        candidates_with_credit_stress=2,
        candidates=cases,
        warnings=(),
    )


def test_feature_snapshot_refuses_future_evidence_and_unverified_rows(tmp_path):
    path = tmp_path / "features.csv"
    _write(path, FEATURE_FIELDS, [_feature(evidence_known_date="2021-01-01")])
    with pytest.raises(ValueError, match="evidence_known_date is after analysis_date"):
        CsvSurvivalFeatureIndex(path)

    _write(path, FEATURE_FIELDS, [_feature(verification_status="unverified")])
    with pytest.raises(ValueError, match="verification_status must be verified"):
        CsvSurvivalFeatureIndex(path)

    _write(path, FEATURE_FIELDS, [_feature(evidence_reopened="false")])
    with pytest.raises(ValueError, match="evidence_reopened must be true"):
        CsvSurvivalFeatureIndex(path)

    _write(path, FEATURE_FIELDS, [_feature(evidence_refs="")])
    with pytest.raises(ValueError, match="evidence_refs are required"):
        CsvSurvivalFeatureIndex(path)


def test_feature_numeric_validation_and_unknown_preservation(tmp_path):
    path = tmp_path / "features.csv"
    _write(path, FEATURE_FIELDS, [_feature(liquidity_runway_months="-1")])
    with pytest.raises(ValueError, match="liquidity_runway_months must be non-negative"):
        CsvSurvivalFeatureIndex(path)

    _write(path, FEATURE_FIELDS, [_feature(
        liquidity_runway_months="",
        nearest_maturity_months="",
        covenant_headroom_pct="",
        net_leverage="",
        impairment_type="temporary",
    )])
    snapshot = CsvSurvivalFeatureIndex(path).get("s1", date(2020, 12, 31))
    assert snapshot is not None
    assert snapshot.liquidity_runway_months is None


def test_bucket_boundaries_are_deterministic_descriptive_bins():
    assert liquidity_runway_bucket(None) == "unknown"
    assert liquidity_runway_bucket(5.99) == "<6m"
    assert liquidity_runway_bucket(6) == "6-<12m"
    assert liquidity_runway_bucket(12) == "12-<24m"
    assert liquidity_runway_bucket(24) == ">=24m"

    assert nearest_maturity_bucket(11.9) == "<12m"
    assert nearest_maturity_bucket(12) == "12-<24m"
    assert nearest_maturity_bucket(24) == "24-<36m"
    assert nearest_maturity_bucket(36) == ">=36m"

    assert covenant_headroom_bucket(-0.1) == "breach/<0%"
    assert covenant_headroom_bucket(0) == "0-<10%"
    assert covenant_headroom_bucket(10) == "10-<25%"
    assert covenant_headroom_bucket(25) == ">=25%"

    assert net_leverage_bucket(-1) == "<2x"
    assert net_leverage_bucket(2) == "2-<4x"
    assert net_leverage_bucket(4) == "4-<6x"
    assert net_leverage_bucket(6) == ">=6x"


def test_feature_strata_cross_credit_group_with_source_backed_outcomes(tmp_path):
    feature_path = tmp_path / "features.csv"
    outcome_path = tmp_path / "outcomes.csv"
    _write(feature_path, FEATURE_FIELDS, [
        _feature("s1", liquidity_runway_months="5", nearest_maturity_months="9", covenant_headroom_pct="-1", net_leverage="7", impairment_type="temporary"),
        _feature("s2", liquidity_runway_months="18", nearest_maturity_months="30", covenant_headroom_pct="15", net_leverage="5", impairment_type="permanent"),
        _feature("s3", liquidity_runway_months="30", nearest_maturity_months="48", covenant_headroom_pct="40", net_leverage="3", impairment_type="temporary"),
        # s4 intentionally has no feature snapshot -> unknown buckets.
    ])
    _write(outcome_path, OUTCOME_FIELDS, [
        _outcome("s1", "A", survived_12m="false", existing_common_survived_12m="false", normalized_within_3y="false", equity_multiple_3y="0"),
        _outcome("s2", "B", survived_12m="true", existing_common_survived_12m="true", normalized_within_3y="true", equity_multiple_3y="4"),
        _outcome("s3", "C", survived_12m="true", existing_common_survived_12m="true", normalized_within_3y="true", equity_multiple_3y="3"),
    ])

    comparison = compare_feature_strata_base_rates(
        _joint(),
        CsvSurvivalFeatureIndex(feature_path),
        CsvSourceBackedOutcomeIndex(outcome_path),
        date(2026, 1, 1),
    )
    assert comparison.cohort_case_count == 4
    assert comparison.feature_snapshot_count == 3
    assert comparison.known_source_label_count == 3

    strata = {(item.dimension, item.bucket, item.credit_group): item for item in comparison.strata}
    short = strata[("liquidity_runway", "<6m", "fresh_credit_stress")]
    assert short.cohort_case_count == 1
    assert short.source_labeled_case_count == 1
    assert short.survived_12m_rate == 0.0
    assert short.existing_common_survival_rate == 0.0
    assert short.three_x_3y_rate == 0.0

    medium = strata[("liquidity_runway", "12-<24m", "fresh_credit_stress")]
    assert medium.survived_12m_rate == 1.0
    assert medium.existing_common_survival_rate == 1.0
    assert medium.three_x_3y_rate == 1.0

    unknown = strata[("liquidity_runway", "unknown", "no_fresh_credit")]
    assert unknown.cohort_case_count == 1
    assert unknown.feature_snapshot_count == 0
    assert unknown.source_labeled_case_count == 0
    assert unknown.survived_12m_rate is None

    breach = strata[("covenant_headroom", "breach/<0%", "fresh_credit_stress")]
    assert breach.survived_12m_rate == 0.0
    assert any("not imply universal causal thresholds" in item for item in comparison.semantics)


def test_feature_strata_reject_duplicate_dimensions(tmp_path):
    feature_path = tmp_path / "features.csv"
    outcome_path = tmp_path / "outcomes.csv"
    _write(feature_path, FEATURE_FIELDS, [_feature()])
    _write(outcome_path, OUTCOME_FIELDS, [_outcome()])
    with pytest.raises(ValueError, match="dimensions must be unique"):
        compare_feature_strata_base_rates(
            _joint(),
            CsvSurvivalFeatureIndex(feature_path),
            CsvSourceBackedOutcomeIndex(outcome_path),
            date(2026, 1, 1),
            dimensions=("liquidity_runway", "liquidity_runway"),
        )

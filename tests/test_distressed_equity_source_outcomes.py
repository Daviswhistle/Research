from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from distressed_equity.credit_replay import JointDistressSeed, JointReplayRun
from distressed_equity.market import PricePoint, SecurityIdentity
from distressed_equity.replay import MarketDistressSeed
from distressed_equity.source_outcomes import (
    CsvSourceBackedOutcomeIndex,
    compare_source_outcome_base_rates,
)


FIELDS = [
    "security_id", "ticker", "analysis_date", "outcome_known_date",
    "survived_12m", "existing_common_survived_12m", "normalized_within_3y", "equity_multiple_3y",
    "industry_group", "impairment_type", "leverage_bucket", "evidence_refs",
    "verification_status", "verifier", "verified_on", "evidence_reopened", "notes",
]


def _write(path: Path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(",".join(FIELDS) + "\n")
        for row in rows:
            handle.write(",".join(str(row.get(field, "")) for field in FIELDS) + "\n")


def _row(security_id="s1", ticker="A", **updates):
    row = {
        "security_id": security_id,
        "ticker": ticker,
        "analysis_date": "2020-12-31",
        "outcome_known_date": "2024-01-31",
        "survived_12m": "true",
        "existing_common_survived_12m": "true",
        "normalized_within_3y": "true",
        "equity_multiple_3y": "4.0",
        "industry_group": "autos",
        "impairment_type": "temporary",
        "leverage_bucket": "high",
        "evidence_refs": "SEC:8-K:001;COURT:case-1",
        "verification_status": "verified",
        "verifier": "reviewer",
        "verified_on": "2024-02-01",
        "evidence_reopened": "true",
        "notes": "source checked",
    }
    row.update(updates)
    return row


def _joint_case(security_id: str, symbol: str, *, fresh: int, stress: bool):
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
        _joint_case("s1", "A", fresh=1, stress=True),
        _joint_case("s2", "B", fresh=1, stress=False),
        _joint_case("s3", "C", fresh=0, stress=False),
    )
    return JointReplayRun(
        equity_provider="csv",
        credit_provider="csv_bond_market",
        analysis_date=date(2020, 12, 31),
        equity_candidate_count=3,
        candidates_with_credit_links=3,
        candidates_with_fresh_credit=2,
        candidates_with_credit_stress=1,
        candidates=cases,
        warnings=(),
    )


def test_verified_source_outcome_labels_require_reopened_evidence(tmp_path):
    path = tmp_path / "outcomes.csv"
    _write(path, [_row(evidence_reopened="false")])
    with pytest.raises(ValueError, match="evidence_reopened must be true"):
        CsvSourceBackedOutcomeIndex(path)

    _write(path, [_row(evidence_refs="")])
    with pytest.raises(ValueError, match="evidence_refs are required"):
        CsvSourceBackedOutcomeIndex(path)

    _write(path, [_row(verification_status="unverified")])
    with pytest.raises(ValueError, match="verification_status must be verified"):
        CsvSourceBackedOutcomeIndex(path)


def test_12m_and_3y_labels_cannot_be_known_before_their_horizons(tmp_path):
    path = tmp_path / "outcomes.csv"
    _write(path, [_row(outcome_known_date="2021-06-30", verified_on="2021-07-01", equity_multiple_3y="")])
    with pytest.raises(ValueError, match="12m survival labels cannot be known"):
        CsvSourceBackedOutcomeIndex(path)

    _write(path, [_row(
        outcome_known_date="2022-12-31",
        verified_on="2023-01-01",
        survived_12m="",
        existing_common_survived_12m="",
        equity_multiple_3y="4.0",
    )])
    with pytest.raises(ValueError, match="equity_multiple_3y cannot be known"):
        CsvSourceBackedOutcomeIndex(path)


def test_knowledge_cutoff_excludes_future_known_outcomes(tmp_path):
    path = tmp_path / "outcomes.csv"
    _write(path, [
        _row("s1", "A", outcome_known_date="2024-01-31", verified_on="2024-02-01"),
        _row("s2", "B", outcome_known_date="2025-01-31", verified_on="2025-02-01"),
    ])
    index = CsvSourceBackedOutcomeIndex(path)
    known = index.known_labels(date(2024, 12, 31))
    assert [item.security_id for item in known] == ["s1"]


def test_source_backed_base_rates_keep_credit_groups_and_label_coverage_separate(tmp_path):
    path = tmp_path / "outcomes.csv"
    _write(path, [
        _row("s1", "A", survived_12m="true", existing_common_survived_12m="true", normalized_within_3y="true", equity_multiple_3y="4"),
        _row("s2", "B", survived_12m="true", existing_common_survived_12m="false", normalized_within_3y="false", equity_multiple_3y="0.5"),
        # s3 intentionally has no verified source label: it must remain missing, not become failure.
    ])
    comparison = compare_source_outcome_base_rates(
        _joint(), CsvSourceBackedOutcomeIndex(path), date(2026, 1, 1)
    )
    assert comparison.cohort_case_count == 3
    assert comparison.known_source_label_count == 2
    groups = {group.group: group for group in comparison.groups}

    stress = groups["fresh_credit_stress"]
    assert stress.cohort_case_count == 1
    assert stress.source_labeled_case_count == 1
    assert stress.survived_12m_rate == 1.0
    assert stress.existing_common_survival_rate == 1.0
    assert stress.normalized_within_3y_rate == 1.0
    assert stress.three_x_3y_rate == 1.0
    assert stress.median_equity_multiple_3y == 4.0

    no_stress = groups["fresh_credit_no_stress"]
    assert no_stress.source_labeled_case_count == 1
    assert no_stress.survived_12m_rate == 1.0
    assert no_stress.existing_common_survival_rate == 0.0
    assert no_stress.normalized_within_3y_rate == 0.0
    assert no_stress.three_x_3y_rate == 0.0

    no_fresh = groups["no_fresh_credit"]
    assert no_fresh.cohort_case_count == 1
    assert no_fresh.source_labeled_case_count == 0
    assert no_fresh.survived_12m_rate is None
    assert any("missing legal/business outcome coverage" in item for item in comparison.semantics)

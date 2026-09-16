from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from distressed_equity.source_outcomes import CsvSourceBackedOutcomeIndex


FIELDS = [
    "security_id", "ticker", "analysis_date", "outcome_known_date",
    "survived_12m", "survived_12m_known_date", "survived_12m_evidence_refs",
    "existing_common_survived_12m", "existing_common_survived_12m_known_date", "existing_common_survived_12m_evidence_refs",
    "normalized_within_3y", "normalized_within_3y_known_date", "normalized_within_3y_evidence_refs",
    "equity_multiple_3y", "equity_multiple_3y_known_date", "equity_multiple_3y_evidence_refs",
    "company_terminal_event_type", "company_terminal_event_date", "company_terminal_event_known_date", "company_terminal_event_evidence_refs",
    "common_terminal_event_type", "common_terminal_event_date", "common_terminal_event_known_date", "common_terminal_event_evidence_refs",
    "final_shareholder_payoff_event_type", "final_shareholder_payoff_event_date", "final_shareholder_payoff_known_date",
    "final_shareholder_payoff_multiple", "final_shareholder_payoff_evidence_refs",
    "industry_group", "impairment_type", "leverage_bucket", "evidence_refs",
    "verification_status", "verifier", "verified_on", "evidence_reopened", "notes",
]


def _write(path: Path, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(",".join(FIELDS) + "\n")
        for row in rows:
            handle.write(",".join(str(row.get(field, "")) for field in FIELDS) + "\n")


def _base(**updates) -> dict[str, str]:
    row = {
        "security_id": "A",
        "ticker": "A",
        "analysis_date": "2020-12-31",
        "outcome_known_date": "2021-06-30",
        "survived_12m": "",
        "existing_common_survived_12m": "",
        "normalized_within_3y": "",
        "equity_multiple_3y": "2.5",
        "equity_multiple_3y_known_date": "2021-06-30",
        "equity_multiple_3y_evidence_refs": "SEC:PAYOFF",
        "final_shareholder_payoff_event_type": "fixed_cash_acquisition_closed",
        "final_shareholder_payoff_event_date": "2021-06-15",
        "final_shareholder_payoff_known_date": "2021-06-30",
        "final_shareholder_payoff_multiple": "2.5",
        "final_shareholder_payoff_evidence_refs": "SEC:PAYOFF",
        "industry_group": "x",
        "impairment_type": "acquisition",
        "leverage_bucket": "high",
        "evidence_refs": "SEC:PAYOFF",
        "verification_status": "verified",
        "verifier": "reviewer",
        "verified_on": "2026-09-17",
        "evidence_reopened": "true",
        "notes": "later row-level note should be stripped from cutoff-safe view",
    }
    row.update(updates)
    return row


def test_fixed_cash_acquisition_can_lock_three_year_multiple_before_horizon(tmp_path: Path):
    path = tmp_path / "outcomes.csv"
    _write(path, [_base()])

    index = CsvSourceBackedOutcomeIndex(path)
    assert index.known_labels(date(2021, 6, 29)) == ()

    label = index.known_labels(date(2021, 6, 30))[0]
    assert label.equity_multiple_3y == 2.5
    assert label.equity_multiple_3y_known_date == date(2021, 6, 30)
    assert label.equity_multiple_3y_evidence_refs == ("SEC:PAYOFF",)
    assert label.evidence_refs == ("SEC:PAYOFF",)
    assert label.notes is None
    assert label.final_shareholder_payoff_fact is not None
    assert label.final_shareholder_payoff_fact.event_type == "fixed_cash_acquisition_closed"
    assert label.final_shareholder_payoff_fact.event_date == date(2021, 6, 15)
    assert label.final_shareholder_payoff_fact.known_date == date(2021, 6, 30)
    assert label.final_shareholder_payoff_fact.payoff_multiple == 2.5
    assert label.final_shareholder_payoff_fact.evidence_refs == ("SEC:PAYOFF",)


def test_early_three_year_multiple_without_final_payoff_fact_is_rejected(tmp_path: Path):
    path = tmp_path / "outcomes.csv"
    _write(path, [_base(
        final_shareholder_payoff_event_type="",
        final_shareholder_payoff_event_date="",
        final_shareholder_payoff_known_date="",
        final_shareholder_payoff_multiple="",
        final_shareholder_payoff_evidence_refs="",
    )])
    with pytest.raises(ValueError, match=r"equity_multiple_3y cannot be known before the \+3y horizon"):
        CsvSourceBackedOutcomeIndex(path)


def test_final_payoff_multiple_must_match_equity_multiple_metric(tmp_path: Path):
    path = tmp_path / "outcomes.csv"
    _write(path, [_base(final_shareholder_payoff_multiple="2.4")])
    with pytest.raises(ValueError, match="final shareholder payoff multiple does not match equity_multiple_3y"):
        CsvSourceBackedOutcomeIndex(path)


def test_final_payoff_evidence_must_be_in_equity_multiple_evidence(tmp_path: Path):
    path = tmp_path / "outcomes.csv"
    _write(path, [_base(equity_multiple_3y_evidence_refs="SEC:OTHER")])
    with pytest.raises(ValueError, match="equity_multiple_3y evidence_refs must include final-payoff evidence refs"):
        CsvSourceBackedOutcomeIndex(path)


def test_no_distribution_extinguishment_requires_zero_payoff(tmp_path: Path):
    path = tmp_path / "outcomes.csv"
    _write(path, [_base(
        equity_multiple_3y="1.0",
        final_shareholder_payoff_event_type="common_extinguished_no_distribution",
        final_shareholder_payoff_multiple="1.0",
    )])
    with pytest.raises(ValueError, match="common_extinguished_no_distribution requires payoff_multiple=0"):
        CsvSourceBackedOutcomeIndex(path)


def test_no_distribution_extinguishment_can_lock_zero_payoff(tmp_path: Path):
    path = tmp_path / "outcomes.csv"
    _write(path, [_base(
        equity_multiple_3y="0",
        final_shareholder_payoff_event_type="common_extinguished_no_distribution",
        final_shareholder_payoff_multiple="0",
    )])
    label = CsvSourceBackedOutcomeIndex(path).known_labels(date(2021, 7, 1))[0]
    assert label.equity_multiple_3y == 0.0
    assert label.final_shareholder_payoff_fact is not None
    assert label.final_shareholder_payoff_fact.payoff_multiple == 0.0


def test_metric_known_date_cannot_precede_final_payoff_knowledge(tmp_path: Path):
    path = tmp_path / "outcomes.csv"
    _write(path, [_base(
        equity_multiple_3y_known_date="2021-06-20",
        outcome_known_date="2021-06-30",
    )])
    with pytest.raises(ValueError, match="equity_multiple_3y known date precedes final-payoff knowledge"):
        CsvSourceBackedOutcomeIndex(path)


def test_final_payoff_fact_requires_equity_multiple_metric(tmp_path: Path):
    path = tmp_path / "outcomes.csv"
    _write(path, [_base(
        equity_multiple_3y="",
        equity_multiple_3y_known_date="",
        equity_multiple_3y_evidence_refs="",
    )])
    with pytest.raises(ValueError, match="final shareholder payoff fact requires equity_multiple_3y"):
        CsvSourceBackedOutcomeIndex(path)


def test_final_payoff_after_three_year_horizon_cannot_backdate_metric(tmp_path: Path):
    path = tmp_path / "outcomes.csv"
    _write(path, [_base(
        outcome_known_date="2024-02-01",
        equity_multiple_3y_known_date="2023-12-01",
        final_shareholder_payoff_event_date="2024-01-15",
        final_shareholder_payoff_known_date="2024-02-01",
    )])
    with pytest.raises(ValueError, match="equity_multiple_3y known date precedes final-payoff knowledge"):
        CsvSourceBackedOutcomeIndex(path)

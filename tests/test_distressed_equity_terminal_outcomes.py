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
        "survived_12m": "false",
        "survived_12m_known_date": "2021-06-30",
        "survived_12m_evidence_refs": "COURT:LIQ",
        "existing_common_survived_12m": "",
        "normalized_within_3y": "",
        "equity_multiple_3y": "",
        "company_terminal_event_type": "liquidation",
        "company_terminal_event_date": "2021-06-15",
        "company_terminal_event_known_date": "2021-06-30",
        "company_terminal_event_evidence_refs": "COURT:LIQ",
        "industry_group": "x",
        "impairment_type": "terminal",
        "leverage_bucket": "high",
        "evidence_refs": "COURT:LIQ",
        "verification_status": "verified",
        "verifier": "reviewer",
        "verified_on": "2026-09-17",
        "evidence_reopened": "true",
        "notes": "",
    }
    row.update(updates)
    return row


def test_company_terminal_fact_allows_early_company_failure_label(tmp_path: Path):
    path = tmp_path / "outcomes.csv"
    _write(path, [_base()])
    index = CsvSourceBackedOutcomeIndex(path)
    labels = index.known_labels(date(2021, 7, 1))
    assert len(labels) == 1
    label = labels[0]
    assert label.survived_12m is False
    assert label.survived_12m_known_date == date(2021, 6, 30)
    assert label.company_terminal_fact is not None
    assert label.company_terminal_fact.event_type == "liquidation"
    assert label.company_terminal_fact.event_date == date(2021, 6, 15)
    assert label.company_terminal_fact.evidence_refs == ("COURT:LIQ",)


def test_early_company_failure_without_terminal_fact_is_rejected(tmp_path: Path):
    path = tmp_path / "outcomes.csv"
    _write(path, [_base(
        company_terminal_event_type="",
        company_terminal_event_date="",
        company_terminal_event_known_date="",
        company_terminal_event_evidence_refs="",
    )])
    with pytest.raises(ValueError, match="early survived_12m=false requires an explicit verified terminal fact"):
        CsvSourceBackedOutcomeIndex(path)


def test_terminal_evidence_must_be_in_metric_evidence_refs(tmp_path: Path):
    path = tmp_path / "outcomes.csv"
    _write(path, [_base(survived_12m_evidence_refs="SEC:OTHER")])
    with pytest.raises(ValueError, match="survived_12m evidence_refs must include terminal-event evidence refs"):
        CsvSourceBackedOutcomeIndex(path)


def test_common_terminal_fact_allows_early_existing_common_extinction(tmp_path: Path):
    path = tmp_path / "outcomes.csv"
    _write(path, [_base(
        survived_12m="",
        survived_12m_known_date="",
        survived_12m_evidence_refs="",
        company_terminal_event_type="",
        company_terminal_event_date="",
        company_terminal_event_known_date="",
        company_terminal_event_evidence_refs="",
        existing_common_survived_12m="false",
        existing_common_survived_12m_known_date="2021-05-31",
        existing_common_survived_12m_evidence_refs="SEC:COMMON_CANCEL",
        common_terminal_event_type="cancellation",
        common_terminal_event_date="2021-05-28",
        common_terminal_event_known_date="2021-05-31",
        common_terminal_event_evidence_refs="SEC:COMMON_CANCEL",
        outcome_known_date="2021-05-31",
        evidence_refs="SEC:COMMON_CANCEL",
    )])
    label = CsvSourceBackedOutcomeIndex(path).known_labels(date(2021, 6, 1))[0]
    assert label.existing_common_survived_12m is False
    assert label.common_terminal_fact is not None
    assert label.common_terminal_fact.event_type == "cancellation"
    assert label.company_terminal_fact is None


def test_company_terminal_fact_allows_early_normalized_false(tmp_path: Path):
    path = tmp_path / "outcomes.csv"
    _write(path, [_base(
        survived_12m="",
        survived_12m_known_date="",
        survived_12m_evidence_refs="",
        normalized_within_3y="false",
        normalized_within_3y_known_date="2021-06-30",
        normalized_within_3y_evidence_refs="COURT:LIQ",
    )])
    label = CsvSourceBackedOutcomeIndex(path).known_labels(date(2021, 7, 1))[0]
    assert label.normalized_within_3y is False
    assert label.company_terminal_fact is not None


def test_early_true_survival_is_still_rejected_without_terminal_inference(tmp_path: Path):
    path = tmp_path / "outcomes.csv"
    _write(path, [_base(
        survived_12m="true",
        survived_12m_evidence_refs="SEC:SURVIVAL",
        company_terminal_event_type="",
        company_terminal_event_date="",
        company_terminal_event_known_date="",
        company_terminal_event_evidence_refs="",
        evidence_refs="SEC:SURVIVAL",
    )])
    with pytest.raises(ValueError, match="survived_12m cannot be known before its nominal horizon"):
        CsvSourceBackedOutcomeIndex(path)


def test_early_three_year_multiple_remains_rejected_without_final_payoff_model(tmp_path: Path):
    path = tmp_path / "outcomes.csv"
    _write(path, [_base(
        survived_12m="",
        survived_12m_known_date="",
        survived_12m_evidence_refs="",
        company_terminal_event_type="",
        company_terminal_event_date="",
        company_terminal_event_known_date="",
        company_terminal_event_evidence_refs="",
        equity_multiple_3y="0",
        equity_multiple_3y_known_date="2021-06-30",
        equity_multiple_3y_evidence_refs="SEC:FINAL_PAYOFF",
        evidence_refs="SEC:FINAL_PAYOFF",
    )])
    with pytest.raises(ValueError, match="explicit final shareholder-payoff model"):
        CsvSourceBackedOutcomeIndex(path)


def test_terminal_fact_cannot_be_supplied_for_unrelated_outcome_state(tmp_path: Path):
    path = tmp_path / "outcomes.csv"
    _write(path, [_base(
        survived_12m="",
        survived_12m_known_date="",
        survived_12m_evidence_refs="",
        normalized_within_3y="true",
        normalized_within_3y_known_date="2021-06-30",
        normalized_within_3y_evidence_refs="SEC:NORMALIZED",
        evidence_refs="SEC:NORMALIZED;COURT:LIQ",
    )])
    with pytest.raises(ValueError, match="company terminal fact requires survived_12m=false or normalized_within_3y=false"):
        CsvSourceBackedOutcomeIndex(path)

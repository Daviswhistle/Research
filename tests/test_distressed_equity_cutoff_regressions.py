from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from distressed_equity.credit_replay import JointReplayRun
from distressed_equity.source_outcomes import CsvSourceBackedOutcomeIndex
from distressed_equity.trace_security_master import (
    TraceSecurityEvent,
    build_trace_security_master_resolver,
)
from distressed_equity.walk_forward_priors import build_walk_forward_priors


def test_same_day_snapshot_cannot_reopen_symbol_closed_by_daily_list():
    events = (
        TraceSecurityEvent(
            "add", date(2023, 1, 1), None, None, "A", "111111AA1", "daily:add"
        ),
        TraceSecurityEvent(
            "change",
            date(2023, 2, 1),
            "A",
            "111111AA1",
            "B",
            "222222BB2",
            "daily:change",
        ),
        TraceSecurityEvent(
            "snapshot",
            date(2023, 2, 1),
            None,
            None,
            "A",
            "111111AA1",
            "master:snapshot",
            "security_master_as_of",
        ),
    )
    with pytest.raises(ValueError, match="snapshot.*reopens symbol A"):
        build_trace_security_master_resolver(events)


def test_cutoff_safe_outcome_rebuilds_row_evidence_from_admitted_metric_refs(tmp_path: Path):
    path = tmp_path / "outcomes.csv"
    path.write_text(
        "security_id,ticker,analysis_date,outcome_known_date,survived_12m,survived_12m_known_date,"
        "survived_12m_evidence_refs,existing_common_survived_12m,normalized_within_3y,equity_multiple_3y,"
        "evidence_refs,verification_status,verifier,verified_on,evidence_reopened,notes\n"
        "A,A,2020-12-31,2024-01-31,true,2021-12-31,SEC:2021:SURVIVAL,,,,"
        "SEC:2024:FINAL,verified,reviewer,2024-02-01,true,later final-outcome note\n",
        encoding="utf-8",
    )
    labels = CsvSourceBackedOutcomeIndex(path).known_labels(date(2022, 6, 30))
    assert len(labels) == 1
    label = labels[0]
    assert label.survived_12m is True
    assert label.outcome_known_date == date(2021, 12, 31)
    assert label.survived_12m_evidence_refs == ("SEC:2021:SURVIVAL",)
    assert label.evidence_refs == ("SEC:2021:SURVIVAL",)
    assert "SEC:2024:FINAL" not in label.evidence_refs
    assert label.notes is None


def test_walk_forward_api_rejects_misspelled_feature_dimension_before_bucket_fallback():
    target = JointReplayRun(
        equity_provider="csv",
        credit_provider="csv_bond_market",
        analysis_date=date(2022, 12, 31),
        equity_candidate_count=0,
        candidates_with_credit_links=0,
        candidates_with_fresh_credit=0,
        candidates_with_credit_stress=0,
        candidates=(),
        warnings=(),
    )
    with pytest.raises(ValueError, match="unsupported walk-forward feature dimension.*liqidity_runway"):
        build_walk_forward_priors(  # type: ignore[arg-type]
            target,
            (),
            None,
            None,
            dimensions=("liqidity_runway",),
        )

from __future__ import annotations

from datetime import date

from distressed_equity.debt_instruments import (
    DebtInstrumentLedger,
    DebtInstrumentSnapshot,
    DebtInstrumentVersion,
)
from distressed_equity.debt_lineage import validate_debt_lineage_events_against_source_packet


def _ledger() -> DebtInstrumentLedger:
    return DebtInstrumentLedger(
        versions=(
            DebtInstrumentVersion(
                stable_id="OLD",
                snapshot=DebtInstrumentSnapshot(
                    as_of_date=date(2023, 1, 15),
                    source_accession="acc-old",
                    name="Old Notes",
                    principal=100.0,
                    currency="USD",
                ),
                match_confidence="verified",
            ),
            DebtInstrumentVersion(
                stable_id="NEW",
                snapshot=DebtInstrumentSnapshot(
                    as_of_date=date(2023, 1, 15),
                    source_accession="acc-new",
                    name="New Notes",
                    principal=100.0,
                    currency="USD",
                ),
                match_confidence="verified",
            ),
        ),
        changes=(),
        unmatched_versions=(),
        warnings=(),
    )


def _packet() -> dict[str, object]:
    return {
        "analysis_date": "2023-01-31",
        "spans": [
            {
                "span_id": "s1",
                "source_accession": "acc-1",
                "published_on": "2023-01-10",
                "text": "top-level event evidence",
            },
            {
                "span_id": "s2",
                "source_accession": "acc-2",
                "published_on": "2023-01-12",
                "text": "participant-local evidence",
            },
            {
                "span_id": "future",
                "source_accession": "acc-future",
                "published_on": "2023-02-01",
                "text": "post-cutoff accounting evidence",
            },
        ],
    }


def _event() -> dict[str, object]:
    return {
        "events": [
            {
                "event_id": "exchange-1",
                "effective_date": "2023-01-15",
                "event_type": "exchange",
                "status": "candidate",
                "predecessors": [
                    {
                        "stable_id": "OLD",
                        "amount": 100.0,
                        "source_refs": ["s2"],
                    }
                ],
                "successors": [
                    {
                        "stable_id": "NEW",
                        "amount": 100.0,
                        "source_refs": [],
                    }
                ],
                "source_refs": ["s1"],
                "source_accessions": ["acc-1", "acc-2"],
                "accounting": {
                    "treatment": "undetermined",
                    "basis": "undetermined",
                    "source_refs": [],
                    "notes": [],
                },
            }
        ]
    }


def test_nested_participant_source_ref_is_validated_even_when_not_duplicated_top_level():
    raw = _event()
    raw["events"][0]["predecessors"][0]["source_refs"] = ["missing"]
    raw["events"][0]["source_accessions"] = ["acc-1"]

    result = validate_debt_lineage_events_against_source_packet(_packet(), raw, _ledger())
    assert result.valid is False
    assert any("unknown source_refs: missing" in error for error in result.errors)


def test_nested_accounting_source_ref_is_cutoff_checked_independently():
    raw = _event()
    raw["events"][0]["accounting"] = {
        "treatment": "modification",
        "standard": "ASC 470",
        "basis": "manual_analysis",
        "quantitative_test_pct": 3.0,
        "source_refs": ["future"],
        "notes": [],
    }
    raw["events"][0]["source_accessions"] = ["acc-1", "acc-2", "acc-future"]

    result = validate_debt_lineage_events_against_source_packet(_packet(), raw, _ledger())
    assert result.valid is False
    assert any("cited source future was published after workspace cutoff" in error for error in result.errors)


def test_source_accessions_cover_top_level_and_nested_evidence():
    raw = _event()
    raw["events"][0]["source_accessions"] = ["acc-1"]

    result = validate_debt_lineage_events_against_source_packet(_packet(), raw, _ledger())
    assert result.valid is False
    assert any("do not match all cited span accessions" in error for error in result.errors)


def test_valid_nested_evidence_can_remain_local_without_top_level_duplication():
    result = validate_debt_lineage_events_against_source_packet(_packet(), _event(), _ledger())
    assert result.valid is True
    assert result.errors == ()
    event = result.events[0]
    assert event.source_refs == ("s1",)
    assert event.predecessors[0].source_refs == ("s2",)

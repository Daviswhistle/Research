from datetime import date
import math

import pytest

from distressed_equity.debt_instruments import (
    DebtInstrumentLedger,
    DebtInstrumentSnapshot,
    DebtInstrumentVersion,
    build_debt_instrument_ledger,
    debt_snapshots_from_dict,
)
from distressed_equity.debt_lineage import (
    DebtAccountingAssessment,
    DebtLineageEvent,
    DebtLineageParticipation,
    build_debt_lineage_graph,
    debt_lineage_events_from_dict,
    debt_lineage_source_packet_fingerprint,
    debt_lineage_verification_template,
    promote_verified_debt_lineage_events,
)
from distressed_equity.instrument_verification import validate_debt_snapshots_against_source_packet
from distressed_equity.sec_instruments import (
    FilingDocument,
    SecInstrumentPacket,
    build_instrument_verification_task,
    extract_source_candidates,
    instrument_verification_template,
)


def _document():
    return FilingDocument(
        sequence=2,
        description="Debt Schedule",
        document="ex41.htm",
        document_type="EX-4.1",
        size=50_000,
        url="https://www.sec.gov/Archives/example/ex41.htm",
        source_accession="0000000001-22-000001",
        filing_date=date(2022, 12, 1),
        filing_form="8-K",
    )


def _source_packet(*span_ids: str):
    return {
        "analysis_date": "2023-12-31",
        "spans": [
            {
                "span_id": span_id,
                "source_accession": "new",
                "published_on": "2023-02-02",
                "text": span_id,
            }
            for span_id in span_ids
        ],
    }


def _verified_events(raw, packet):
    events = debt_lineage_events_from_dict(raw)
    source_fingerprint = debt_lineage_source_packet_fingerprint(packet)
    verification = debt_lineage_verification_template(
        events,
        source_packet_fingerprint=source_fingerprint,
    )
    for review in verification["event_reviews"]:
        review.update(
            {
                "status": "verified",
                "verifier": "test-reviewer",
                "verified_on": "2026-09-16",
                "evidence_reopened": True,
            }
        )
    return promote_verified_debt_lineage_events(
        events,
        verification,
        source_packet_fingerprint=source_fingerprint,
    )


def test_generated_snapshot_template_requires_effective_date_and_review_attestation():
    spans, candidates = extract_source_candidates(
        """
        <p>$500 million aggregate principal amount of 5.25% Senior Notes due 2028.</p>
        <p>CUSIP 123456789.</p>
        """,
        _document(),
        max_candidates=5,
    )
    assert candidates
    packet = SecInstrumentPacket(
        ticker="TEST",
        company_name="Test Co",
        analysis_date=date(2022, 12, 31),
        documents=(_document(),),
        spans=spans,
        candidates=candidates,
        warnings=(),
    )
    template = instrument_verification_template(packet)
    row = template["instruments"][0]
    assert row["as_of_date"] is None
    assert row["verification"] == {
        "schema_version": "2",
        "status": "unverified",
        "verifier": None,
        "verified_on": None,
        "evidence_reopened": False,
        "source_packet_fingerprint": None,
        "row_fingerprint": None,
    }
    task = build_instrument_verification_task(packet)
    assert any("economic as_of_date" in item for item in task.required_output)
    assert any("source_packet_fingerprint" in item for item in task.required_output)

    source_packet = {
        "analysis_date": "2022-12-31",
        "spans": [
            {
                "span_id": span.span_id,
                "source_accession": span.source_accession,
                "published_on": span.published_on.isoformat(),
            }
            for span in spans
        ],
    }
    validation = validate_debt_snapshots_against_source_packet(source_packet, template)
    assert validation.valid is False
    assert any("verification.status" in item for item in validation.errors)
    assert any("as_of_date" in item for item in validation.errors)


def test_snapshot_numeric_fields_reject_nan_and_infinity():
    base = {
        "as_of_date": "2023-02-01",
        "source_accession": "a",
        "name": "Term Loan B",
        "maturity_year": 2028,
    }
    for key in ("principal", "commitment", "drawn", "available", "coupon_pct", "spread_bps"):
        for value in (math.nan, math.inf, -math.inf):
            raw = {"instruments": [{**base, key: value}]}
            with pytest.raises(ValueError, match=rf"{key} must be finite"):
                debt_snapshots_from_dict(raw)


def test_fractional_maturity_year_is_rejected_instead_of_truncated():
    raw = {"instruments": [{
        "as_of_date": "2023-02-01",
        "source_accession": "a",
        "name": "Term Loan B",
        "maturity_year": 2028.9,
    }]}
    with pytest.raises(ValueError, match="maturity_year must be an integral year"):
        debt_snapshots_from_dict(raw)


def test_programmatic_lineage_nonfinite_values_are_rejected_by_public_graph_builder():
    snapshot = DebtInstrumentSnapshot(
        as_of_date=date(2023, 2, 1), source_accession="a", name="Old Notes",
        principal=1_000.0, maturity_year=2025, cusip="111111AA1",
    )
    ledger = DebtInstrumentLedger(
        versions=(DebtInstrumentVersion("CUSIP:111111AA1", snapshot, "exact_identifier"),),
        changes=(), unmatched_versions=(), warnings=(),
    )
    event = DebtLineageEvent(
        event_id="bad",
        effective_date=date(2023, 2, 1),
        event_type="redemption",
        status="verified",
        predecessors=(DebtLineageParticipation("CUSIP:111111AA1", amount=math.nan),),
        successors=(),
        source_refs=(),
        cash_paid=math.inf,
        accounting=DebtAccountingAssessment(),
    )
    with pytest.raises(ValueError, match="must be finite"):
        build_debt_lineage_graph(ledger, (event,))


def test_boundary_date_tie_requires_observation_accession_even_before_event_date():
    before_a = DebtInstrumentSnapshot(
        as_of_date=date(2023, 1, 31),
        source_accession="before-a",
        name="Old Notes",
        principal=1_000.0,
        maturity_year=2025,
        cusip="111111AA1",
    )
    before_b = DebtInstrumentSnapshot(
        as_of_date=date(2023, 1, 31),
        source_accession="before-b",
        name="Old Notes",
        principal=600.0,
        maturity_year=2026,
        cusip="111111AA1",
    )
    successor = DebtInstrumentSnapshot(
        as_of_date=date(2023, 2, 15),
        source_accession="new",
        name="New Notes",
        principal=500.0,
        maturity_year=2028,
        cusip="222222BB2",
    )
    ledger = DebtInstrumentLedger(
        versions=(
            DebtInstrumentVersion("CUSIP:111111AA1", before_a, "exact_identifier"),
            DebtInstrumentVersion("CUSIP:111111AA1", before_b, "exact_identifier"),
            DebtInstrumentVersion("CUSIP:222222BB2", successor, "exact_identifier"),
        ),
        changes=(),
        unmatched_versions=(),
        warnings=(),
    )
    raw = {
        "events": [
            {
                "event_id": "exchange",
                "effective_date": "2023-02-15",
                "event_type": "exchange",
                "status": "candidate",
                "predecessors": [
                    {"stable_id": "CUSIP:111111AA1", "amount": 500.0, "source_refs": ["s1"]}
                ],
                "successors": [
                    {"stable_id": "CUSIP:222222BB2", "amount": 500.0, "source_refs": ["s1"]}
                ],
                "source_refs": ["s1"],
            }
        ]
    }
    packet = _source_packet("s1")
    events = _verified_events(raw, packet)
    with pytest.raises(ValueError, match="selected predecessor boundary date"):
        build_debt_lineage_graph(ledger, events)

    raw["events"][0]["predecessors"][0]["observation_accession"] = "before-a"
    graph = build_debt_lineage_graph(ledger, _verified_events(raw, packet))
    assert graph.impacts[0].predecessor_principal == 500.0


def test_wrong_side_snapshot_principal_does_not_become_implicit_participation():
    predecessor_seen_late = DebtInstrumentSnapshot(
        as_of_date=date(2023, 3, 1), source_accession="late-old", name="Old Notes",
        principal=900.0, maturity_year=2025, cusip="111111AA1",
    )
    successor = DebtInstrumentSnapshot(
        as_of_date=date(2023, 2, 1), source_accession="new", name="New Notes",
        principal=500.0, maturity_year=2028, cusip="222222BB2",
    )
    ledger = DebtInstrumentLedger(
        versions=(
            DebtInstrumentVersion("CUSIP:111111AA1", predecessor_seen_late, "exact_identifier"),
            DebtInstrumentVersion("CUSIP:222222BB2", successor, "exact_identifier"),
        ),
        changes=(), unmatched_versions=(), warnings=(),
    )
    raw = {"events": [{
        "event_id": "exchange", "effective_date": "2023-02-01", "event_type": "exchange",
        "status": "candidate",
        "predecessors": [{"stable_id": "CUSIP:111111AA1", "source_refs": ["s1"]}],
        "successors": [{"stable_id": "CUSIP:222222BB2", "amount": 500.0, "source_refs": ["s1"]}],
        "source_refs": ["s1"],
    }]}
    graph = build_debt_lineage_graph(ledger, _verified_events(raw, _source_packet("s1")))
    impact = graph.impacts[0]
    assert impact.predecessor_principal is None
    assert impact.principal_delta is None
    assert any("not used as an implicit participating amount" in item for item in impact.unresolved)
    assert "CUSIP:111111AA1" in graph.terminal_instruments


def test_aggregate_partial_exchanges_can_fully_consume_predecessor_terminal():
    old = DebtInstrumentSnapshot(
        as_of_date=date(2023, 2, 1),
        source_accession="old",
        name="Old Notes",
        principal=1_000.0,
        maturity_year=2025,
        cusip="111111AA1",
    )
    new_1 = DebtInstrumentSnapshot(
        as_of_date=date(2023, 2, 1),
        source_accession="new-1",
        name="New Notes 1",
        principal=500.0,
        maturity_year=2028,
        cusip="222222BB2",
    )
    new_2 = DebtInstrumentSnapshot(
        as_of_date=date(2023, 2, 1),
        source_accession="new-2",
        name="New Notes 2",
        principal=500.0,
        maturity_year=2029,
        cusip="333333CC3",
    )
    ledger = build_debt_instrument_ledger((old, new_1, new_2))
    raw = {
        "events": [
            {
                "event_id": "exchange-1",
                "effective_date": "2023-02-01",
                "event_type": "exchange",
                "status": "candidate",
                "predecessors": [{"stable_id": "CUSIP:111111AA1", "amount": 500.0, "source_refs": ["s1"]}],
                "successors": [{"stable_id": "CUSIP:222222BB2", "amount": 500.0, "source_refs": ["s1"]}],
                "source_refs": ["s1"],
            },
            {
                "event_id": "exchange-2",
                "effective_date": "2023-02-01",
                "event_type": "exchange",
                "status": "candidate",
                "predecessors": [{"stable_id": "CUSIP:111111AA1", "amount": 500.0, "source_refs": ["s2"]}],
                "successors": [{"stable_id": "CUSIP:333333CC3", "amount": 500.0, "source_refs": ["s2"]}],
                "source_refs": ["s2"],
            },
        ]
    }
    graph = build_debt_lineage_graph(ledger, _verified_events(raw, _source_packet("s1", "s2")))
    assert "CUSIP:111111AA1" not in graph.terminal_instruments
    assert set(graph.terminal_instruments) == {"CUSIP:222222BB2", "CUSIP:333333CC3"}


def test_same_accession_different_measurement_dates_do_not_share_consumption_cap():
    old_early = DebtInstrumentSnapshot(
        as_of_date=date(2023, 1, 31), source_accession="same", name="Old Notes",
        principal=1_000.0, maturity_year=2025, cusip="111111AA1",
    )
    old_late = DebtInstrumentSnapshot(
        as_of_date=date(2023, 2, 28), source_accession="same", name="Old Notes",
        principal=1_000.0, maturity_year=2025, cusip="111111AA1",
    )
    new_1 = DebtInstrumentSnapshot(
        as_of_date=date(2023, 2, 1), source_accession="n1", name="New 1",
        principal=600.0, maturity_year=2028, cusip="222222BB2",
    )
    new_2 = DebtInstrumentSnapshot(
        as_of_date=date(2023, 3, 1), source_accession="n2", name="New 2",
        principal=600.0, maturity_year=2029, cusip="333333CC3",
    )
    ledger = DebtInstrumentLedger(
        versions=(
            DebtInstrumentVersion("CUSIP:111111AA1", old_early, "exact_identifier"),
            DebtInstrumentVersion("CUSIP:111111AA1", old_late, "exact_identifier"),
            DebtInstrumentVersion("CUSIP:222222BB2", new_1, "exact_identifier"),
            DebtInstrumentVersion("CUSIP:333333CC3", new_2, "exact_identifier"),
        ),
        changes=(), unmatched_versions=(), warnings=(),
    )
    raw = {"events": [
        {
            "event_id": "e1", "effective_date": "2023-02-01", "event_type": "exchange", "status": "candidate",
            "predecessors": [{"stable_id": "CUSIP:111111AA1", "amount": 600.0, "observation_accession": "same", "source_refs": ["s1"]}],
            "successors": [{"stable_id": "CUSIP:222222BB2", "amount": 600.0, "source_refs": ["s1"]}],
            "source_refs": ["s1"],
        },
        {
            "event_id": "e2", "effective_date": "2023-03-01", "event_type": "exchange", "status": "candidate",
            "predecessors": [{"stable_id": "CUSIP:111111AA1", "amount": 600.0, "source_refs": ["s2"]}],
            "successors": [{"stable_id": "CUSIP:333333CC3", "amount": 600.0, "source_refs": ["s2"]}],
            "source_refs": ["s2"],
        },
    ]}
    # The first event is intentionally ambiguous because the same accession covers
    # two measurement dates; accession alone is not enough to identify one row.
    with pytest.raises(ValueError, match="does not identify exactly one debt snapshot"):
        build_debt_lineage_graph(ledger, _verified_events(raw, _source_packet("s1", "s2")))


def test_mixed_maturity_precision_same_year_is_not_false_acceleration():
    old = DebtInstrumentSnapshot(
        as_of_date=date(2023, 2, 1),
        source_accession="old",
        name="Old Notes",
        principal=500.0,
        maturity_date=date(2025, 12, 31),
        maturity_year=2025,
        cusip="111111AA1",
    )
    new = DebtInstrumentSnapshot(
        as_of_date=date(2023, 2, 1),
        source_accession="new",
        name="New Notes",
        principal=500.0,
        maturity_year=2025,
        cusip="222222BB2",
    )
    ledger = build_debt_instrument_ledger((old, new))
    raw = {
        "events": [
            {
                "event_id": "exchange",
                "effective_date": "2023-02-01",
                "event_type": "exchange",
                "status": "candidate",
                "predecessors": [{"stable_id": "CUSIP:111111AA1", "amount": 500.0, "source_refs": ["s1"]}],
                "successors": [{"stable_id": "CUSIP:222222BB2", "amount": 500.0, "source_refs": ["s1"]}],
                "source_refs": ["s1"],
            }
        ]
    }
    graph = build_debt_lineage_graph(ledger, _verified_events(raw, _source_packet("s1")))
    impact = graph.impacts[0]
    assert impact.nearest_maturity_before == "2025-12-31"
    assert impact.nearest_maturity_after == "2025"
    assert impact.maturity_effect == "not_comparable"
    assert "nearest_maturity_accelerated" not in impact.signals
    assert "nearest_maturity_extended" not in impact.signals

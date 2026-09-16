from datetime import date

from distressed_equity.debt_instruments import (
    DebtInstrumentLedger,
    DebtInstrumentSnapshot,
    DebtInstrumentVersion,
)
from distressed_equity.debt_lineage import (
    build_debt_lineage_graph,
    debt_lineage_events_from_dict,
    debt_lineage_source_packet_fingerprint,
    debt_lineage_verification_template,
    promote_verified_debt_lineage_events,
)


def verified_events(raw, source_packet):
    events = debt_lineage_events_from_dict(raw)
    fingerprint = debt_lineage_source_packet_fingerprint(source_packet)
    verification = debt_lineage_verification_template(
        events,
        source_packet_fingerprint=fingerprint,
    )
    verification["event_reviews"][0].update(
        {
            "status": "verified",
            "verifier": "terminal-test",
            "verified_on": "2026-09-16",
            "evidence_reopened": True,
        }
    )
    return promote_verified_debt_lineage_events(
        events,
        verification,
        source_packet_fingerprint=fingerprint,
    )


def test_off_date_predecessor_principal_cannot_prove_full_extinguishment():
    old = DebtInstrumentSnapshot(
        as_of_date=date(2023, 1, 31),
        source_accession="old-balance",
        name="Old Notes",
        principal=1_000.0,
        maturity_year=2025,
        cusip="111111AA1",
    )
    new = DebtInstrumentSnapshot(
        as_of_date=date(2023, 2, 15),
        source_accession="new-note",
        name="New Notes",
        principal=1_000.0,
        maturity_year=2028,
        cusip="222222BB2",
    )
    ledger = DebtInstrumentLedger(
        versions=(
            DebtInstrumentVersion("CUSIP:111111AA1", old, "exact_identifier"),
            DebtInstrumentVersion("CUSIP:222222BB2", new, "exact_identifier"),
        ),
        changes=(),
        unmatched_versions=(),
        warnings=(),
    )
    source_packet = {
        "analysis_date": "2023-12-31",
        "spans": [
            {
                "span_id": "s1",
                "source_accession": "exchange-doc",
                "published_on": "2023-02-16",
                "text": "exchange evidence",
            }
        ],
    }
    raw = {
        "events": [
            {
                "event_id": "exchange",
                "effective_date": "2023-02-15",
                "event_type": "exchange",
                "status": "candidate",
                "predecessors": [
                    {
                        "stable_id": "CUSIP:111111AA1",
                        "amount": 1_000.0,
                        "source_refs": ["s1"],
                    }
                ],
                "successors": [
                    {
                        "stable_id": "CUSIP:222222BB2",
                        "amount": 1_000.0,
                        "source_refs": ["s1"],
                    }
                ],
                "source_refs": ["s1"],
            }
        ]
    }

    graph = build_debt_lineage_graph(ledger, verified_events(raw, source_packet))

    # The old 1,000 balance was measured 15 days before the event. Even though
    # the explicit participating amount also equals 1,000, that stale balance is
    # not an event-date cap and therefore cannot prove zero residual principal.
    assert "CUSIP:111111AA1" in graph.terminal_instruments
    assert "CUSIP:222222BB2" in graph.terminal_instruments
    impact = graph.impacts[0]
    assert impact.predecessor_principal == 1_000.0
    assert any("observation date 2023-01-31 differs" in item for item in impact.unresolved)


def test_event_date_principal_can_still_prove_full_extinguishment():
    old = DebtInstrumentSnapshot(
        as_of_date=date(2023, 2, 15),
        source_accession="old-event-date",
        name="Old Notes",
        principal=1_000.0,
        maturity_year=2025,
        cusip="111111AA1",
    )
    new = DebtInstrumentSnapshot(
        as_of_date=date(2023, 2, 15),
        source_accession="new-note",
        name="New Notes",
        principal=1_000.0,
        maturity_year=2028,
        cusip="222222BB2",
    )
    ledger = DebtInstrumentLedger(
        versions=(
            DebtInstrumentVersion("CUSIP:111111AA1", old, "exact_identifier"),
            DebtInstrumentVersion("CUSIP:222222BB2", new, "exact_identifier"),
        ),
        changes=(),
        unmatched_versions=(),
        warnings=(),
    )
    packet = {
        "analysis_date": "2023-12-31",
        "spans": [
            {
                "span_id": "s1",
                "source_accession": "exchange-doc",
                "published_on": "2023-02-16",
                "text": "exchange evidence",
            }
        ],
    }
    raw = {
        "events": [
            {
                "event_id": "exchange",
                "effective_date": "2023-02-15",
                "event_type": "exchange",
                "status": "candidate",
                "predecessors": [{"stable_id": "CUSIP:111111AA1", "amount": 1_000.0, "source_refs": ["s1"]}],
                "successors": [{"stable_id": "CUSIP:222222BB2", "amount": 1_000.0, "source_refs": ["s1"]}],
                "source_refs": ["s1"],
            }
        ]
    }
    graph = build_debt_lineage_graph(ledger, verified_events(raw, packet))
    assert "CUSIP:111111AA1" not in graph.terminal_instruments
    assert "CUSIP:222222BB2" in graph.terminal_instruments

from datetime import date

import pytest

from distressed_equity.debt_instruments import DebtInstrumentSnapshot, build_debt_instrument_ledger
from distressed_equity.debt_lineage import (
    build_debt_lineage_graph,
    debt_lineage_event_template,
    debt_lineage_events_from_dict,
    debt_lineage_graph_to_dict,
    debt_lineage_verification_template,
    promote_verified_debt_lineage_events,
    validate_debt_lineage_events_against_source_packet,
)


def exchange_ledger():
    old = DebtInstrumentSnapshot(
        as_of_date=date(2022, 12, 31),
        source_accession="old",
        name="5.00% Senior Notes due 2025",
        instrument_type="senior_notes",
        principal=1_000.0,
        maturity_year=2025,
        coupon_pct=5.0,
        seniority="senior_unsecured",
        secured=False,
        cusip="111111AA1",
    )
    new = DebtInstrumentSnapshot(
        as_of_date=date(2023, 3, 31),
        source_accession="new",
        name="8.00% Senior Secured Notes due 2028",
        instrument_type="senior_notes",
        principal=550.0,
        maturity_year=2028,
        coupon_pct=8.0,
        seniority="first_lien",
        secured=True,
        cusip="222222BB2",
    )
    return build_debt_instrument_ledger((old, new))


def same_day_exchange_ledger():
    old = DebtInstrumentSnapshot(
        as_of_date=date(2023, 2, 1),
        source_accession="old-event",
        name="Old Notes",
        principal=1_000.0,
        maturity_year=2025,
        secured=False,
        cusip="111111AA1",
    )
    new = DebtInstrumentSnapshot(
        as_of_date=date(2023, 2, 1),
        source_accession="new-event",
        name="New Notes",
        principal=550.0,
        maturity_year=2028,
        secured=True,
        cusip="222222BB2",
    )
    return build_debt_instrument_ledger((old, new))


def exchange_event(*, predecessor_amount=600.0, successor_amount=550.0):
    return {
        "events": [
            {
                "event_id": "2023-exchange",
                "effective_date": "2023-02-01",
                "event_type": "exchange",
                "status": "candidate",
                "predecessors": [
                    {
                        "stable_id": "CUSIP:111111AA1",
                        "amount": predecessor_amount,
                        "source_refs": ["s1"],
                    }
                ],
                "successors": [
                    {
                        "stable_id": "CUSIP:222222BB2",
                        "amount": successor_amount,
                        "source_refs": ["s1"],
                    }
                ],
                "source_refs": ["s1"],
                "source_accession": "new",
                "cash_paid": 25.0,
                "fees_paid": 5.0,
                "equity_issued_shares": 10.0,
                "accounting": {
                    "treatment": "extinguishment",
                    "standard": "US_GAAP",
                    "basis": "issuer_disclosed",
                    "source_refs": ["s1"],
                },
            }
        ]
    }


def verified_events(raw):
    events = debt_lineage_events_from_dict(raw)
    verification = debt_lineage_verification_template(events)
    for review in verification["event_reviews"]:
        review.update(
            {
                "status": "verified",
                "verifier": "test-verifier",
                "verified_on": "2026-09-16",
                "evidence_reopened": True,
            }
        )
    return promote_verified_debt_lineage_events(events, verification)


def source_packet(*, analysis_date="2023-12-31", published_on="2023-02-02"):
    return {
        "analysis_date": analysis_date,
        "spans": [
            {
                "span_id": "s1",
                "source_accession": "new",
                "published_on": published_on,
            }
        ],
    }


def test_exchange_links_distinct_cusips_without_collapsing_stable_ids():
    ledger = exchange_ledger()
    assert {version.stable_id for version in ledger.versions} == {
        "CUSIP:111111AA1",
        "CUSIP:222222BB2",
    }

    graph = build_debt_lineage_graph(ledger, verified_events(exchange_event()))
    assert graph.root_instruments == ("CUSIP:111111AA1",)
    assert graph.terminal_instruments == ("CUSIP:222222BB2",)
    assert [(edge.from_node, edge.to_node) for edge in graph.edges] == [
        ("CUSIP:111111AA1", "EVENT:2023-exchange"),
        ("EVENT:2023-exchange", "CUSIP:222222BB2"),
    ]

    impact = graph.impacts[0]
    assert impact.predecessor_principal == 600.0
    assert impact.successor_principal == 550.0
    assert impact.principal_delta == -50.0
    assert impact.maturity_effect == "extended"
    assert impact.secured_principal_before == 0.0
    assert impact.secured_principal_after == 550.0
    assert impact.secured_principal_delta == 550.0
    assert set(impact.signals) >= {
        "principal_reduced",
        "nearest_maturity_extended",
        "secured_principal_increased",
        "cash_outflow_to_creditors",
        "transaction_fee_outflow",
        "existing_common_dilution",
        "accounting_extinguishment",
    }
    assert any("observation date" in item for item in impact.unresolved)


def test_partial_exchange_uses_participating_amount_not_full_old_principal():
    graph = build_debt_lineage_graph(
        exchange_ledger(),
        verified_events(exchange_event(predecessor_amount=400.0, successor_amount=360.0)),
    )
    impact = graph.impacts[0]
    assert impact.predecessor_principal == 400.0
    assert impact.successor_principal == 360.0
    assert impact.principal_delta == -40.0


def test_one_to_many_exchange_is_hyperedge_not_false_pairwise_identity():
    old = DebtInstrumentSnapshot(
        as_of_date=date(2022, 12, 31),
        source_accession="a",
        name="Old Notes",
        principal=1_000.0,
        maturity_year=2025,
        secured=False,
        cusip="111111AA1",
    )
    first = DebtInstrumentSnapshot(
        as_of_date=date(2023, 3, 31),
        source_accession="b",
        name="New First Lien Notes",
        principal=500.0,
        maturity_year=2028,
        secured=True,
        cusip="222222BB2",
    )
    second = DebtInstrumentSnapshot(
        as_of_date=date(2023, 3, 31),
        source_accession="b",
        name="New Second Lien Notes",
        principal=300.0,
        maturity_year=2029,
        secured=True,
        cusip="333333CC3",
    )
    ledger = build_debt_instrument_ledger((old, first, second))
    raw = exchange_event(predecessor_amount=900.0, successor_amount=500.0)
    raw["events"][0]["successors"].append(
        {"stable_id": "CUSIP:333333CC3", "amount": 300.0, "source_refs": ["s1"]}
    )
    graph = build_debt_lineage_graph(ledger, verified_events(raw))
    assert len(graph.edges) == 3
    assert {edge.to_node for edge in graph.edges if edge.role == "successor"} == {
        "CUSIP:222222BB2",
        "CUSIP:333333CC3",
    }
    assert graph.impacts[0].principal_delta == -100.0


def test_amendment_cannot_be_used_to_bridge_different_stable_ids():
    raw = exchange_event()
    raw["events"][0]["event_type"] = "amendment"
    with pytest.raises(ValueError, match="must preserve stable_id"):
        debt_lineage_events_from_dict(raw)


def test_same_id_amendment_does_not_destroy_root_or_terminal_identity():
    old = DebtInstrumentSnapshot(
        as_of_date=date(2022, 12, 31),
        source_accession="a",
        name="Term Loan",
        principal=800.0,
        maturity_year=2026,
        cusip="444444DD4",
    )
    new = DebtInstrumentSnapshot(
        as_of_date=date(2023, 3, 31),
        source_accession="b",
        name="Term Loan",
        principal=790.0,
        maturity_year=2027,
        cusip="444444DD4",
    )
    ledger = build_debt_instrument_ledger((old, new))
    raw = {
        "events": [
            {
                "event_id": "amend-1",
                "effective_date": "2023-02-01",
                "event_type": "amendment",
                "status": "candidate",
                "predecessors": [{"stable_id": "CUSIP:444444DD4", "amount": 800.0}],
                "successors": [{"stable_id": "CUSIP:444444DD4", "amount": 790.0}],
                "source_refs": ["s1"],
            }
        ]
    }
    graph = build_debt_lineage_graph(ledger, verified_events(raw))
    assert graph.root_instruments == ("CUSIP:444444DD4",)
    assert graph.terminal_instruments == ("CUSIP:444444DD4",)


def test_cross_id_exchange_cannot_repeat_same_stable_id_on_both_sides():
    raw = exchange_event()
    raw["events"][0]["successors"][0]["stable_id"] = "CUSIP:111111AA1"
    with pytest.raises(ValueError, match="must be disjoint"):
        debt_lineage_events_from_dict(raw)


def test_redemption_with_successor_must_be_modelled_as_refinancing_or_exchange():
    raw = exchange_event()
    raw["events"][0]["event_type"] = "redemption"
    with pytest.raises(ValueError, match="must not have successor"):
        debt_lineage_events_from_dict(raw)


def test_accounting_classification_requires_source_backed_basis():
    raw = exchange_event()
    raw["events"][0]["accounting"]["source_refs"] = []
    with pytest.raises(ValueError, match="source_refs are required"):
        debt_lineage_events_from_dict(raw)

    raw = exchange_event()
    raw["events"][0]["accounting"]["basis"] = "undetermined"
    with pytest.raises(ValueError, match="basis is required"):
        debt_lineage_events_from_dict(raw)


def test_direct_verified_input_is_rejected_without_promotion():
    raw = exchange_event()
    raw["events"][0]["status"] = "verified"
    with pytest.raises(ValueError, match="fingerprint-bound promotion"):
        debt_lineage_events_from_dict(raw)


def test_candidate_event_does_not_define_confirmed_roots_or_terminals():
    graph = build_debt_lineage_graph(
        exchange_ledger(),
        debt_lineage_events_from_dict(exchange_event()),
    )
    assert graph.root_instruments == ()
    assert graph.terminal_instruments == ()
    assert graph.unresolved_events == ("2023-exchange",)
    assert any("candidate-only" in warning for warning in graph.warnings)


def test_verification_requires_reopened_evidence_and_matching_fingerprint():
    events = debt_lineage_events_from_dict(exchange_event())
    verification = debt_lineage_verification_template(events)
    verification["event_reviews"][0].update(
        {
            "status": "verified",
            "verifier": "reviewer",
            "verified_on": "2026-09-16",
            "evidence_reopened": False,
        }
    )
    with pytest.raises(ValueError, match="evidence was not re-opened"):
        promote_verified_debt_lineage_events(events, verification)

    verification["event_reviews"][0]["evidence_reopened"] = True
    changed_events = debt_lineage_events_from_dict(exchange_event(successor_amount=540.0))
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        promote_verified_debt_lineage_events(changed_events, verification)


def test_verified_event_records_verifier_and_fingerprint():
    event = verified_events(exchange_event())[0]
    assert event.status == "verified"
    assert event.verified_by == "test-verifier"
    assert event.verified_on == date(2026, 9, 16)
    assert event.evidence_reopened is True
    assert event.verification_fingerprint.startswith("sha256:")


def test_participating_amount_cannot_exceed_principal_observed_on_event_date():
    events = debt_lineage_events_from_dict(exchange_event(predecessor_amount=1_001.0))
    with pytest.raises(ValueError, match="exceeds observed principal on event date"):
        build_debt_lineage_graph(same_day_exchange_ledger(), events)


def test_non_event_date_snapshot_is_not_false_hard_ceiling_for_event_amount():
    graph = build_debt_lineage_graph(
        exchange_ledger(),
        verified_events(exchange_event(successor_amount=600.0)),
    )
    impact = graph.impacts[0]
    assert impact.successor_principal == 600.0
    assert any("not used as a hard ceiling" in item for item in impact.unresolved)


def test_source_validation_blocks_future_or_unknown_event_evidence():
    valid = validate_debt_lineage_events_against_source_packet(
        source_packet(), exchange_event(), exchange_ledger()
    )
    assert valid.valid is True
    assert any("observation date" in warning for warning in valid.warnings)

    future = validate_debt_lineage_events_against_source_packet(
        source_packet(analysis_date="2023-01-31"), exchange_event(), exchange_ledger()
    )
    assert future.valid is False
    assert any("after workspace cutoff" in error for error in future.errors)

    unknown = exchange_event()
    unknown["events"][0]["source_refs"] = ["missing"]
    unknown["events"][0]["predecessors"][0]["source_refs"] = ["missing"]
    unknown["events"][0]["successors"][0]["source_refs"] = ["missing"]
    unknown["events"][0]["accounting"]["source_refs"] = ["missing"]
    invalid = validate_debt_lineage_events_against_source_packet(
        source_packet(), unknown, exchange_ledger()
    )
    assert invalid.valid is False
    assert any("unknown source_refs" in error for error in invalid.errors)


def test_serialization_and_template_make_non_inference_boundary_explicit():
    ledger = exchange_ledger()
    graph = build_debt_lineage_graph(ledger, verified_events(exchange_event()))
    payload = debt_lineage_graph_to_dict(graph)
    assert payload["semantics"]["accounting_treatment_is_auto_inferred"] is False
    assert payload["semantics"]["candidate_events_are_confirmed"] is False
    assert payload["semantics"]["verified_requires_fingerprint_bound_reopened_evidence"] is True
    assert payload["semantics"]["snapshot_principal_is_event_ceiling_only_on_same_date"] is True
    assert payload["events"][0]["effective_date"] == "2023-02-01"
    assert payload["events"][0]["verified_by"] == "test-verifier"

    template = debt_lineage_event_template(ledger)
    assert set(template["available_stable_ids"]) == {
        "CUSIP:111111AA1",
        "CUSIP:222222BB2",
    }
    assert template["allowed_input_statuses"] == ["candidate"]
    assert any("Do not mark status=verified" in item for item in template["guardrails"])
    assert any("Do not merge old and new CUSIPs" in item for item in template["guardrails"])
    assert any("not a hard ceiling" in item for item in template["guardrails"])

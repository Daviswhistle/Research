import json

from distressed_equity.orchestration_cli import main


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def workspace_packet():
    return {
        "snapshot": {
            "ticker": "TEST", "cik": "0000000001", "company_name": "Test Co",
            "analysis_date": "2023-12-31", "warnings": [],
        },
        "evidence": [],
        "screening_draft": {"screening_candidate_draft": {
            "ticker": "TEST", "company_name": "Test Co", "analysis_date": "2023-12-31",
            "capital_structure": {"current_price": None},
        }},
        "capital_stack_packet": {"snippets": []},
        "capital_stack_diff": {"changes": []},
        "legal_name_alias_graph": {},
        "source_document_graph": None,
        "cross_cik_graph": None,
        "named_entity_contract_graph": None,
        "debt_instrument_source_packet": {
            "analysis_date": "2023-12-31", "documents": [], "candidates": [],
            "spans": [
                {"span_id": "s-old", "source_accession": "old", "published_on": "2023-01-10"},
                {"span_id": "s-new", "source_accession": "new", "published_on": "2023-04-15"},
            ],
        },
    }


def verification():
    return {
        "status": "verified",
        "verifier": "workspace-snapshot-reviewer",
        "verified_on": "2026-09-16",
        "evidence_reopened": True,
    }


def instruments_payload():
    return {"instruments": [
        {
            "as_of_date": "2023-01-01", "source_accession": "old", "name": "Old Notes",
            "principal": 1000.0, "maturity_year": 2025, "secured": False,
            "cusip": "111111AA1", "source_refs": ["s-old"], "verification": verification(),
        },
        {
            "as_of_date": "2023-03-31", "source_accession": "new", "name": "New Secured Notes",
            "principal": 550.0, "maturity_year": 2028, "secured": True,
            "cusip": "222222BB2", "source_refs": ["s-new"], "verification": verification(),
        },
    ]}


def events_payload():
    return {"events": [{
        "event_id": "exchange-1", "effective_date": "2023-02-01",
        "event_type": "exchange", "status": "candidate",
        "predecessors": [{"stable_id": "CUSIP:111111AA1", "amount": 600.0}],
        "successors": [{"stable_id": "CUSIP:222222BB2", "amount": 550.0}],
        "source_refs": ["s-new"], "source_accession": "new",
        "accounting": {"treatment": "undetermined", "basis": "undetermined", "source_refs": []},
    }]}


def base_args(workspace, instruments_path):
    return [
        "--ticker", "TEST", "--analysis-date", "2023-12-31",
        "--workspace", str(workspace), "--debt-instruments", str(instruments_path),
    ]


def test_research_workspace_requires_verification_before_confirming_lineage(tmp_path):
    workspace = tmp_path / "workspace"; workspace.mkdir()
    write_json(workspace / "research_packet.json", workspace_packet())
    instruments_path = tmp_path / "instruments.json"; write_json(instruments_path, instruments_payload())
    events_path = tmp_path / "events.json"; write_json(events_path, events_payload())

    args = base_args(workspace, instruments_path) + ["--debt-lineage", str(events_path)]
    assert main(args) == 0
    candidate = json.loads((workspace / "debt_lineage.json").read_text(encoding="utf-8"))
    assert candidate["events"][0]["status"] == "candidate"
    assert candidate["root_instruments"] == []
    assert candidate["terminal_instruments"] == []

    verification_path = workspace / "debt_lineage_verification_template.json"
    verification_payload = json.loads(verification_path.read_text(encoding="utf-8"))
    verification_payload["event_reviews"][0].update({
        "status": "verified", "verifier": "workspace-reviewer",
        "verified_on": "2026-09-16", "evidence_reopened": True,
    })
    approved_path = tmp_path / "lineage_verification.json"; write_json(approved_path, verification_payload)
    assert main(args + ["--debt-lineage-verification", str(approved_path)]) == 0

    lineage = json.loads((workspace / "debt_lineage.json").read_text(encoding="utf-8"))
    assert lineage["events"][0]["status"] == "verified"
    assert lineage["events"][0]["verified_by"] == "workspace-reviewer"
    assert lineage["impacts"][0]["principal_delta"] == -50.0
    assert lineage["root_instruments"] == ["CUSIP:111111AA1"]
    assert lineage["terminal_instruments"] == ["CUSIP:111111AA1", "CUSIP:222222BB2"]


def test_in_place_approved_verification_survives_rebuild(tmp_path):
    workspace = tmp_path / "workspace"; workspace.mkdir()
    write_json(workspace / "research_packet.json", workspace_packet())
    instruments_path = tmp_path / "instruments.json"; write_json(instruments_path, instruments_payload())
    events_path = tmp_path / "events.json"; write_json(events_path, events_payload())
    args = base_args(workspace, instruments_path) + ["--debt-lineage", str(events_path)]

    assert main(args) == 0
    approval_path = workspace / "debt_lineage_verification_template.json"
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    approval["event_reviews"][0].update({
        "status": "verified",
        "verifier": "in-place-reviewer",
        "verified_on": "2026-09-16",
        "evidence_reopened": True,
    })
    write_json(approval_path, approval)

    assert main(args + ["--debt-lineage-verification", str(approval_path)]) == 0
    persisted = json.loads(approval_path.read_text(encoding="utf-8"))
    assert persisted["event_reviews"][0]["status"] == "verified"
    lineage = json.loads((workspace / "debt_lineage.json").read_text(encoding="utf-8"))
    assert lineage["events"][0]["status"] == "verified"
    assert lineage["events"][0]["verified_by"] == "in-place-reviewer"


def test_rebuilding_ledger_without_lineage_removes_stale_lineage_artifacts(tmp_path):
    workspace = tmp_path / "workspace"; workspace.mkdir()
    write_json(workspace / "research_packet.json", workspace_packet())
    instruments_path = tmp_path / "instruments.json"; write_json(instruments_path, instruments_payload())
    write_json(workspace / "debt_lineage.json", {"stale": True})
    write_json(workspace / "debt_lineage_verification_template.json", {"stale": True})

    assert main(base_args(workspace, instruments_path)) == 0
    assert not (workspace / "debt_lineage.json").exists()
    assert not (workspace / "debt_lineage_verification_template.json").exists()
    assert (workspace / "debt_instrument_ledger.json").exists()
    assert (workspace / "debt_lineage_template.json").exists()


def test_failed_replacement_lineage_does_not_leave_old_derived_artifacts(tmp_path):
    workspace = tmp_path / "workspace"; workspace.mkdir()
    write_json(workspace / "research_packet.json", workspace_packet())
    instruments_path = tmp_path / "instruments.json"; write_json(instruments_path, instruments_payload())
    write_json(workspace / "debt_lineage.json", {"stale": True})
    write_json(workspace / "debt_lineage_verification_template.json", {"stale": True})

    invalid = events_payload()
    invalid["events"][0]["source_refs"] = ["missing-span"]
    events_path = tmp_path / "invalid-events.json"; write_json(events_path, invalid)
    try:
        main(base_args(workspace, instruments_path) + ["--debt-lineage", str(events_path)])
    except ValueError as exc:
        assert "unknown source_refs" in str(exc)
    else:
        raise AssertionError("expected invalid replacement lineage to fail")

    assert (workspace / "debt_instrument_ledger.json").exists()
    assert not (workspace / "debt_lineage.json").exists()
    assert not (workspace / "debt_lineage_verification_template.json").exists()


def test_unverified_snapshot_candidate_is_rejected_by_workspace(tmp_path):
    workspace = tmp_path / "workspace"; workspace.mkdir()
    write_json(workspace / "research_packet.json", workspace_packet())
    raw = instruments_payload(); raw["instruments"][0].pop("verification")
    instruments_path = tmp_path / "instruments.json"; write_json(instruments_path, raw)
    try:
        main(base_args(workspace, instruments_path))
    except ValueError as exc:
        assert "explicit verification is required" in str(exc)
    else:
        raise AssertionError("expected unverified snapshot candidate to fail")


def test_research_workspace_refuses_lineage_without_debt_instruments(tmp_path):
    workspace = tmp_path / "workspace"; workspace.mkdir()
    packet = workspace_packet(); packet["debt_instrument_source_packet"] = {"analysis_date": "2023-12-31", "spans": []}
    write_json(workspace / "research_packet.json", packet)
    events_path = tmp_path / "events.json"; write_json(events_path, {"events": []})
    try:
        main(["--ticker", "TEST", "--analysis-date", "2023-12-31", "--workspace", str(workspace), "--debt-lineage", str(events_path)])
    except ValueError as exc:
        assert "requires --debt-instruments" in str(exc)
    else:
        raise AssertionError("expected --debt-lineage without --debt-instruments to fail")


def test_research_workspace_refuses_verification_without_lineage(tmp_path):
    workspace = tmp_path / "workspace"; workspace.mkdir()
    write_json(workspace / "research_packet.json", workspace_packet())
    verification_path = tmp_path / "verification.json"; write_json(verification_path, {"schema_version": "1", "event_reviews": []})
    try:
        main([
            "--ticker", "TEST", "--analysis-date", "2023-12-31", "--workspace", str(workspace),
            "--debt-lineage-verification", str(verification_path),
        ])
    except ValueError as exc:
        assert "requires --debt-lineage" in str(exc)
    else:
        raise AssertionError("expected verification without lineage to fail")

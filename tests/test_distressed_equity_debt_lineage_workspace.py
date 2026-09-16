import json

from distressed_equity.orchestration_cli import main


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def workspace_packet():
    return {
        "snapshot": {
            "ticker": "TEST",
            "cik": "0000000001",
            "company_name": "Test Co",
            "analysis_date": "2023-12-31",
            "warnings": [],
        },
        "evidence": [],
        "screening_draft": {
            "screening_candidate_draft": {
                "ticker": "TEST",
                "company_name": "Test Co",
                "analysis_date": "2023-12-31",
                "capital_structure": {"current_price": None},
            }
        },
        "capital_stack_packet": {"snippets": []},
        "capital_stack_diff": {"changes": []},
        "legal_name_alias_graph": {},
        "source_document_graph": None,
        "cross_cik_graph": None,
        "named_entity_contract_graph": None,
        "debt_instrument_source_packet": {
            "analysis_date": "2023-12-31",
            "documents": [],
            "candidates": [],
            "spans": [
                {
                    "span_id": "s-old",
                    "source_accession": "old",
                    "published_on": "2023-01-01",
                },
                {
                    "span_id": "s-new",
                    "source_accession": "new",
                    "published_on": "2023-02-02",
                },
            ],
        },
    }


def instruments_payload():
    return {
        "instruments": [
            {
                "as_of_date": "2023-01-01",
                "source_accession": "old",
                "name": "Old Notes",
                "principal": 1000.0,
                "maturity_year": 2025,
                "secured": False,
                "cusip": "111111AA1",
                "source_refs": ["s-old"],
            },
            {
                "as_of_date": "2023-03-31",
                "source_accession": "new",
                "name": "New Secured Notes",
                "principal": 550.0,
                "maturity_year": 2028,
                "secured": True,
                "cusip": "222222BB2",
                "source_refs": ["s-new"],
            },
        ]
    }


def events_payload():
    return {
        "events": [
            {
                "event_id": "exchange-1",
                "effective_date": "2023-02-01",
                "event_type": "exchange",
                "status": "candidate",
                "predecessors": [
                    {"stable_id": "CUSIP:111111AA1", "amount": 600.0}
                ],
                "successors": [
                    {"stable_id": "CUSIP:222222BB2", "amount": 550.0}
                ],
                "source_refs": ["s-new"],
                "source_accession": "new",
                "accounting": {
                    "treatment": "undetermined",
                    "basis": "undetermined",
                    "source_refs": [],
                },
            }
        ]
    }


def test_research_workspace_requires_verification_before_confirming_lineage(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_json(workspace / "research_packet.json", workspace_packet())

    instruments_path = tmp_path / "instruments.json"
    events_path = tmp_path / "events.json"
    write_json(instruments_path, instruments_payload())
    write_json(events_path, events_payload())

    base_args = [
        "--ticker",
        "TEST",
        "--analysis-date",
        "2023-12-31",
        "--workspace",
        str(workspace),
        "--debt-instruments",
        str(instruments_path),
        "--debt-lineage",
        str(events_path),
    ]
    assert main(base_args) == 0

    template = json.loads((workspace / "debt_lineage_template.json").read_text(encoding="utf-8"))
    assert set(template["available_stable_ids"]) == {
        "CUSIP:111111AA1",
        "CUSIP:222222BB2",
    }

    candidate_lineage = json.loads((workspace / "debt_lineage.json").read_text(encoding="utf-8"))
    assert candidate_lineage["events"][0]["status"] == "candidate"
    assert candidate_lineage["root_instruments"] == []
    assert candidate_lineage["terminal_instruments"] == []

    verification_path = workspace / "debt_lineage_verification_template.json"
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    review = verification["event_reviews"][0]
    review.update(
        {
            "status": "verified",
            "verifier": "workspace-reviewer",
            "verified_on": "2026-09-16",
            "evidence_reopened": True,
        }
    )
    approved_path = tmp_path / "lineage_verification.json"
    write_json(approved_path, verification)

    assert main(base_args + ["--debt-lineage-verification", str(approved_path)]) == 0

    lineage = json.loads((workspace / "debt_lineage.json").read_text(encoding="utf-8"))
    assert lineage["events"][0]["status"] == "verified"
    assert lineage["events"][0]["verified_by"] == "workspace-reviewer"
    assert lineage["impacts"][0]["principal_delta"] == -50.0
    assert "nearest_maturity_extended" in lineage["impacts"][0]["signals"]
    assert lineage["root_instruments"] == ["CUSIP:111111AA1"]
    assert lineage["terminal_instruments"] == ["CUSIP:222222BB2"]

    summary = (workspace / "summary.md").read_text(encoding="utf-8")
    assert "Debt exchange / modification lineage" in summary
    assert "verified 1" in summary
    assert "Verification template" in summary


def test_research_workspace_refuses_lineage_without_debt_instruments(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    packet = workspace_packet()
    packet["debt_instrument_source_packet"] = {"analysis_date": "2023-12-31", "spans": []}
    write_json(workspace / "research_packet.json", packet)
    events_path = tmp_path / "events.json"
    write_json(events_path, {"events": []})

    try:
        main(
            [
                "--ticker",
                "TEST",
                "--analysis-date",
                "2023-12-31",
                "--workspace",
                str(workspace),
                "--debt-lineage",
                str(events_path),
            ]
        )
    except ValueError as exc:
        assert "requires --debt-instruments" in str(exc)
    else:
        raise AssertionError("expected --debt-lineage without --debt-instruments to fail")


def test_research_workspace_refuses_verification_without_lineage(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_json(workspace / "research_packet.json", workspace_packet())
    verification_path = tmp_path / "verification.json"
    write_json(verification_path, {"schema_version": "1", "event_reviews": []})

    try:
        main(
            [
                "--ticker",
                "TEST",
                "--analysis-date",
                "2023-12-31",
                "--workspace",
                str(workspace),
                "--debt-lineage-verification",
                str(verification_path),
            ]
        )
    except ValueError as exc:
        assert "requires --debt-lineage" in str(exc)
    else:
        raise AssertionError("expected verification without lineage to fail")

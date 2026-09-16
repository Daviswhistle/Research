import json

import distressed_equity.orchestration_cli as cli
from distressed_equity.orchestration import ResearchBundle


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def refreshed_packet():
    return {
        "snapshot": {
            "ticker": "TEST",
            "cik": "0000000001",
            "company_name": "Refreshed Test Co",
            "analysis_date": "2022-12-31",
            "warnings": [],
        },
        "evidence": [],
        "point_in_time_violations": [],
        "screening_draft": {
            "screening_candidate_draft": {
                "ticker": "TEST",
                "company_name": "Refreshed Test Co",
                "analysis_date": "2022-12-31",
                "capital_structure": {"current_price": None},
            }
        },
        "capital_stack_packet": {"snippets": [], "warnings": []},
        "capital_stack_diff": {"changes": [], "warnings": []},
        "legal_name_alias_graph": None,
        "source_document_graph": None,
        "cross_cik_graph": None,
        "named_entity_contract_graph": None,
        "debt_instrument_source_packet": {
            "analysis_date": "2022-12-31",
            "documents": [],
            "spans": [],
            "candidates": [],
            "warnings": [],
        },
        "debt_instrument_verification": None,
        "market_snapshot": None,
        "agent_tasks": [],
    }


def test_refresh_removes_all_artifacts_bound_to_old_debt_evidence_before_publish(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_json(
        workspace / "research_packet.json",
        {
            "snapshot": {
                "ticker": "TEST",
                "cik": "0000000001",
                "company_name": "Old Test Co",
                "analysis_date": "2022-12-31",
            }
        },
    )
    stale_names = (
        "debt_instrument_ledger.json",
        "debt_lineage_template.json",
        "debt_lineage.json",
        "debt_lineage_verification_template.json",
        "bond_market.json",
        "merged.json",
    )
    for name in stale_names:
        write_json(workspace / name, {"old_evidence": True})

    bundle = ResearchBundle(
        ticker="TEST",
        company_name="Refreshed Test Co",
        analysis_date=cli.date(2022, 12, 31),
        packet=refreshed_packet(),
        warnings=(),
    )
    monkeypatch.setattr(cli, "SecClient", lambda user_agent=None: object())
    monkeypatch.setattr(cli, "build_research_bundle", lambda *args, **kwargs: bundle)
    monkeypatch.setattr(cli, "render_research_summary", lambda bundle, merged: "refreshed\n")

    assert cli.main(
        [
            "--ticker",
            "TEST",
            "--analysis-date",
            "2022-12-31",
            "--workspace",
            str(workspace),
            "--refresh",
        ]
    ) == 0

    refreshed = json.loads((workspace / "research_packet.json").read_text(encoding="utf-8"))
    assert refreshed["snapshot"]["company_name"] == "Refreshed Test Co"
    for name in stale_names:
        assert not (workspace / name).exists()

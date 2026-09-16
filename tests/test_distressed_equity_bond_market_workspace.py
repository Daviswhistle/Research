from __future__ import annotations

import json

from distressed_equity.orchestration_cli import main


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def packet():
    return {
        "snapshot": {
            "ticker": "TEST", "cik": "0000000001", "company_name": "Test Co",
            "analysis_date": "2022-12-31", "warnings": [],
        },
        "evidence": [],
        "point_in_time_violations": [],
        "screening_draft": {"screening_candidate_draft": {
            "ticker": "TEST", "company_name": "Test Co", "analysis_date": "2022-12-31",
            "capital_structure": {"current_price": None},
        }},
        "capital_stack_packet": {"snippets": [], "warnings": []},
        "capital_stack_diff": {"changes": [], "warnings": []},
        "legal_name_alias_graph": {},
        "source_document_graph": None,
        "cross_cik_graph": None,
        "named_entity_contract_graph": None,
        "market_snapshot": None,
        "agent_tasks": [],
        "debt_instrument_source_packet": {
            "analysis_date": "2022-12-31",
            "documents": [],
            "candidates": [],
            "spans": [
                {
                    "span_id": "s1", "source_accession": "acc1",
                    "published_on": "2022-11-15", "text": "verified note evidence",
                }
            ],
        },
    }


def debt_payload():
    return {"instruments": [{
        "as_of_date": "2022-09-30",
        "source_accession": "acc1",
        "name": "5% Senior Notes",
        "principal": 1_000_000_000.0,
        "maturity_date": "2028-06-15",
        "maturity_year": 2028,
        "coupon_pct": 5.0,
        "secured": False,
        "currency": "USD",
        "cusip": "111111AA1",
        "source_refs": ["s1"],
        "notes": [],
        "verification": {
            "status": "verified",
            "verifier": "bond-market-test",
            "verified_on": "2026-09-16",
            "evidence_reopened": True,
        },
    }]}


def base_args(workspace, debt_path):
    return [
        "--ticker", "TEST",
        "--analysis-date", "2022-12-31",
        "--workspace", str(workspace),
        "--debt-instruments", str(debt_path),
    ]


def test_workspace_builds_bond_market_only_after_verified_debt_identity(tmp_path):
    workspace = tmp_path / "workspace"; workspace.mkdir()
    write_json(workspace / "research_packet.json", packet())
    debt_path = tmp_path / "debt.json"; write_json(debt_path, debt_payload())
    bond_csv = tmp_path / "bond.csv"
    bond_csv.write_text(
        "date,cusip,price_pct_par,yield_pct,benchmark_yield_pct,volume,source\n"
        "2022-12-29,111111AA1,55,18,4,500000,normalized TRACE\n",
        encoding="utf-8",
    )

    args = base_args(workspace, debt_path) + [
        "--bond-market-provider", "csv",
        "--bond-observations-csv", str(bond_csv),
    ]
    assert main(args) == 0

    payload = json.loads((workspace / "bond_market.json").read_text(encoding="utf-8"))
    assert payload["provider"] == "csv_bond_market"
    assert len(payload["assessments"]) == 1
    item = payload["assessments"][0]
    assert item["stable_id"] == "CUSIP:111111AA1"
    assert item["observation"]["price_pct_par"] == 55.0
    assert item["spread_bps"] == 1400.0
    assert len(item["implied_credit_risk"]) == 3

    summary = (workspace / "summary.md").read_text(encoding="utf-8")
    assert "Historical bond market stress" in summary
    assert "Instruments below 80% of par: 1" in summary
    assert "not physical default forecasts" in summary


def test_workspace_refuses_bond_market_without_verified_debt_input(tmp_path):
    workspace = tmp_path / "workspace"; workspace.mkdir()
    write_json(workspace / "research_packet.json", packet())
    bond_csv = tmp_path / "bond.csv"
    bond_csv.write_text(
        "date,cusip,price_pct_par\n2022-12-29,111111AA1,55\n",
        encoding="utf-8",
    )
    try:
        main([
            "--ticker", "TEST", "--analysis-date", "2022-12-31", "--workspace", str(workspace),
            "--bond-market-provider", "csv", "--bond-observations-csv", str(bond_csv),
        ])
    except ValueError as exc:
        assert "requires --debt-instruments" in str(exc)
    else:
        raise AssertionError("expected bond market without debt identity to fail")


def test_rebuilding_debt_ledger_without_bond_provider_removes_stale_bond_market(tmp_path):
    workspace = tmp_path / "workspace"; workspace.mkdir()
    write_json(workspace / "research_packet.json", packet())
    write_json(workspace / "bond_market.json", {"stale": True})
    debt_path = tmp_path / "debt.json"; write_json(debt_path, debt_payload())

    assert main(base_args(workspace, debt_path)) == 0
    assert not (workspace / "bond_market.json").exists()
    assert (workspace / "debt_instrument_ledger.json").exists()

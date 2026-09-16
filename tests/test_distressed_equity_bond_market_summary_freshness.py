from __future__ import annotations

import json

from distressed_equity.instrument_verification import debt_snapshot_verification_template
from distressed_equity.orchestration_cli import main


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def frozen_packet():
    return {
        "snapshot": {
            "ticker": "TEST",
            "cik": "0000000001",
            "company_name": "Test Co",
            "analysis_date": "2022-12-31",
            "warnings": [],
        },
        "evidence": [],
        "point_in_time_violations": [],
        "screening_draft": {
            "screening_candidate_draft": {
                "ticker": "TEST",
                "company_name": "Test Co",
                "analysis_date": "2022-12-31",
                "capital_structure": {"current_price": None},
            }
        },
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
                    "span_id": "s1",
                    "source_accession": "acc1",
                    "published_on": "2022-11-15",
                    "text": "verified note evidence",
                }
            ],
        },
    }


def verified_debt(source_packet):
    raw = {
        "instruments": [
            {
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
            }
        ]
    }
    bound = debt_snapshot_verification_template(source_packet, raw)
    bound["instruments"][0]["verification"].update(
        {
            "status": "verified",
            "verifier": "summary-test",
            "verified_on": "2026-09-16",
            "evidence_reopened": True,
        }
    )
    return bound


def test_workspace_summary_excludes_stale_trade_from_current_stress_counts(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    packet = frozen_packet()
    write_json(workspace / "research_packet.json", packet)

    debt_path = tmp_path / "debt.json"
    write_json(debt_path, verified_debt(packet["debt_instrument_source_packet"]))

    bond_csv = tmp_path / "bond.csv"
    bond_csv.write_text(
        "date,cusip,price_pct_par,yield_pct,benchmark_yield_pct,source\n"
        "2022-10-01,111111AA1,50,20,4,stale TRACE\n",
        encoding="utf-8",
    )

    assert main(
        [
            "--ticker",
            "TEST",
            "--analysis-date",
            "2022-12-31",
            "--workspace",
            str(workspace),
            "--debt-instruments",
            str(debt_path),
            "--bond-market-provider",
            "csv",
            "--bond-observations-csv",
            str(bond_csv),
            "--bond-max-staleness-days",
            "30",
        ]
    ) == 0

    summary = (workspace / "summary.md").read_text(encoding="utf-8")
    assert "Instruments with cutoff/lookback market observations: 1" in summary
    assert "Fresh observations eligible for current-stress counts: 0" in summary
    assert "Stale observations excluded from current-stress counts: 1" in summary
    assert "Instruments below 80% of par: 0" in summary
    assert "Instruments with benchmarked credit spread: 0" in summary

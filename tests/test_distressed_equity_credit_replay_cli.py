from __future__ import annotations

import json
from pathlib import Path

from distressed_equity.credit_replay_cli import main


def test_credit_replay_cli_runs_point_in_time_equity_credit_and_market_outcomes(tmp_path: Path):
    securities = tmp_path / "securities.csv"
    prices = tmp_path / "prices.csv"
    links = tmp_path / "credit_links.csv"
    bonds = tmp_path / "bonds.csv"
    output = tmp_path / "joint.json"
    markdown = tmp_path / "joint.md"

    securities.write_text(
        "security_id,symbol,name,exchange,asset_type,start_date,end_date\n"
        "sec-1,TEST,Test Co,NYSE,stock,2010-01-01,\n",
        encoding="utf-8",
    )
    prices.write_text(
        "security_id,symbol,date,close,adjusted_close\n"
        "sec-1,TEST,2019-01-02,10,10\n"
        "sec-1,TEST,2021-12-31,25,25\n"
        "sec-1,TEST,2022-12-31,5,5\n"
        "sec-1,TEST,2023-12-31,10,10\n"
        "sec-1,TEST,2024-06-30,20,20\n"
        "sec-1,TEST,2025-12-31,15,15\n",
        encoding="utf-8",
    )
    links.write_text(
        "security_id,cusip,isin,start_date,end_date,source\n"
        "sec-1,111111AA1,,2020-01-01,,point-in-time link\n",
        encoding="utf-8",
    )
    bonds.write_text(
        "date,cusip,price_pct_par,yield_pct,benchmark_yield_pct,source\n"
        "2022-12-30,111111AA1,55,20,4,TRACE normalized\n",
        encoding="utf-8",
    )

    assert main([
        "--analysis-date", "2022-12-31",
        "--securities-csv", str(securities),
        "--prices-csv", str(prices),
        "--credit-links-csv", str(links),
        "--bond-observations-csv", str(bonds),
        "--outcome-cutoff", "2026-01-31",
        "--output", str(output),
        "--markdown-output", str(markdown),
    ]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["analysis_date"] == "2022-12-31"
    assert payload["equity_candidate_count"] == 1
    assert payload["candidates_with_credit_links"] == 1
    assert payload["candidates_with_fresh_credit"] == 1
    assert payload["candidates_with_credit_stress"] == 1
    candidate = payload["candidates"][0]
    assert candidate["equity"]["security"]["security_id"] == "sec-1"
    assert candidate["equity"]["adjusted_drawdown_from_peak"] == 0.8
    assert candidate["min_fresh_price_pct_par"] == 55.0
    assert candidate["max_fresh_yield_pct"] == 20.0
    assert candidate["max_fresh_spread_bps"] == 1600.0
    assert candidate["any_credit_stress"] is True

    outcomes = payload["market_outcomes"]
    assert outcomes["outcome_cutoff"] == "2026-01-31"
    assert outcomes["case_count"] == 1
    assert outcomes["active_12m_count"] == 1
    assert outcomes["active_3y_count"] == 1
    outcome = outcomes["outcomes"][0]
    assert outcome["adjusted_price_multiple_12m"] == 2.0
    assert outcome["adjusted_price_multiple_3y"] == 3.0
    assert outcome["max_adjusted_price_multiple_within_3y"] == 4.0

    summary = markdown.read_text(encoding="utf-8")
    assert "Joint equity / credit distress replay" in summary
    assert "Market-observable outcomes" in summary
    assert "TEST" in summary
    assert "55.0" in summary
    assert "1600 bps" in summary
    assert "3.00x" in summary
    assert "4.00x" in summary
    assert "not labels for corporate survival" in summary

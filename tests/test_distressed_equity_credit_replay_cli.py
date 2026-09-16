from __future__ import annotations

import json
from pathlib import Path

from distressed_equity.credit_replay_cli import main


def test_credit_replay_cli_runs_full_point_in_time_outcome_pipeline(tmp_path: Path):
    securities = tmp_path / "securities.csv"
    prices = tmp_path / "prices.csv"
    links = tmp_path / "credit_links.csv"
    bonds = tmp_path / "bonds.csv"
    source_outcomes = tmp_path / "source_outcomes.csv"
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
    source_outcomes.write_text(
        "security_id,ticker,analysis_date,outcome_known_date,survived_12m,existing_common_survived_12m,"
        "normalized_within_3y,equity_multiple_3y,industry_group,impairment_type,leverage_bucket,evidence_refs,"
        "verification_status,verifier,verified_on,evidence_reopened,notes\n"
        "sec-1,TEST,2022-12-31,2025-12-31,true,true,true,3.0,retail,temporary,high,"
        "SEC:8-K:001;COURT:case-1,verified,reviewer,2026-01-01,true,verified sources\n",
        encoding="utf-8",
    )

    assert main([
        "--analysis-date", "2022-12-31",
        "--securities-csv", str(securities),
        "--prices-csv", str(prices),
        "--credit-links-csv", str(links),
        "--bond-observations-csv", str(bonds),
        "--outcome-cutoff", "2026-01-31",
        "--source-outcomes-csv", str(source_outcomes),
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

    base_rates = {group["group"]: group for group in payload["market_base_rates"]["groups"]}
    stress = base_rates["fresh_credit_stress"]
    assert stress["case_count"] == 1
    assert stress["same_security_active_12m_rate"] == 1.0
    assert stress["same_security_active_3y_rate"] == 1.0
    assert stress["three_x_3y_endpoint_rate"] == 1.0
    assert stress["three_x_observed_within_3y_rate"] == 1.0
    assert stress["median_3y_endpoint_multiple"] == 3.0
    assert stress["median_max_observed_multiple_within_3y"] == 4.0

    source_rates = payload["source_outcome_base_rates"]
    assert source_rates["knowledge_cutoff"] == "2026-01-31"
    assert source_rates["known_source_label_count"] == 1
    source_groups = {group["group"]: group for group in source_rates["groups"]}
    source_stress = source_groups["fresh_credit_stress"]
    assert source_stress["cohort_case_count"] == 1
    assert source_stress["source_labeled_case_count"] == 1
    assert source_stress["survived_12m_rate"] == 1.0
    assert source_stress["existing_common_survival_rate"] == 1.0
    assert source_stress["normalized_within_3y_rate"] == 1.0
    assert source_stress["three_x_3y_rate"] == 1.0
    assert source_stress["median_equity_multiple_3y"] == 3.0

    summary = markdown.read_text(encoding="utf-8")
    assert "Joint equity / credit distress replay" in summary
    assert "Market-observable outcomes" in summary
    assert "Market-observable base-rate comparison" in summary
    assert "Verified legal / business outcome base rates" in summary
    assert "fresh_credit_stress" in summary
    assert "TEST" in summary
    assert "55.0" in summary
    assert "1600 bps" in summary
    assert "3.00x" in summary
    assert "4.00x" in summary
    assert "not labels for corporate survival" in summary
    assert "not bankruptcy/common-survival rates" in summary
    assert "Missing labels stay missing" in summary


def test_source_outcomes_require_explicit_outcome_cutoff(tmp_path: Path):
    securities = tmp_path / "securities.csv"
    prices = tmp_path / "prices.csv"
    links = tmp_path / "credit_links.csv"
    bonds = tmp_path / "bonds.csv"
    labels = tmp_path / "labels.csv"
    securities.write_text(
        "security_id,symbol,name,exchange,asset_type,start_date,end_date\nsec-1,T,T,NYSE,stock,2010-01-01,\n",
        encoding="utf-8",
    )
    prices.write_text(
        "security_id,symbol,date,close,adjusted_close\nsec-1,T,2022-12-31,5,5\n",
        encoding="utf-8",
    )
    links.write_text(
        "security_id,cusip,isin,start_date,end_date,source\nsec-1,111111AA1,,2020-01-01,,x\n",
        encoding="utf-8",
    )
    bonds.write_text(
        "date,cusip,price_pct_par,yield_pct,source\n2022-12-30,111111AA1,55,20,x\n",
        encoding="utf-8",
    )
    labels.write_text("placeholder\n", encoding="utf-8")

    try:
        main([
            "--analysis-date", "2022-12-31",
            "--securities-csv", str(securities),
            "--prices-csv", str(prices),
            "--credit-links-csv", str(links),
            "--bond-observations-csv", str(bonds),
            "--source-outcomes-csv", str(labels),
        ])
    except ValueError as exc:
        assert "requires --outcome-cutoff" in str(exc)
    else:
        raise AssertionError("expected source outcome labels without a knowledge cutoff to be rejected")

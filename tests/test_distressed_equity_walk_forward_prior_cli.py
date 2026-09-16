from __future__ import annotations

import json
from pathlib import Path

import pytest

from distressed_equity.walk_forward_prior_cli import main


def test_walk_forward_prior_cli_replays_historical_dates_without_future_outcome_leakage(tmp_path: Path):
    securities = tmp_path / "securities.csv"
    prices = tmp_path / "prices.csv"
    links = tmp_path / "links.csv"
    bonds = tmp_path / "bonds.csv"
    outcomes = tmp_path / "outcomes.csv"
    features = tmp_path / "features.csv"
    output = tmp_path / "priors.json"
    markdown = tmp_path / "priors.md"

    securities.write_text(
        "security_id,symbol,name,exchange,asset_type,start_date,end_date\n"
        "A,A,Historical A,NYSE,stock,2010-01-01,2020-12-31\n"
        "B,B,Target B,NYSE,stock,2021-01-01,\n",
        encoding="utf-8",
    )
    prices.write_text(
        "security_id,symbol,date,close,adjusted_close\n"
        "A,A,2018-12-31,20,20\n"
        "A,A,2019-12-31,4,4\n"
        "B,B,2021-12-31,25,25\n"
        "B,B,2022-12-31,5,5\n",
        encoding="utf-8",
    )
    links.write_text(
        "security_id,cusip,isin,start_date,end_date,source\n"
        "A,111111AA1,,2018-01-01,2020-12-31,point-in-time link A\n"
        "B,222222BB2,,2021-01-01,,point-in-time link B\n",
        encoding="utf-8",
    )
    bonds.write_text(
        "date,cusip,price_pct_par,yield_pct,benchmark_yield_pct,source\n"
        "2019-12-30,111111AA1,55,20,4,TRACE normalized\n"
        "2022-12-30,222222BB2,60,18,4,TRACE normalized\n",
        encoding="utf-8",
    )
    outcomes.write_text(
        "security_id,ticker,analysis_date,outcome_known_date,survived_12m,existing_common_survived_12m,"
        "normalized_within_3y,equity_multiple_3y,industry_group,impairment_type,leverage_bucket,evidence_refs,"
        "verification_status,verifier,verified_on,evidence_reopened,notes\n"
        "A,A,2019-12-31,2020-12-31,true,true,,,retail,temporary,high,"
        "SEC:A:OUTCOME,verified,reviewer,2026-09-16,true,known before target T0\n",
        encoding="utf-8",
    )
    features.write_text(
        "security_id,analysis_date,evidence_known_date,liquidity_runway_months,nearest_maturity_months,"
        "covenant_headroom_pct,net_leverage,impairment_type,evidence_refs,verification_status,verifier,verified_on,"
        "evidence_reopened,notes\n"
        "A,2019-12-31,2019-12-30,18,30,15,5,temporary,SEC:A:T0,verified,reviewer,2026-09-16,true,historical feature\n"
        "B,2022-12-31,2022-12-30,18,30,15,5,temporary,SEC:B:T0,verified,reviewer,2026-09-16,true,target feature\n",
        encoding="utf-8",
    )

    assert main([
        "--analysis-date", "2022-12-31",
        "--prior-analysis-date", "2019-12-31",
        "--securities-csv", str(securities),
        "--prices-csv", str(prices),
        "--credit-links-csv", str(links),
        "--bond-observations-csv", str(bonds),
        "--source-outcomes-csv", str(outcomes),
        "--survival-features-csv", str(features),
        "--feature-dimensions", "liquidity_runway",
        "--output", str(output),
        "--markdown-output", str(markdown),
    ]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["target_analysis_date"] == "2022-12-31"
    assert payload["historical_run_dates"] == ["2019-12-31"]
    assert payload["historical_case_count_before_episode_dedup"] == 1
    assert payload["historical_case_count_after_episode_dedup"] == 1
    assert payload["source_labels_known_by_target"] == 1
    assert payload["target_case_count"] == 1

    prior = payload["priors"][0]
    assert prior["security_id"] == "B"
    assert prior["credit_group"] == "fresh_credit_stress"
    assert prior["feature_buckets"]["liquidity_runway"] == "12-<24m"

    credit = prior["credit_group_prior"]
    assert credit["calibration_case_count"] == 1
    assert credit["source_labeled_case_count"] == 1
    assert credit["survived_12m"]["successes"] == 1
    assert credit["survived_12m"]["resolved_n"] == 1
    assert credit["survived_12m"]["rate"] == 1.0
    assert credit["survived_12m"]["small_sample"] is True
    assert 0.0 < credit["survived_12m"]["wilson_95_low"] < 1.0
    assert credit["survived_12m"]["wilson_95_high"] == 1.0

    liquidity = prior["feature_priors"][0]
    assert liquidity["basis"] == "liquidity_runway"
    assert liquidity["bucket"] == "12-<24m"
    assert liquidity["calibration_case_count"] == 1
    assert liquidity["existing_common_survival"]["rate"] == 1.0

    summary = markdown.read_text(encoding="utf-8")
    assert "Walk-forward distress priors" in summary
    assert "2019-12-31" in summary
    assert "B" in summary
    assert "12-<24m" in summary
    assert "small-n" in summary
    assert "ex-ante empirical priors" in summary
    assert "Target-cohort outcomes" in summary


def test_walk_forward_prior_cli_rejects_nonhistorical_calibration_dates():
    required = [
        "--analysis-date", "2022-12-31",
        "--prior-analysis-date", "2022-12-31",
        "--securities-csv", "missing-securities.csv",
        "--prices-csv", "missing-prices.csv",
        "--credit-links-csv", "missing-links.csv",
        "--bond-observations-csv", "missing-bonds.csv",
        "--source-outcomes-csv", "missing-outcomes.csv",
        "--survival-features-csv", "missing-features.csv",
    ]
    with pytest.raises(ValueError, match="must strictly precede"):
        main(required)

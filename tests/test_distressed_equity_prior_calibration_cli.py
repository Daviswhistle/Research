from __future__ import annotations

import json
from pathlib import Path

import pytest

from distressed_equity.prior_calibration_cli import main


def test_prior_calibration_cli_replays_grid_and_scores_ex_ante_priors(tmp_path: Path):
    securities = tmp_path / "securities.csv"
    prices = tmp_path / "prices.csv"
    links = tmp_path / "links.csv"
    bonds = tmp_path / "bonds.csv"
    outcomes = tmp_path / "outcomes.csv"
    features = tmp_path / "features.csv"
    output = tmp_path / "calibration.json"
    markdown = tmp_path / "calibration.md"

    securities.write_text(
        "security_id,symbol,name,exchange,asset_type,start_date,end_date\n"
        "A,A,Case A,NYSE,stock,2010-01-01,2019-12-31\n"
        "B,B,Case B,NYSE,stock,2020-01-01,2021-12-31\n"
        "C,C,Case C,NYSE,stock,2022-01-01,\n",
        encoding="utf-8",
    )
    prices.write_text(
        "security_id,symbol,date,close,adjusted_close\n"
        "A,A,2017-12-31,25,25\n"
        "A,A,2018-12-31,5,5\n"
        "B,B,2020-01-02,25,25\n"
        "B,B,2020-12-31,5,5\n"
        "C,C,2022-01-02,25,25\n"
        "C,C,2022-12-31,5,5\n",
        encoding="utf-8",
    )
    links.write_text(
        "security_id,cusip,isin,start_date,end_date,source\n"
        "A,111111AA1,,2018-01-01,2019-12-31,link A\n"
        "B,222222BB2,,2020-01-01,2021-12-31,link B\n"
        "C,333333CC3,,2022-01-01,,link C\n",
        encoding="utf-8",
    )
    bonds.write_text(
        "date,cusip,price_pct_par,yield_pct,benchmark_yield_pct,source\n"
        "2018-12-30,111111AA1,55,20,4,TRACE A\n"
        "2020-12-30,222222BB2,55,20,4,TRACE B\n"
        "2022-12-30,333333CC3,55,20,4,TRACE C\n",
        encoding="utf-8",
    )
    outcomes.write_text(
        "security_id,ticker,analysis_date,outcome_known_date,survived_12m,existing_common_survived_12m,"
        "normalized_within_3y,equity_multiple_3y,industry_group,impairment_type,leverage_bucket,evidence_refs,"
        "verification_status,verifier,verified_on,evidence_reopened,notes\n"
        "A,A,2018-12-31,2019-12-31,true,true,,,x,temporary,high,SEC:A:OUT,verified,reviewer,2026-09-16,true,A survives\n"
        "B,B,2020-12-31,2021-12-31,false,false,,,x,temporary,high,SEC:B:OUT,verified,reviewer,2026-09-16,true,B fails\n"
        "C,C,2022-12-31,2023-12-31,true,true,,,x,temporary,high,SEC:C:OUT,verified,reviewer,2026-09-16,true,C survives\n",
        encoding="utf-8",
    )
    features.write_text(
        "security_id,analysis_date,evidence_known_date,liquidity_runway_months,nearest_maturity_months,"
        "covenant_headroom_pct,net_leverage,impairment_type,evidence_refs,verification_status,verifier,verified_on,"
        "evidence_reopened,notes\n"
        "A,2018-12-31,2018-12-30,18,30,15,5,temporary,SEC:A:T0,verified,reviewer,2026-09-16,true,A features\n"
        "B,2020-12-31,2020-12-30,18,30,15,5,temporary,SEC:B:T0,verified,reviewer,2026-09-16,true,B features\n"
        "C,2022-12-31,2022-12-30,18,30,15,5,temporary,SEC:C:T0,verified,reviewer,2026-09-16,true,C features\n",
        encoding="utf-8",
    )

    assert main([
        "--analysis-date", "2018-12-31",
        "--analysis-date", "2020-12-31",
        "--analysis-date", "2022-12-31",
        "--evaluation-cutoff", "2024-12-31",
        "--securities-csv", str(securities),
        "--prices-csv", str(prices),
        "--credit-links-csv", str(links),
        "--bond-observations-csv", str(bonds),
        "--source-outcomes-csv", str(outcomes),
        "--survival-features-csv", str(features),
        "--exchange", "nyse",
        "--exchange", "NYSE",
        "--feature-dimensions", "liquidity_runway",
        "--calibration-bin-width", "0.5",
        "--code-revision", "test-rev",
        "--output", str(output),
        "--markdown-output", str(markdown),
    ]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    manifest = payload["experiment_manifest"]
    assert manifest["pipeline"] == "prior_calibration"
    assert manifest["code_revision"] == "test-rev"
    assert manifest["code_revision_source"] == "explicit"
    assert manifest["reproducibility_complete"] is True
    assert len(manifest["data_config_fingerprint"]) == 64
    assert len(manifest["experiment_fingerprint"]) == 64
    assert manifest["config"]["analysis_dates"] == ["2018-12-31", "2020-12-31", "2022-12-31"]
    assert manifest["config"]["evaluation_cutoff"] == "2024-12-31"
    assert manifest["config"]["exchanges"] == ["NYSE"]
    assert manifest["config"]["feature_dimensions"] == ["liquidity_runway"]
    assert manifest["config"]["stability_dimensions"] == [
        "target_date", "target_year", "credit_group", "calibration_n_band", "basis_bucket"
    ]
    assert manifest["config"]["calibration_bin_width"] == 0.5

    assert payload["analysis_dates"] == ["2018-12-31", "2020-12-31", "2022-12-31"]
    assert payload["prior_run_count"] == 2
    assert payload["target_case_count"] == 2
    assert len(payload["walk_forward_prior_runs"]) == 2

    summaries = {(item["basis"], item["metric"]): item for item in payload["summaries"]}
    survival = summaries[("credit_group", "survived_12m")]
    assert survival["forecast_count"] == 2
    assert survival["mean_predicted_probability"] == pytest.approx(0.75)
    assert survival["observed_rate"] == 0.5
    assert survival["brier_score"] == pytest.approx(0.625)
    assert survival["small_sample_forecast_count"] == 2

    liquidity = summaries[("liquidity_runway", "survived_12m")]
    assert liquidity["forecast_count"] == 2
    assert liquidity["brier_score"] == pytest.approx(0.625)

    observations = [
        item
        for item in payload["observations"]
        if item["basis"] == "credit_group" and item["metric"] == "survived_12m"
    ]
    assert [(item["security_id"], item["predicted_probability"], item["actual"]) for item in observations] == [
        ("B", 1.0, False),
        ("C", 0.5, True),
    ]

    stability = payload["stability"]
    assert stability["forecast_observation_count"] == payload["forecast_observation_count"]
    assert stability["slice_count"] > 0
    stability_keys = {
        (item["dimension"], item["basis"], item["metric"], item["value"])
        for item in stability["slices"]
    }
    assert ("target_year", "credit_group", "survived_12m", "2020") in stability_keys
    assert ("target_year", "credit_group", "survived_12m", "2022") in stability_keys
    assert ("credit_group", "credit_group", "survived_12m", "fresh_credit_stress") in stability_keys
    assert ("basis_bucket", "liquidity_runway", "survived_12m", "12-<24m") in stability_keys

    summary = markdown.read_text(encoding="utf-8")
    assert "Walk-forward prior calibration" in summary
    assert "experiment fingerprint" in summary
    assert manifest["experiment_fingerprint"] in summary
    assert "Brier score" in summary
    assert "0.6250" in summary
    assert "credit_group" in summary
    assert "liquidity_runway" in summary
    assert "Stability slices" in summary
    assert "calibration_n_band" in summary
    assert "separate forecast family" in summary


def test_prior_calibration_cli_requires_at_least_two_analysis_dates():
    with pytest.raises(ValueError, match="at least two unique"):
        main([
            "--analysis-date", "2022-12-31",
            "--evaluation-cutoff", "2024-12-31",
            "--securities-csv", "missing.csv",
            "--prices-csv", "missing.csv",
            "--credit-links-csv", "missing.csv",
            "--bond-observations-csv", "missing.csv",
            "--source-outcomes-csv", "missing.csv",
            "--survival-features-csv", "missing.csv",
        ])

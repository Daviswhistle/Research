from __future__ import annotations

import json
from pathlib import Path

from distressed_equity.population_coverage_cli import main


def test_population_coverage_cli_exposes_missing_credit_outcome_feature_and_evaluability_coverage(tmp_path: Path):
    securities = tmp_path / "securities.csv"
    prices = tmp_path / "prices.csv"
    links = tmp_path / "links.csv"
    bonds = tmp_path / "bonds.csv"
    outcomes = tmp_path / "outcomes.csv"
    features = tmp_path / "features.csv"
    output = tmp_path / "coverage.json"
    markdown = tmp_path / "coverage.md"

    securities.write_text(
        "security_id,symbol,name,exchange,asset_type,start_date,end_date\n"
        "A,A,Case A,NYSE,stock,2010-01-01,2019-12-31\n"
        "B,B,Case B,NYSE,stock,2020-01-01,\n"
        "C,C,Evaluable Noncandidate,NYSE,stock,2020-01-01,\n"
        "D,D,Unevaluable Missing Prices,NYSE,stock,2020-01-01,\n",
        encoding="utf-8",
    )
    prices.write_text(
        "security_id,symbol,date,close,adjusted_close\n"
        "A,A,2017-12-31,25,25\n"
        "A,A,2018-12-31,5,5\n"
        "B,B,2020-01-02,25,25\n"
        "B,B,2020-12-31,5,5\n"
        "C,C,2020-01-02,25,25\n"
        "C,C,2020-12-31,20,20\n",
        encoding="utf-8",
    )
    links.write_text(
        "security_id,cusip,isin,start_date,end_date,source\n"
        "A,111111AA1,,2018-01-01,2019-12-31,link A\n",
        encoding="utf-8",
    )
    bonds.write_text(
        "date,cusip,price_pct_par,yield_pct,benchmark_yield_pct,source\n"
        "2018-12-30,111111AA1,55,20,4,TRACE A\n",
        encoding="utf-8",
    )
    outcomes.write_text(
        "security_id,ticker,analysis_date,outcome_known_date,survived_12m,existing_common_survived_12m,"
        "normalized_within_3y,equity_multiple_3y,evidence_refs,verification_status,verifier,verified_on,evidence_reopened\n"
        "A,A,2018-12-31,2019-12-31,true,,,,SEC:A:SURVIVAL,verified,reviewer,2022-12-31,true\n",
        encoding="utf-8",
    )
    features.write_text(
        "security_id,analysis_date,evidence_known_date,liquidity_runway_months,nearest_maturity_months,"
        "covenant_headroom_pct,net_leverage,impairment_type,evidence_refs,verification_status,verifier,verified_on,"
        "evidence_reopened,notes\n"
        "A,2018-12-31,2018-12-30,18,30,15,5,temporary,SEC:A:T0,verified,reviewer,2022-12-31,true,A features\n"
        "B,2020-12-31,2020-12-30,6,,,,,SEC:B:T0,verified,reviewer,2022-12-31,true,B partial features\n",
        encoding="utf-8",
    )

    assert main([
        "--analysis-date", "2018-12-31",
        "--analysis-date", "2020-12-31",
        "--outcome-coverage-cutoff", "2022-12-31",
        "--securities-csv", str(securities),
        "--prices-csv", str(prices),
        "--credit-links-csv", str(links),
        "--bond-observations-csv", str(bonds),
        "--source-outcomes-csv", str(outcomes),
        "--survival-features-csv", str(features),
        "--exchange", "nyse",
        "--exchange", "NYSE",
        "--code-revision", "test-rev",
        "--output", str(output),
        "--markdown-output", str(markdown),
    ]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    manifest = payload["experiment_manifest"]
    assert manifest["pipeline"] == "population_coverage"
    assert manifest["code_revision"] == "test-rev"
    assert manifest["code_revision_source"] == "explicit"
    assert manifest["reproducibility_complete"] is True
    assert len(manifest["data_config_fingerprint"]) == 64
    assert len(manifest["experiment_fingerprint"]) == 64
    assert {item["role"] for item in manifest["input_files"]} == {
        "securities", "prices", "credit_links", "bond_observations", "source_outcomes", "survival_features"
    }
    assert manifest["config"]["analysis_dates"] == ["2018-12-31", "2020-12-31"]
    assert manifest["config"]["outcome_coverage_cutoff"] == "2022-12-31"
    assert manifest["config"]["exchanges"] == ["NYSE"]

    assert payload["analysis_dates"] == ["2018-12-31", "2020-12-31"]
    assert payload["total_case_observations"] == 2
    assert payload["unique_security_count"] == 2
    assert payload["repeated_case_observation_count"] == 0

    rows = {row["analysis_date"]: row for row in payload["rows"]}
    first = rows["2018-12-31"]
    assert first["historical_universe_size"] == 1
    assert first["scanned_security_count"] == 1
    assert first["evaluable_security_count"] == 1
    assert first["unevaluable_security_count"] == 0
    assert first["evaluable_non_candidate_count"] == 0
    assert first["evaluable_coverage_of_scanned"] == 1.0
    assert first["distress_rate_of_evaluable"] == 1.0
    assert first["equity_distress_case_count"] == 1
    assert first["credit_linked_case_count"] == 1
    assert first["fresh_credit_case_count"] == 1
    assert first["credit_stress_case_count"] == 1
    assert first["credit_link_coverage_of_distress"] == 1.0
    assert first["fresh_credit_coverage_of_linked"] == 1.0
    assert first["source_labeled_case_count"] == 1
    assert first["survived_12m_labeled_count"] == 1
    assert first["survived_12m_horizon_eligible_count"] == 1
    assert first["survived_12m_missing_among_horizon_eligible_count"] == 0
    assert first["survived_12m_label_coverage_of_horizon_eligible"] == 1.0
    assert first["normalized_3y_horizon_eligible_count"] == 1
    assert first["normalized_3y_labeled_count"] == 0
    assert first["normalized_3y_missing_among_horizon_eligible_count"] == 1
    assert first["normalized_3y_label_coverage_of_horizon_eligible"] == 0.0
    assert first["t0_feature_snapshot_count"] == 1
    assert first["liquidity_runway_feature_count"] == 1
    assert first["nearest_maturity_feature_count"] == 1
    assert first["covenant_headroom_feature_count"] == 1
    assert first["net_leverage_feature_count"] == 1
    assert first["impairment_type_feature_count"] == 1
    assert first["feature_snapshot_coverage_of_distress"] == 1.0

    second = rows["2020-12-31"]
    assert second["historical_universe_size"] == 3
    assert second["scanned_security_count"] == 3
    assert second["evaluable_security_count"] == 2
    assert second["unevaluable_security_count"] == 1
    assert second["evaluable_non_candidate_count"] == 1
    assert second["evaluable_coverage_of_scanned"] == 2 / 3
    assert second["distress_rate_of_evaluable"] == 0.5
    assert second["skip_reason_counts"] == [
        {
            "reason": "drawdown below configured distress threshold",
            "count": 1,
            "classification": "evaluable_non_candidate",
        },
        {
            "reason": "no cutoff-date raw price",
            "count": 1,
            "classification": "unevaluable",
        },
    ]
    assert second["equity_distress_case_count"] == 1
    assert second["credit_linked_case_count"] == 0
    assert second["fresh_credit_case_count"] == 0
    assert second["credit_stress_case_count"] == 0
    assert second["credit_link_coverage_of_distress"] == 0.0
    assert second["fresh_credit_coverage_of_linked"] is None
    assert second["source_labeled_case_count"] == 0
    assert second["source_label_coverage_of_distress"] == 0.0
    assert second["survived_12m_horizon_eligible_count"] == 1
    assert second["survived_12m_labeled_count"] == 0
    assert second["survived_12m_missing_among_horizon_eligible_count"] == 1
    assert second["survived_12m_right_censored_count"] == 0
    assert second["normalized_3y_horizon_eligible_count"] == 0
    assert second["normalized_3y_labeled_count"] == 0
    assert second["normalized_3y_early_resolved_count"] == 0
    assert second["normalized_3y_right_censored_count"] == 1
    assert second["normalized_3y_missing_among_horizon_eligible_count"] == 0
    assert second["normalized_3y_label_coverage_of_horizon_eligible"] is None
    assert second["equity_multiple_3y_horizon_eligible_count"] == 0
    assert second["equity_multiple_3y_right_censored_count"] == 1
    assert second["t0_feature_snapshot_count"] == 1
    assert second["liquidity_runway_feature_count"] == 1
    assert second["nearest_maturity_feature_count"] == 0
    assert second["covenant_headroom_feature_count"] == 0
    assert second["net_leverage_feature_count"] == 0
    assert second["impairment_type_feature_count"] == 0

    assert any("unevaluable" in warning for warning in payload["warnings"])

    summary = markdown.read_text(encoding="utf-8")
    assert "Historical distress population coverage" in summary
    assert "experiment fingerprint" in summary
    assert manifest["experiment_fingerprint"] in summary
    assert "Market evaluability" in summary
    assert "Evaluable non-candidate" in summary
    assert "Right-censored" in summary
    assert "Missing among eligible" in summary
    assert "ex-post dataset coverage audit" in summary


def test_population_coverage_rejects_analysis_date_after_coverage_cutoff(tmp_path: Path):
    try:
        main([
            "--analysis-date", "2024-12-31",
            "--outcome-coverage-cutoff", "2023-12-31",
            "--securities-csv", str(tmp_path / "missing.csv"),
            "--prices-csv", str(tmp_path / "missing.csv"),
            "--credit-links-csv", str(tmp_path / "missing.csv"),
            "--bond-observations-csv", str(tmp_path / "missing.csv"),
            "--source-outcomes-csv", str(tmp_path / "missing.csv"),
            "--survival-features-csv", str(tmp_path / "missing.csv"),
        ])
    except ValueError as exc:
        assert "analysis dates must be on or before" in str(exc)
    else:
        raise AssertionError("expected analysis date after coverage cutoff to be rejected")

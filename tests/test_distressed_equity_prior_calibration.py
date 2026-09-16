from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from distressed_equity.prior_calibration import (
    evaluate_walk_forward_priors,
    prior_calibration_to_dict,
)
from distressed_equity.rate_diagnostics import binomial_rate_diagnostic
from distressed_equity.source_outcomes import CsvSourceBackedOutcomeIndex
from distressed_equity.walk_forward_priors import (
    WalkForwardCasePrior,
    WalkForwardPriorRun,
    WalkForwardPriorStratum,
)


FIELDS = [
    "security_id", "ticker", "analysis_date", "outcome_known_date",
    "survived_12m", "survived_12m_known_date", "survived_12m_evidence_refs",
    "existing_common_survived_12m", "existing_common_survived_12m_known_date", "existing_common_survived_12m_evidence_refs",
    "normalized_within_3y", "normalized_within_3y_known_date", "normalized_within_3y_evidence_refs",
    "equity_multiple_3y", "equity_multiple_3y_known_date", "equity_multiple_3y_evidence_refs",
    "industry_group", "impairment_type", "leverage_bucket", "evidence_refs",
    "verification_status", "verifier", "verified_on", "evidence_reopened", "notes",
]


def _write(path: Path, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(",".join(FIELDS) + "\n")
        for row in rows:
            handle.write(",".join(str(row.get(field, "")) for field in FIELDS) + "\n")


def _outcome(
    security_id: str,
    analysis_date: str,
    *,
    survived: bool,
    common: bool,
    multiple: float,
    multiple_known: str,
) -> dict[str, str]:
    normalized = multiple >= 3.0
    return {
        "security_id": security_id,
        "ticker": security_id,
        "analysis_date": analysis_date,
        "outcome_known_date": multiple_known,
        "survived_12m": str(survived).lower(),
        "survived_12m_known_date": f"{int(analysis_date[:4]) + 1}{analysis_date[4:]}",
        "survived_12m_evidence_refs": f"SEC:{security_id}:SURVIVAL",
        "existing_common_survived_12m": str(common).lower(),
        "existing_common_survived_12m_known_date": f"{int(analysis_date[:4]) + 1}{analysis_date[4:]}",
        "existing_common_survived_12m_evidence_refs": f"SEC:{security_id}:COMMON",
        "normalized_within_3y": str(normalized).lower(),
        "normalized_within_3y_known_date": multiple_known,
        "normalized_within_3y_evidence_refs": f"SEC:{security_id}:NORMALIZED",
        "equity_multiple_3y": str(multiple),
        "equity_multiple_3y_known_date": multiple_known,
        "equity_multiple_3y_evidence_refs": f"SEC:{security_id}:MULTIPLE",
        "industry_group": "x",
        "impairment_type": "temporary",
        "leverage_bucket": "high",
        "evidence_refs": f"SEC:{security_id}:FINAL",
        "verification_status": "verified",
        "verifier": "reviewer",
        "verified_on": "2026-09-16",
        "evidence_reopened": "true",
        "notes": "",
    }


def _diag(rate: float, n: int = 20):
    return binomial_rate_diagnostic(rate, n, small_sample_n=30)


def _stratum(basis: str, bucket: str, rate: float) -> WalkForwardPriorStratum:
    return WalkForwardPriorStratum(
        basis=basis,
        bucket=bucket,
        credit_group="fresh_credit_stress",
        calibration_case_count=20,
        source_labeled_case_count=20,
        survived_12m=_diag(rate),
        existing_common_survival=_diag(rate),
        normalized_within_3y=_diag(rate),
        three_x_3y=_diag(rate),
        median_equity_multiple_3y=2.0,
        case_keys=(),
    )


def _case(security_id: str, when: date, probability: float) -> WalkForwardCasePrior:
    return WalkForwardCasePrior(
        security_id=security_id,
        symbol=security_id,
        analysis_date=when,
        credit_group="fresh_credit_stress",
        feature_buckets=(("liquidity_runway", "12-<24m"),),
        credit_group_prior=_stratum("credit_group", "fresh_credit_stress", probability),
        feature_priors=(_stratum("liquidity_runway", "12-<24m", probability),),
        warnings=(),
    )


def _run(when: date, *cases: WalkForwardCasePrior) -> WalkForwardPriorRun:
    return WalkForwardPriorRun(
        target_analysis_date=when,
        historical_run_dates=(date(2018, 12, 31),),
        episode_gap_days=365,
        historical_case_count_before_episode_dedup=20,
        historical_case_count_after_episode_dedup=20,
        source_labels_known_by_target=20,
        target_case_count=len(cases),
        priors=tuple(cases),
        semantics=(),
    )


def test_prior_calibration_keeps_forecast_families_separate_and_computes_brier(tmp_path: Path):
    outcomes_path = tmp_path / "outcomes.csv"
    _write(outcomes_path, [
        _outcome("A", "2020-12-31", survived=True, common=True, multiple=4.0, multiple_known="2023-12-31"),
        _outcome("B", "2020-12-31", survived=False, common=False, multiple=0.5, multiple_known="2023-12-31"),
    ])
    prior_run = _run(
        date(2020, 12, 31),
        _case("A", date(2020, 12, 31), 0.8),
        _case("B", date(2020, 12, 31), 0.4),
    )

    report = evaluate_walk_forward_priors(
        [prior_run],
        CsvSourceBackedOutcomeIndex(outcomes_path),
        date(2024, 12, 31),
        bin_width=0.2,
    )
    summaries = {(item.basis, item.metric): item for item in report.summaries}
    survival = summaries[("credit_group", "survived_12m")]
    assert survival.forecast_count == 2
    assert survival.mean_predicted_probability == pytest.approx(0.6)
    assert survival.observed_rate == 0.5
    assert survival.brier_score == pytest.approx(0.10)
    assert survival.small_sample_forecast_count == 2
    assert survival.median_calibration_resolved_n == 20

    feature_survival = summaries[("liquidity_runway", "survived_12m")]
    assert feature_survival.forecast_count == 2
    assert feature_survival.brier_score == pytest.approx(0.10)

    normalized = summaries[("credit_group", "normalized_within_3y")]
    assert normalized.forecast_count == 2
    assert normalized.observed_rate == 0.5
    assert normalized.brier_score == pytest.approx(0.10)

    three_x = summaries[("credit_group", "three_x_3y")]
    assert three_x.forecast_count == 2
    assert three_x.observed_rate == 0.5
    assert three_x.brier_score == pytest.approx(0.10)

    # 0.8 falls into [0.8, 1.0], 0.4 into [0.4, 0.6).
    bins = {round(item.lower_bound, 1): item for item in survival.bins}
    assert bins[0.4].forecast_count == 1
    assert bins[0.4].mean_predicted_probability == pytest.approx(0.4)
    assert bins[0.4].observed_rate == 0.0
    assert bins[0.8].forecast_count == 1
    assert bins[0.8].mean_predicted_probability == pytest.approx(0.8)
    assert bins[0.8].observed_rate == 1.0

    payload = prior_calibration_to_dict(report)
    assert payload["forecast_observation_count"] == 16  # 2 cases × 2 bases × 4 metrics
    assert any("evaluated separately" in item for item in payload["semantics"])
    assert any("every evaluated forecast" in item for item in payload["warnings"])


def test_evaluation_cutoff_masks_later_three_year_outcome_but_keeps_known_12m_metric(tmp_path: Path):
    outcomes_path = tmp_path / "outcomes.csv"
    _write(outcomes_path, [
        _outcome("A", "2020-12-31", survived=True, common=True, multiple=4.0, multiple_known="2023-12-31"),
    ])
    prior_run = _run(date(2020, 12, 31), _case("A", date(2020, 12, 31), 0.8))

    report = evaluate_walk_forward_priors(
        [prior_run],
        CsvSourceBackedOutcomeIndex(outcomes_path),
        date(2022, 6, 30),
        bin_width=0.5,
    )
    metrics = {(row.basis, row.metric) for row in report.observations}
    assert ("credit_group", "survived_12m") in metrics
    assert ("credit_group", "existing_common_survival") in metrics
    assert ("credit_group", "normalized_within_3y") not in metrics
    assert ("credit_group", "three_x_3y") not in metrics
    assert ("liquidity_runway", "three_x_3y") not in metrics


def test_prior_calibration_rejects_bin_width_that_does_not_partition_unit_interval(tmp_path: Path):
    outcomes_path = tmp_path / "outcomes.csv"
    _write(outcomes_path, [])
    with pytest.raises(ValueError, match="divide 1.0 exactly"):
        evaluate_walk_forward_priors(
            [], CsvSourceBackedOutcomeIndex(outcomes_path), date(2024, 1, 1), bin_width=0.3
        )

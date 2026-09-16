from __future__ import annotations

from datetime import date

import pytest

from distressed_equity.calibration_stability import (
    calibration_stability_to_dict,
    evaluate_calibration_stability,
)
from distressed_equity.prior_calibration import (
    PriorCalibrationReport,
    PriorForecastObservation,
)


def _observation(
    security_id: str,
    when: date,
    *,
    basis: str,
    bucket: str,
    credit_group: str,
    probability: float,
    resolved_n: int,
    actual: bool,
) -> PriorForecastObservation:
    return PriorForecastObservation(
        security_id=security_id,
        symbol=security_id,
        target_analysis_date=when,
        basis=basis,
        bucket=bucket,
        credit_group=credit_group,
        metric="survived_12m",
        predicted_probability=probability,
        calibration_resolved_n=resolved_n,
        calibration_successes=round(probability * resolved_n),
        calibration_wilson_95_low=None,
        calibration_wilson_95_high=None,
        calibration_small_sample=resolved_n < 30,
        actual=actual,
        squared_error=(probability - (1.0 if actual else 0.0)) ** 2,
    )


def _report() -> PriorCalibrationReport:
    observations = (
        _observation(
            "A", date(2020, 12, 31), basis="credit_group", bucket="fresh_credit_stress",
            credit_group="fresh_credit_stress", probability=0.8, resolved_n=4, actual=True,
        ),
        _observation(
            "B", date(2020, 12, 31), basis="credit_group", bucket="fresh_credit_stress",
            credit_group="fresh_credit_stress", probability=0.4, resolved_n=20, actual=False,
        ),
        _observation(
            "C", date(2021, 12, 31), basis="credit_group", bucket="fresh_credit_no_stress",
            credit_group="fresh_credit_no_stress", probability=0.7, resolved_n=50, actual=False,
        ),
        _observation(
            "D", date(2021, 12, 31), basis="credit_group", bucket="fresh_credit_no_stress",
            credit_group="fresh_credit_no_stress", probability=0.2, resolved_n=120, actual=True,
        ),
        _observation(
            "A", date(2020, 12, 31), basis="liquidity_runway", bucket="12-<24m",
            credit_group="fresh_credit_stress", probability=0.6, resolved_n=20, actual=True,
        ),
    )
    return PriorCalibrationReport(
        evaluation_cutoff=date(2026, 9, 17),
        prior_run_count=2,
        target_case_count=4,
        forecast_observation_count=len(observations),
        summaries=(),
        observations=observations,
        semantics=(),
        warnings=(),
    )


def test_calibration_stability_slices_keep_basis_families_separate():
    report = evaluate_calibration_stability(_report())
    rows = {
        (item.dimension, item.basis, item.metric, item.value): item
        for item in report.slices
    }

    year_2020 = rows[("target_year", "credit_group", "survived_12m", "2020")]
    assert year_2020.forecast_count == 2
    assert year_2020.unique_target_date_count == 1
    assert year_2020.unique_security_count == 2
    assert year_2020.mean_predicted_probability == pytest.approx(0.6)
    assert year_2020.observed_rate == pytest.approx(0.5)
    assert year_2020.calibration_gap == pytest.approx(-0.1)
    assert year_2020.absolute_calibration_gap == pytest.approx(0.1)
    assert year_2020.brier_score == pytest.approx(0.10)
    assert year_2020.median_calibration_resolved_n == pytest.approx(12.0)
    assert year_2020.min_calibration_resolved_n == 4
    assert year_2020.max_calibration_resolved_n == 20
    assert year_2020.small_sample_forecast_count == 2

    year_2021 = rows[("target_year", "credit_group", "survived_12m", "2021")]
    assert year_2021.forecast_count == 2
    assert year_2021.mean_predicted_probability == pytest.approx(0.45)
    assert year_2021.observed_rate == pytest.approx(0.5)
    assert year_2021.calibration_gap == pytest.approx(0.05)
    assert year_2021.brier_score == pytest.approx((0.49 + 0.64) / 2)

    # Same date/metric but a different prior basis remains a separate family.
    liquidity_2020 = rows[("target_year", "liquidity_runway", "survived_12m", "2020")]
    assert liquidity_2020.forecast_count == 1
    assert liquidity_2020.mean_predicted_probability == pytest.approx(0.6)
    assert liquidity_2020.observed_rate == 1.0


def test_calibration_stability_exposes_credit_state_sample_band_and_t0_bucket():
    report = evaluate_calibration_stability(_report())
    rows = {
        (item.dimension, item.basis, item.metric, item.value): item
        for item in report.slices
    }

    stress = rows[("credit_group", "credit_group", "survived_12m", "fresh_credit_stress")]
    assert stress.forecast_count == 2
    assert stress.brier_score == pytest.approx(0.10)

    no_stress = rows[("credit_group", "credit_group", "survived_12m", "fresh_credit_no_stress")]
    assert no_stress.forecast_count == 2
    assert no_stress.brier_score == pytest.approx((0.49 + 0.64) / 2)

    assert rows[("calibration_n_band", "credit_group", "survived_12m", "1-4")].forecast_count == 1
    assert rows[("calibration_n_band", "credit_group", "survived_12m", "10-29")].forecast_count == 1
    assert rows[("calibration_n_band", "credit_group", "survived_12m", "30-99")].forecast_count == 1
    assert rows[("calibration_n_band", "credit_group", "survived_12m", "100+")].forecast_count == 1

    assert rows[("basis_bucket", "credit_group", "survived_12m", "fresh_credit_stress")].forecast_count == 2
    assert rows[("basis_bucket", "credit_group", "survived_12m", "fresh_credit_no_stress")].forecast_count == 2
    assert rows[("basis_bucket", "liquidity_runway", "survived_12m", "12-<24m")].forecast_count == 1

    payload = calibration_stability_to_dict(report)
    assert payload["forecast_observation_count"] == 5
    assert payload["slice_count"] == len(report.slices)
    assert any("not labeled as economic regimes" in item for item in payload["semantics"])
    assert payload["warnings"] == []


def test_calibration_stability_rejects_unknown_or_duplicate_dimensions():
    with pytest.raises(ValueError, match="unsupported calibration stability dimensions"):
        evaluate_calibration_stability(_report(), dimensions=("target_year", "macro_regime"))

    with pytest.raises(ValueError, match="dimensions must be unique"):
        evaluate_calibration_stability(_report(), dimensions=("target_year", "target_year"))


def test_calibration_stability_warns_when_calendar_or_credit_variation_is_absent():
    single = _observation(
        "A", date(2020, 12, 31), basis="credit_group", bucket="fresh_credit_stress",
        credit_group="fresh_credit_stress", probability=0.8, resolved_n=20, actual=True,
    )
    calibration = PriorCalibrationReport(
        evaluation_cutoff=date(2024, 12, 31),
        prior_run_count=1,
        target_case_count=1,
        forecast_observation_count=1,
        summaries=(),
        observations=(single,),
        semantics=(),
        warnings=(),
    )
    report = evaluate_calibration_stability(calibration)
    assert any("fewer than two realized target dates" in item for item in report.warnings)
    assert any("only one realized credit group" in item for item in report.warnings)

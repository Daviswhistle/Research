from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import math
from statistics import median
from typing import Iterable

from .rate_diagnostics import BinomialRateDiagnostic
from .source_outcomes import CsvSourceBackedOutcomeIndex, SourceBackedOutcomeLabel
from .walk_forward_priors import WalkForwardPriorRun, WalkForwardPriorStratum


@dataclass(frozen=True)
class PriorForecastObservation:
    security_id: str
    symbol: str
    target_analysis_date: date
    basis: str
    bucket: str
    credit_group: str
    metric: str
    predicted_probability: float
    calibration_resolved_n: int
    calibration_successes: int
    calibration_wilson_95_low: float | None
    calibration_wilson_95_high: float | None
    calibration_small_sample: bool
    actual: bool
    squared_error: float


@dataclass(frozen=True)
class CalibrationBin:
    lower_bound: float
    upper_bound: float
    upper_inclusive: bool
    forecast_count: int
    mean_predicted_probability: float | None
    observed_rate: float | None
    brier_score: float | None


@dataclass(frozen=True)
class PriorCalibrationSummary:
    basis: str
    metric: str
    forecast_count: int
    small_sample_forecast_count: int
    mean_predicted_probability: float | None
    observed_rate: float | None
    brier_score: float | None
    median_calibration_resolved_n: float | None
    bins: tuple[CalibrationBin, ...]


@dataclass(frozen=True)
class PriorCalibrationReport:
    evaluation_cutoff: date
    prior_run_count: int
    target_case_count: int
    forecast_observation_count: int
    summaries: tuple[PriorCalibrationSummary, ...]
    observations: tuple[PriorForecastObservation, ...]
    semantics: tuple[str, ...]
    warnings: tuple[str, ...]


_METRICS = (
    "survived_12m",
    "existing_common_survival",
    "normalized_within_3y",
    "three_x_3y",
)


def _case_key(security_id: str, analysis_date: date) -> str:
    return f"{security_id}|{analysis_date.isoformat()}"


def _actual(label: SourceBackedOutcomeLabel, metric: str) -> bool | None:
    if metric == "survived_12m":
        return label.survived_12m
    if metric == "existing_common_survival":
        return label.existing_common_survived_12m
    if metric == "normalized_within_3y":
        return label.normalized_within_3y
    if metric == "three_x_3y":
        if label.equity_multiple_3y is None:
            return None
        return label.equity_multiple_3y >= 3.0
    raise ValueError(f"unsupported calibration metric: {metric}")


def _diagnostic(stratum: WalkForwardPriorStratum, metric: str) -> BinomialRateDiagnostic:
    if metric == "survived_12m":
        return stratum.survived_12m
    if metric == "existing_common_survival":
        return stratum.existing_common_survival
    if metric == "normalized_within_3y":
        return stratum.normalized_within_3y
    if metric == "three_x_3y":
        return stratum.three_x_3y
    raise ValueError(f"unsupported calibration metric: {metric}")


def _bins(rows: tuple[PriorForecastObservation, ...], bin_width: float) -> tuple[CalibrationBin, ...]:
    count = int(round(1.0 / bin_width))
    output: list[CalibrationBin] = []
    for index in range(count):
        lower = index * bin_width
        upper = 1.0 if index == count - 1 else (index + 1) * bin_width
        selected = tuple(
            row
            for row in rows
            if row.predicted_probability >= lower
            and (
                row.predicted_probability <= upper
                if index == count - 1
                else row.predicted_probability < upper
            )
        )
        output.append(
            CalibrationBin(
                lower_bound=lower,
                upper_bound=upper,
                upper_inclusive=index == count - 1,
                forecast_count=len(selected),
                mean_predicted_probability=(
                    sum(row.predicted_probability for row in selected) / len(selected)
                    if selected
                    else None
                ),
                observed_rate=(
                    sum(row.actual for row in selected) / len(selected)
                    if selected
                    else None
                ),
                brier_score=(
                    sum(row.squared_error for row in selected) / len(selected)
                    if selected
                    else None
                ),
            )
        )
    return tuple(output)


def _summary(
    basis: str,
    metric: str,
    rows: tuple[PriorForecastObservation, ...],
    *,
    bin_width: float,
) -> PriorCalibrationSummary:
    return PriorCalibrationSummary(
        basis=basis,
        metric=metric,
        forecast_count=len(rows),
        small_sample_forecast_count=sum(row.calibration_small_sample for row in rows),
        mean_predicted_probability=(
            sum(row.predicted_probability for row in rows) / len(rows)
            if rows
            else None
        ),
        observed_rate=(sum(row.actual for row in rows) / len(rows) if rows else None),
        brier_score=(sum(row.squared_error for row in rows) / len(rows) if rows else None),
        median_calibration_resolved_n=(
            median(row.calibration_resolved_n for row in rows) if rows else None
        ),
        bins=_bins(rows, bin_width),
    )


def evaluate_walk_forward_priors(
    prior_runs: Iterable[WalkForwardPriorRun],
    outcomes: CsvSourceBackedOutcomeIndex,
    evaluation_cutoff: date,
    *,
    bin_width: float = 0.10,
) -> PriorCalibrationReport:
    """Evaluate ex-ante empirical priors against outcomes known by a later cutoff.

    Every prior basis is evaluated independently. Credit-group and feature-stratum
    priors overlap by design and are never combined into a synthetic probability.
    """

    if not math.isfinite(bin_width) or bin_width <= 0 or bin_width > 1:
        raise ValueError("bin_width must be finite and in (0, 1]")
    reciprocal = 1.0 / bin_width
    if not math.isclose(reciprocal, round(reciprocal), rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("bin_width must divide 1.0 exactly, for example 0.05, 0.10, 0.20, 0.25, 0.50, or 1.0")

    runs = tuple(sorted(prior_runs, key=lambda item: item.target_analysis_date))
    labels = {label.case_key: label for label in outcomes.known_labels(evaluation_cutoff)}
    observations: list[PriorForecastObservation] = []
    seen_forecasts: set[tuple[str, str, str, str]] = set()
    warnings: list[str] = []

    for run in runs:
        if run.target_analysis_date > evaluation_cutoff:
            warnings.append(
                f"prior run {run.target_analysis_date.isoformat()} is after evaluation cutoff and contributes no realized observations"
            )
        for case in run.priors:
            label = labels.get(_case_key(case.security_id, case.analysis_date))
            if label is None:
                continue
            strata = (case.credit_group_prior, *case.feature_priors)
            for stratum in strata:
                for metric in _METRICS:
                    diagnostic = _diagnostic(stratum, metric)
                    actual = _actual(label, metric)
                    if diagnostic.rate is None or diagnostic.resolved_n <= 0 or actual is None:
                        continue
                    probability = float(diagnostic.rate)
                    if probability < 0 or probability > 1 or not math.isfinite(probability):
                        raise ValueError(
                            f"invalid prior probability {probability} for {case.security_id} {case.analysis_date} {stratum.basis}/{metric}"
                        )
                    forecast_key = (
                        _case_key(case.security_id, case.analysis_date),
                        stratum.basis,
                        stratum.bucket,
                        metric,
                    )
                    if forecast_key in seen_forecasts:
                        raise ValueError(f"duplicate prior forecast observation: {forecast_key}")
                    seen_forecasts.add(forecast_key)
                    squared_error = (probability - (1.0 if actual else 0.0)) ** 2
                    observations.append(
                        PriorForecastObservation(
                            security_id=case.security_id,
                            symbol=case.symbol,
                            target_analysis_date=case.analysis_date,
                            basis=stratum.basis,
                            bucket=stratum.bucket,
                            credit_group=stratum.credit_group,
                            metric=metric,
                            predicted_probability=probability,
                            calibration_resolved_n=diagnostic.resolved_n,
                            calibration_successes=diagnostic.successes,
                            calibration_wilson_95_low=diagnostic.wilson_95_low,
                            calibration_wilson_95_high=diagnostic.wilson_95_high,
                            calibration_small_sample=diagnostic.small_sample,
                            actual=bool(actual),
                            squared_error=squared_error,
                        )
                    )

    observations.sort(
        key=lambda row: (
            row.basis,
            row.metric,
            row.target_analysis_date,
            row.security_id,
            row.bucket,
        )
    )
    grouped: dict[tuple[str, str], list[PriorForecastObservation]] = {}
    for row in observations:
        grouped.setdefault((row.basis, row.metric), []).append(row)
    summaries = tuple(
        _summary(basis, metric, tuple(grouped[(basis, metric)]), bin_width=bin_width)
        for basis, metric in sorted(grouped)
    )

    target_case_count = sum(len(run.priors) for run in runs)
    if observations and all(row.calibration_small_sample for row in observations):
        warnings.append(
            "every evaluated forecast was generated from a calibration denominator below its configured small-sample threshold"
        )
    return PriorCalibrationReport(
        evaluation_cutoff=evaluation_cutoff,
        prior_run_count=len(runs),
        target_case_count=target_case_count,
        forecast_observation_count=len(observations),
        summaries=summaries,
        observations=tuple(observations),
        semantics=(
            "forecast probabilities are the raw ex-ante empirical rates emitted by cutoff-safe walk-forward prior runs",
            "credit-group and feature-stratum forecast families are evaluated separately because they overlap and are not independent probabilities",
            "realized outcomes are admitted metric-by-metric only when their source-backed known date is on or before the evaluation cutoff",
            "Brier score is the mean squared error between the raw prior probability and the realized binary outcome",
            "calibration bins report mean forecast probability versus observed rate and retain empty bins explicitly",
            "small-sample flags describe the calibration population behind each forecast; they do not remove or shrink the raw forecast",
        ),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def prior_calibration_to_dict(report: PriorCalibrationReport) -> dict[str, object]:
    return {
        "evaluation_cutoff": report.evaluation_cutoff.isoformat(),
        "prior_run_count": report.prior_run_count,
        "target_case_count": report.target_case_count,
        "forecast_observation_count": report.forecast_observation_count,
        "summaries": [asdict(item) for item in report.summaries],
        "observations": [
            {
                **asdict(item),
                "target_analysis_date": item.target_analysis_date.isoformat(),
            }
            for item in report.observations
        ],
        "semantics": list(report.semantics),
        "warnings": list(report.warnings),
    }

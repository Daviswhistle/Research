from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median
from typing import Callable, Iterable

from .prior_calibration import PriorCalibrationReport, PriorForecastObservation


@dataclass(frozen=True)
class CalibrationStabilitySlice:
    dimension: str
    value: str
    basis: str
    metric: str
    forecast_count: int
    unique_target_date_count: int
    unique_security_count: int
    small_sample_forecast_count: int
    mean_predicted_probability: float | None
    observed_rate: float | None
    calibration_gap: float | None
    absolute_calibration_gap: float | None
    brier_score: float | None
    median_calibration_resolved_n: float | None
    min_calibration_resolved_n: int | None
    max_calibration_resolved_n: int | None


@dataclass(frozen=True)
class CalibrationStabilityReport:
    evaluation_cutoff: str
    forecast_observation_count: int
    slice_count: int
    slices: tuple[CalibrationStabilitySlice, ...]
    semantics: tuple[str, ...]
    warnings: tuple[str, ...]


def _sample_size_band(resolved_n: int) -> str:
    if resolved_n < 5:
        return "1-4"
    if resolved_n < 10:
        return "5-9"
    if resolved_n < 30:
        return "10-29"
    if resolved_n < 100:
        return "30-99"
    return "100+"


def _slice(
    rows: tuple[PriorForecastObservation, ...],
    *,
    dimension: str,
    value: str,
    basis: str,
    metric: str,
) -> CalibrationStabilitySlice:
    if not rows:
        raise ValueError("calibration stability slice cannot be empty")
    mean_predicted = sum(row.predicted_probability for row in rows) / len(rows)
    observed = sum(row.actual for row in rows) / len(rows)
    gap = observed - mean_predicted
    resolved_ns = [row.calibration_resolved_n for row in rows]
    return CalibrationStabilitySlice(
        dimension=dimension,
        value=value,
        basis=basis,
        metric=metric,
        forecast_count=len(rows),
        unique_target_date_count=len({row.target_analysis_date for row in rows}),
        unique_security_count=len({row.security_id for row in rows}),
        small_sample_forecast_count=sum(row.calibration_small_sample for row in rows),
        mean_predicted_probability=mean_predicted,
        observed_rate=observed,
        calibration_gap=gap,
        absolute_calibration_gap=abs(gap),
        brier_score=sum(row.squared_error for row in rows) / len(rows),
        median_calibration_resolved_n=median(resolved_ns),
        min_calibration_resolved_n=min(resolved_ns),
        max_calibration_resolved_n=max(resolved_ns),
    )


def _group_slices(
    observations: tuple[PriorForecastObservation, ...],
    *,
    dimension: str,
    selector: Callable[[PriorForecastObservation], str],
) -> tuple[CalibrationStabilitySlice, ...]:
    grouped: dict[tuple[str, str, str], list[PriorForecastObservation]] = {}
    for row in observations:
        value = selector(row)
        grouped.setdefault((row.basis, row.metric, value), []).append(row)
    return tuple(
        _slice(
            tuple(grouped[(basis, metric, value)]),
            dimension=dimension,
            value=value,
            basis=basis,
            metric=metric,
        )
        for basis, metric, value in sorted(grouped)
    )


def evaluate_calibration_stability(
    calibration: PriorCalibrationReport,
    *,
    dimensions: Iterable[str] = (
        "target_date",
        "target_year",
        "credit_group",
        "calibration_n_band",
        "basis_bucket",
    ),
) -> CalibrationStabilityReport:
    """Slice already-realized OOS forecasts without creating a new probability model.

    Every slice remains nested inside the original `(basis, metric)` forecast
    family. This prevents overlapping credit/feature priors from being pooled as
    though they were independent predictions.
    """

    selectors: dict[str, Callable[[PriorForecastObservation], str]] = {
        "target_date": lambda row: row.target_analysis_date.isoformat(),
        "target_year": lambda row: str(row.target_analysis_date.year),
        "credit_group": lambda row: row.credit_group,
        "calibration_n_band": lambda row: _sample_size_band(row.calibration_resolved_n),
        "basis_bucket": lambda row: row.bucket,
    }
    dims = tuple(dimensions)
    if len(set(dims)) != len(dims):
        raise ValueError("calibration stability dimensions must be unique")
    unsupported = sorted(set(dims) - set(selectors))
    if unsupported:
        raise ValueError(
            "unsupported calibration stability dimensions: " + ", ".join(unsupported)
        )

    observations = tuple(calibration.observations)
    slices = tuple(
        item
        for dimension in dims
        for item in _group_slices(
            observations,
            dimension=dimension,
            selector=selectors[dimension],
        )
    )
    slices = tuple(
        sorted(
            slices,
            key=lambda item: (item.dimension, item.basis, item.metric, item.value),
        )
    )

    warnings: list[str] = []
    if not observations:
        warnings.append("no realized forecast observations are available for stability slicing")
    if observations and len({row.target_analysis_date for row in observations}) < 2:
        warnings.append(
            "stability report contains fewer than two realized target dates; calendar stability cannot yet be assessed"
        )
    if observations and len({row.credit_group for row in observations}) < 2:
        warnings.append(
            "stability report contains only one realized credit group; cross-credit-state stability cannot yet be assessed"
        )

    return CalibrationStabilityReport(
        evaluation_cutoff=calibration.evaluation_cutoff.isoformat(),
        forecast_observation_count=len(observations),
        slice_count=len(slices),
        slices=slices,
        semantics=(
            "stability diagnostics only regroup already-realized out-of-sample forecast observations; they do not refit, shrink, combine, or alter prior probabilities",
            "every slice remains inside its original basis and metric so overlapping forecast families are never pooled as independent predictions",
            "target_date and target_year are deterministic calendar partitions and are not labeled as economic regimes without external evidence",
            "credit_group slices expose stability across point-in-time credit states within each forecast family",
            "calibration_n_band uses fixed descriptive bands 1-4, 5-9, 10-29, 30-99, and 100+ based on each forecast's historical resolved denominator",
            "basis_bucket slices expose the T0 feature/credit bucket used by the original prior and preserve raw forecast counts and calibration denominators",
            "calibration_gap is observed_rate minus mean_predicted_probability; positive values indicate underprediction in that slice and negative values indicate overprediction",
            "small or single-date slices remain visible rather than being automatically suppressed or promoted to a stability conclusion",
        ),
        warnings=tuple(warnings),
    )


def calibration_stability_to_dict(report: CalibrationStabilityReport) -> dict[str, object]:
    return {
        "evaluation_cutoff": report.evaluation_cutoff,
        "forecast_observation_count": report.forecast_observation_count,
        "slice_count": report.slice_count,
        "slices": [asdict(item) for item in report.slices],
        "semantics": list(report.semantics),
        "warnings": list(report.warnings),
    }

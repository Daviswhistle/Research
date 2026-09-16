from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from statistics import median
from typing import Callable, Iterable

from .credit_replay import JointDistressSeed, JointReplayRun
from .rate_diagnostics import BinomialRateDiagnostic, binomial_rate_diagnostic, diagnostic_to_dict
from .source_outcomes import CsvSourceBackedOutcomeIndex, SourceBackedOutcomeLabel
from .survival_features import CsvSurvivalFeatureIndex, FeatureDimension, feature_bucket


@dataclass(frozen=True)
class WalkForwardPriorStratum:
    basis: str
    bucket: str
    credit_group: str
    calibration_case_count: int
    source_labeled_case_count: int
    survived_12m: BinomialRateDiagnostic
    existing_common_survival: BinomialRateDiagnostic
    normalized_within_3y: BinomialRateDiagnostic
    three_x_3y: BinomialRateDiagnostic
    median_equity_multiple_3y: float | None
    case_keys: tuple[str, ...]


@dataclass(frozen=True)
class WalkForwardCasePrior:
    security_id: str
    symbol: str
    analysis_date: date
    credit_group: str
    feature_buckets: tuple[tuple[str, str], ...]
    credit_group_prior: WalkForwardPriorStratum
    feature_priors: tuple[WalkForwardPriorStratum, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class WalkForwardPriorRun:
    target_analysis_date: date
    historical_run_dates: tuple[date, ...]
    episode_gap_days: int
    historical_case_count_before_episode_dedup: int
    historical_case_count_after_episode_dedup: int
    source_labels_known_by_target: int
    target_case_count: int
    priors: tuple[WalkForwardCasePrior, ...]
    semantics: tuple[str, ...]


def _case_key(item: JointDistressSeed) -> str:
    return f"{item.equity.security.security_id}|{item.equity.analysis_date.isoformat()}"


def _credit_group(item: JointDistressSeed) -> str:
    if item.fresh_credit_instrument_count <= 0:
        return "no_fresh_credit"
    return "fresh_credit_stress" if item.any_credit_stress else "fresh_credit_no_stress"


def _episode_dedup(
    cases: Iterable[JointDistressSeed],
    *,
    episode_gap_days: int,
) -> tuple[JointDistressSeed, ...]:
    if episode_gap_days < 0:
        raise ValueError("episode_gap_days cannot be negative")
    by_security: dict[str, list[JointDistressSeed]] = {}
    for item in cases:
        by_security.setdefault(item.equity.security.security_id, []).append(item)
    output: list[JointDistressSeed] = []
    for security_id in sorted(by_security):
        rows = sorted(by_security[security_id], key=lambda item: item.equity.analysis_date)
        last_included: date | None = None
        for item in rows:
            current = item.equity.analysis_date
            if last_included is not None and (current - last_included).days <= episode_gap_days:
                continue
            output.append(item)
            last_included = current
    return tuple(sorted(output, key=lambda item: (item.equity.analysis_date, item.equity.security.security_id)))


def _label_metric(
    rows: Iterable[tuple[JointDistressSeed, SourceBackedOutcomeLabel]],
    getter: Callable[[SourceBackedOutcomeLabel], bool | None],
    *,
    small_sample_n: int,
) -> BinomialRateDiagnostic:
    values = [getter(label) for _, label in rows]
    resolved = [bool(value) for value in values if value is not None]
    rate = None if not resolved else sum(resolved) / len(resolved)
    return binomial_rate_diagnostic(rate, len(resolved), small_sample_n=small_sample_n)


def _stratum(
    rows: Iterable[JointDistressSeed],
    labels: dict[str, SourceBackedOutcomeLabel],
    *,
    basis: str,
    bucket: str,
    credit_group: str,
    small_sample_n: int,
) -> WalkForwardPriorStratum:
    cases = tuple(rows)
    matched = [(item, labels[_case_key(item)]) for item in cases if _case_key(item) in labels]
    multiples = [
        float(label.equity_multiple_3y)
        for _, label in matched
        if label.equity_multiple_3y is not None
    ]
    three_x_rate = None if not multiples else sum(value >= 3.0 for value in multiples) / len(multiples)
    return WalkForwardPriorStratum(
        basis=basis,
        bucket=bucket,
        credit_group=credit_group,
        calibration_case_count=len(cases),
        source_labeled_case_count=len(matched),
        survived_12m=_label_metric(
            matched, lambda label: label.survived_12m, small_sample_n=small_sample_n
        ),
        existing_common_survival=_label_metric(
            matched,
            lambda label: label.existing_common_survived_12m,
            small_sample_n=small_sample_n,
        ),
        normalized_within_3y=_label_metric(
            matched,
            lambda label: label.normalized_within_3y,
            small_sample_n=small_sample_n,
        ),
        three_x_3y=binomial_rate_diagnostic(
            three_x_rate,
            len(multiples),
            small_sample_n=small_sample_n,
        ),
        median_equity_multiple_3y=(median(multiples) if multiples else None),
        case_keys=tuple(_case_key(item) for item in cases),
    )


def build_walk_forward_priors(
    target: JointReplayRun,
    historical_runs: Iterable[JointReplayRun],
    outcomes: CsvSourceBackedOutcomeIndex,
    features: CsvSurvivalFeatureIndex,
    *,
    dimensions: Iterable[FeatureDimension] = (
        "liquidity_runway",
        "nearest_maturity",
        "covenant_headroom",
        "net_leverage",
        "impairment_type",
    ),
    episode_gap_days: int = 365,
    small_sample_n: int = 30,
) -> WalkForwardPriorRun:
    """Build empirical priors using only information knowable at target T0.

    Target-cohort outcomes are never used. Historical labels must have
    `outcome_known_date <= target.analysis_date`. Repeated observations of the
    same security within the configured episode gap are collapsed before
    calibration, and same-security history inside the target's episode gap is
    excluded from that target case's prior.
    """

    dims = tuple(dimensions)
    if len(set(dims)) != len(dims):
        raise ValueError("walk-forward feature dimensions must be unique")
    if small_sample_n <= 0:
        raise ValueError("small_sample_n must be positive")
    if episode_gap_days < 0:
        raise ValueError("episode_gap_days cannot be negative")

    history_runs = tuple(sorted(historical_runs, key=lambda run: run.analysis_date))
    future_or_same = [run.analysis_date for run in history_runs if run.analysis_date >= target.analysis_date]
    if future_or_same:
        raise ValueError(
            "walk-forward historical runs must strictly precede target analysis date; invalid dates="
            + ", ".join(item.isoformat() for item in future_or_same)
        )
    history_cases_raw = tuple(item for run in history_runs for item in run.candidates)
    history_cases = _episode_dedup(history_cases_raw, episode_gap_days=episode_gap_days)
    known_labels = {
        label.case_key: label
        for label in outcomes.known_labels(target.analysis_date)
    }

    priors: list[WalkForwardCasePrior] = []
    for target_case in target.candidates:
        target_security_id = target_case.equity.security.security_id
        target_date = target_case.equity.analysis_date
        # Prevent a previous observation of the same ongoing distress episode
        # from becoming its own empirical prior.
        eligible_history = tuple(
            item
            for item in history_cases
            if not (
                item.equity.security.security_id == target_security_id
                and (target_date - item.equity.analysis_date).days <= episode_gap_days
            )
        )
        credit_group = _credit_group(target_case)
        same_credit = tuple(item for item in eligible_history if _credit_group(item) == credit_group)
        credit_prior = _stratum(
            same_credit,
            known_labels,
            basis="credit_group",
            bucket=credit_group,
            credit_group=credit_group,
            small_sample_n=small_sample_n,
        )

        target_snapshot = features.get(target_security_id, target_date)
        feature_buckets: list[tuple[str, str]] = []
        feature_priors: list[WalkForwardPriorStratum] = []
        warnings: list[str] = []
        if target_snapshot is None:
            warnings.append("target case has no verified T0 survival feature snapshot; feature priors use unknown buckets")

        for dimension in dims:
            target_bucket = feature_bucket(target_snapshot, dimension)
            feature_buckets.append((dimension, target_bucket))
            matching: list[JointDistressSeed] = []
            for historical_case in same_credit:
                historical_snapshot = features.get(
                    historical_case.equity.security.security_id,
                    historical_case.equity.analysis_date,
                )
                if feature_bucket(historical_snapshot, dimension) == target_bucket:
                    matching.append(historical_case)
            feature_priors.append(
                _stratum(
                    matching,
                    known_labels,
                    basis=dimension,
                    bucket=target_bucket,
                    credit_group=credit_group,
                    small_sample_n=small_sample_n,
                )
            )

        priors.append(
            WalkForwardCasePrior(
                security_id=target_security_id,
                symbol=target_case.equity.security.symbol,
                analysis_date=target_date,
                credit_group=credit_group,
                feature_buckets=tuple(feature_buckets),
                credit_group_prior=credit_prior,
                feature_priors=tuple(feature_priors),
                warnings=tuple(warnings),
            )
        )

    return WalkForwardPriorRun(
        target_analysis_date=target.analysis_date,
        historical_run_dates=tuple(run.analysis_date for run in history_runs),
        episode_gap_days=episode_gap_days,
        historical_case_count_before_episode_dedup=len(history_cases_raw),
        historical_case_count_after_episode_dedup=len(history_cases),
        source_labels_known_by_target=sum(key in {_case_key(item) for item in history_cases} for key in known_labels),
        target_case_count=len(target.candidates),
        priors=tuple(priors),
        semantics=(
            "walk-forward priors use only historical analysis dates strictly before target T0",
            "source-backed labels are admitted only when outcome_known_date is on or before target T0",
            "target-cohort outcomes are never used to build the target prior",
            "repeated historical observations of the same security within episode_gap_days are collapsed before calibration",
            "same-security history inside the target episode gap is excluded from that target case's prior",
            "raw empirical rates, resolved denominators, Wilson 95% intervals, and small-sample warnings are preserved; no LLM probability adjustment is applied",
        ),
    )


def _diagnostic_payload(value: BinomialRateDiagnostic) -> dict[str, object]:
    return diagnostic_to_dict(value)


def _stratum_to_dict(value: WalkForwardPriorStratum) -> dict[str, object]:
    payload = asdict(value)
    payload["survived_12m"] = _diagnostic_payload(value.survived_12m)
    payload["existing_common_survival"] = _diagnostic_payload(value.existing_common_survival)
    payload["normalized_within_3y"] = _diagnostic_payload(value.normalized_within_3y)
    payload["three_x_3y"] = _diagnostic_payload(value.three_x_3y)
    return payload


def walk_forward_prior_to_dict(run: WalkForwardPriorRun) -> dict[str, object]:
    return {
        "target_analysis_date": run.target_analysis_date.isoformat(),
        "historical_run_dates": [item.isoformat() for item in run.historical_run_dates],
        "episode_gap_days": run.episode_gap_days,
        "historical_case_count_before_episode_dedup": run.historical_case_count_before_episode_dedup,
        "historical_case_count_after_episode_dedup": run.historical_case_count_after_episode_dedup,
        "source_labels_known_by_target": run.source_labels_known_by_target,
        "target_case_count": run.target_case_count,
        "semantics": list(run.semantics),
        "priors": [
            {
                "security_id": item.security_id,
                "symbol": item.symbol,
                "analysis_date": item.analysis_date.isoformat(),
                "credit_group": item.credit_group,
                "feature_buckets": {key: value for key, value in item.feature_buckets},
                "credit_group_prior": _stratum_to_dict(item.credit_group_prior),
                "feature_priors": [_stratum_to_dict(value) for value in item.feature_priors],
                "warnings": list(item.warnings),
            }
            for item in run.priors
        ],
    }

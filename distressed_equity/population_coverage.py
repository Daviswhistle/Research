from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Iterable

from .credit_replay import JointReplayRun
from .replay import ReplayRun
from .source_outcomes import CsvSourceBackedOutcomeIndex, SourceBackedOutcomeLabel
from .survival_features import CsvSurvivalFeatureIndex


@dataclass(frozen=True)
class PopulationCoverageAtDate:
    analysis_date: date
    historical_universe_size: int
    scanned_security_count: int
    equity_distress_case_count: int
    credit_linked_case_count: int
    fresh_credit_case_count: int
    credit_stress_case_count: int
    credit_link_coverage_of_distress: float | None
    fresh_credit_coverage_of_distress: float | None
    fresh_credit_coverage_of_linked: float | None
    source_labeled_case_count: int
    survived_12m_labeled_count: int
    existing_common_12m_labeled_count: int
    normalized_3y_labeled_count: int
    equity_multiple_3y_labeled_count: int
    source_label_coverage_of_distress: float | None
    t0_feature_snapshot_count: int
    liquidity_runway_feature_count: int
    nearest_maturity_feature_count: int
    covenant_headroom_feature_count: int
    net_leverage_feature_count: int
    impairment_type_feature_count: int
    feature_snapshot_coverage_of_distress: float | None


@dataclass(frozen=True)
class PopulationCoverageReport:
    outcome_coverage_cutoff: date
    analysis_dates: tuple[date, ...]
    total_case_observations: int
    unique_security_count: int
    repeated_case_observation_count: int
    rows: tuple[PopulationCoverageAtDate, ...]
    semantics: tuple[str, ...]
    warnings: tuple[str, ...]


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _case_key(security_id: str, analysis_date: date) -> str:
    return f"{security_id}|{analysis_date.isoformat()}"


def _known_label_map(
    outcomes: CsvSourceBackedOutcomeIndex,
    cutoff: date,
) -> dict[str, SourceBackedOutcomeLabel]:
    return {label.case_key: label for label in outcomes.known_labels(cutoff)}


def population_coverage_at_date(
    equity_run: ReplayRun,
    joint_run: JointReplayRun,
    outcomes: CsvSourceBackedOutcomeIndex,
    features: CsvSurvivalFeatureIndex,
    *,
    outcome_coverage_cutoff: date,
) -> PopulationCoverageAtDate:
    if equity_run.analysis_date != joint_run.analysis_date:
        raise ValueError("equity and joint replay analysis dates must match")
    if equity_run.analysis_date > outcome_coverage_cutoff:
        raise ValueError("analysis date cannot be after outcome coverage cutoff")
    equity_case_keys = {
        _case_key(seed.security.security_id, seed.analysis_date)
        for seed in equity_run.candidates
    }
    joint_case_keys = {
        _case_key(seed.equity.security.security_id, seed.equity.analysis_date)
        for seed in joint_run.candidates
    }
    if equity_case_keys != joint_case_keys:
        raise ValueError("equity replay and joint replay must contain the same distress case set")

    labels = _known_label_map(outcomes, outcome_coverage_cutoff)
    matched_labels = [labels[key] for key in sorted(equity_case_keys) if key in labels]

    feature_rows = []
    for seed in equity_run.candidates:
        snapshot = features.get(seed.security.security_id, seed.analysis_date)
        if snapshot is not None:
            feature_rows.append(snapshot)

    distress_count = len(equity_run.candidates)
    linked_count = joint_run.candidates_with_credit_links
    fresh_count = joint_run.candidates_with_fresh_credit
    return PopulationCoverageAtDate(
        analysis_date=equity_run.analysis_date,
        historical_universe_size=equity_run.historical_universe_size,
        scanned_security_count=equity_run.scanned_security_count,
        equity_distress_case_count=distress_count,
        credit_linked_case_count=linked_count,
        fresh_credit_case_count=fresh_count,
        credit_stress_case_count=joint_run.candidates_with_credit_stress,
        credit_link_coverage_of_distress=_rate(linked_count, distress_count),
        fresh_credit_coverage_of_distress=_rate(fresh_count, distress_count),
        fresh_credit_coverage_of_linked=_rate(fresh_count, linked_count),
        source_labeled_case_count=len(matched_labels),
        survived_12m_labeled_count=sum(label.survived_12m is not None for label in matched_labels),
        existing_common_12m_labeled_count=sum(
            label.existing_common_survived_12m is not None for label in matched_labels
        ),
        normalized_3y_labeled_count=sum(
            label.normalized_within_3y is not None for label in matched_labels
        ),
        equity_multiple_3y_labeled_count=sum(
            label.equity_multiple_3y is not None for label in matched_labels
        ),
        source_label_coverage_of_distress=_rate(len(matched_labels), distress_count),
        t0_feature_snapshot_count=len(feature_rows),
        liquidity_runway_feature_count=sum(
            row.liquidity_runway_months is not None for row in feature_rows
        ),
        nearest_maturity_feature_count=sum(
            row.nearest_maturity_months is not None for row in feature_rows
        ),
        covenant_headroom_feature_count=sum(
            row.covenant_headroom_pct is not None for row in feature_rows
        ),
        net_leverage_feature_count=sum(row.net_leverage is not None for row in feature_rows),
        impairment_type_feature_count=sum(
            row.impairment_type is not None for row in feature_rows
        ),
        feature_snapshot_coverage_of_distress=_rate(len(feature_rows), distress_count),
    )


def build_population_coverage_report(
    replay_pairs: Iterable[tuple[ReplayRun, JointReplayRun]],
    outcomes: CsvSourceBackedOutcomeIndex,
    features: CsvSurvivalFeatureIndex,
    *,
    outcome_coverage_cutoff: date,
) -> PopulationCoverageReport:
    pairs = tuple(sorted(replay_pairs, key=lambda item: item[0].analysis_date))
    dates = tuple(item[0].analysis_date for item in pairs)
    if len(dates) != len(set(dates)):
        raise ValueError("population coverage analysis dates must be unique")
    rows = tuple(
        population_coverage_at_date(
            equity,
            joint,
            outcomes,
            features,
            outcome_coverage_cutoff=outcome_coverage_cutoff,
        )
        for equity, joint in pairs
    )
    case_keys: list[str] = []
    security_ids: set[str] = set()
    for equity, _ in pairs:
        for seed in equity.candidates:
            case_keys.append(_case_key(seed.security.security_id, seed.analysis_date))
            security_ids.add(seed.security.security_id)
    warnings: list[str] = []
    if rows and sum(row.credit_linked_case_count for row in rows) == 0:
        warnings.append("no distress case has a point-in-time corporate-credit link")
    if rows and sum(row.source_labeled_case_count for row in rows) == 0:
        warnings.append("no distress case has a source-backed outcome label known by the requested coverage cutoff")
    if rows and sum(row.t0_feature_snapshot_count for row in rows) == 0:
        warnings.append("no distress case has a verified T0 survival-feature snapshot")
    return PopulationCoverageReport(
        outcome_coverage_cutoff=outcome_coverage_cutoff,
        analysis_dates=dates,
        total_case_observations=len(case_keys),
        unique_security_count=len(security_ids),
        repeated_case_observation_count=len(case_keys) - len(security_ids),
        rows=rows,
        semantics=(
            "coverage is an ex-post dataset audit, not an investment signal or ex-ante prior",
            "source outcome coverage uses only metric values whose source-backed known date is on or before outcome_coverage_cutoff",
            "point-in-time credit-link coverage, fresh-credit coverage, source-label coverage, and T0-feature coverage are reported separately",
            "missing credit links are not interpreted as no debt or healthy credit",
            "missing source outcomes and missing T0 features remain missing rather than entering any denominator as failures or favorable states",
            "repeated_case_observation_count counts repeated permanent-security observations across replay dates and does not itself perform distress-episode deduplication",
        ),
        warnings=tuple(warnings),
    )


def population_coverage_to_dict(report: PopulationCoverageReport) -> dict[str, object]:
    return {
        "outcome_coverage_cutoff": report.outcome_coverage_cutoff.isoformat(),
        "analysis_dates": [item.isoformat() for item in report.analysis_dates],
        "total_case_observations": report.total_case_observations,
        "unique_security_count": report.unique_security_count,
        "repeated_case_observation_count": report.repeated_case_observation_count,
        "rows": [
            {
                **asdict(row),
                "analysis_date": row.analysis_date.isoformat(),
            }
            for row in report.rows
        ],
        "semantics": list(report.semantics),
        "warnings": list(report.warnings),
    }

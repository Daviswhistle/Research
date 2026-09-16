from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, replace
from datetime import date
import math
from pathlib import Path
from statistics import median
from typing import Iterable

from .credit_replay import JointDistressSeed, JointReplayRun


@dataclass(frozen=True)
class SourceBackedOutcomeLabel:
    security_id: str
    ticker: str
    analysis_date: date
    outcome_known_date: date
    survived_12m: bool | None
    survived_12m_known_date: date | None
    survived_12m_evidence_refs: tuple[str, ...]
    existing_common_survived_12m: bool | None
    existing_common_survived_12m_known_date: date | None
    existing_common_survived_12m_evidence_refs: tuple[str, ...]
    normalized_within_3y: bool | None
    normalized_within_3y_known_date: date | None
    normalized_within_3y_evidence_refs: tuple[str, ...]
    equity_multiple_3y: float | None
    equity_multiple_3y_known_date: date | None
    equity_multiple_3y_evidence_refs: tuple[str, ...]
    industry_group: str | None
    impairment_type: str | None
    leverage_bucket: str | None
    evidence_refs: tuple[str, ...]
    verifier: str
    verified_on: date
    evidence_reopened: bool
    notes: str | None = None

    @property
    def case_key(self) -> str:
        return f"{self.security_id}|{self.analysis_date.isoformat()}"

    def as_known(self, cutoff: date) -> SourceBackedOutcomeLabel | None:
        """Return only outcome fields whose evidence was knowable by `cutoff`."""

        updates: dict[str, object] = {}
        known_dates: list[date] = []
        metrics = (
            ("survived_12m", "survived_12m_known_date", "survived_12m_evidence_refs"),
            (
                "existing_common_survived_12m",
                "existing_common_survived_12m_known_date",
                "existing_common_survived_12m_evidence_refs",
            ),
            (
                "normalized_within_3y",
                "normalized_within_3y_known_date",
                "normalized_within_3y_evidence_refs",
            ),
            ("equity_multiple_3y", "equity_multiple_3y_known_date", "equity_multiple_3y_evidence_refs"),
        )
        for value_field, date_field, refs_field in metrics:
            value = getattr(self, value_field)
            known_date = getattr(self, date_field)
            if value is not None and known_date is not None and known_date <= cutoff:
                updates[value_field] = value
                updates[date_field] = known_date
                updates[refs_field] = getattr(self, refs_field)
                known_dates.append(known_date)
            else:
                updates[value_field] = None
                updates[date_field] = None
                updates[refs_field] = ()
        if not known_dates:
            return None
        updates["outcome_known_date"] = max(known_dates)
        return replace(self, **updates)


@dataclass(frozen=True)
class SourceOutcomeRateGroup:
    group: str
    cohort_case_count: int
    source_labeled_case_count: int
    survived_12m_resolved_count: int
    survived_12m_rate: float | None
    common_12m_resolved_count: int
    existing_common_survival_rate: float | None
    normalized_3y_resolved_count: int
    normalized_within_3y_rate: float | None
    equity_multiple_3y_resolved_count: int
    three_x_3y_rate: float | None
    median_equity_multiple_3y: float | None
    case_keys: tuple[str, ...]


@dataclass(frozen=True)
class SourceOutcomeBaseRateComparison:
    knowledge_cutoff: date
    cohort_case_count: int
    known_source_label_count: int
    groups: tuple[SourceOutcomeRateGroup, ...]
    semantics: tuple[str, ...]
    warnings: tuple[str, ...]


def _parse_date(value: str | None, *, field: str, row: int) -> date:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"source outcomes CSV row {row}: {field} is required")
    try:
        return date.fromisoformat(clean[:10])
    except ValueError as exc:
        raise ValueError(f"source outcomes CSV row {row}: {field} must be YYYY-MM-DD") from exc


def _optional_date(value: str | None, *, field: str, row: int) -> date | None:
    clean = str(value or "").strip()
    if not clean:
        return None
    return _parse_date(clean, field=field, row=row)


def _optional(value: str | None) -> str | None:
    clean = str(value or "").strip()
    return clean or None


def _refs(value: str | None) -> tuple[str, ...]:
    return tuple(item.strip() for item in str(value or "").split(";") if item.strip())


def _bool(value: str | None, *, field: str, row: int, required: bool = False) -> bool | None:
    clean = str(value or "").strip().lower()
    if not clean:
        if required:
            raise ValueError(f"source outcomes CSV row {row}: {field} is required")
        return None
    if clean in {"true", "1", "yes", "y"}:
        return True
    if clean in {"false", "0", "no", "n"}:
        return False
    raise ValueError(f"source outcomes CSV row {row}: {field} must be true/false")


def _float(value: str | None, *, field: str, row: int) -> float | None:
    clean = str(value or "").strip()
    if not clean:
        return None
    try:
        numeric = float(clean)
    except ValueError as exc:
        raise ValueError(f"source outcomes CSV row {row}: {field} must be numeric") from exc
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError(f"source outcomes CSV row {row}: {field} must be finite and non-negative")
    return numeric


def _add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def _metric_provenance(
    raw: dict[str, str | None],
    *,
    metric: str,
    value: object | None,
    fallback_date: date,
    fallback_refs: tuple[str, ...],
    row: int,
) -> tuple[date | None, tuple[str, ...]]:
    date_field = f"{metric}_known_date"
    refs_field = f"{metric}_evidence_refs"
    explicit_date = _optional_date(raw.get(date_field), field=date_field, row=row)
    explicit_refs = _refs(raw.get(refs_field))
    if value is None:
        if explicit_date is not None or explicit_refs:
            raise ValueError(
                f"source outcomes CSV row {row}: {metric} provenance cannot be supplied when the outcome is unresolved"
            )
        return None, ()
    if explicit_date is not None:
        if not explicit_refs:
            raise ValueError(
                f"source outcomes CSV row {row}: {refs_field} is required when {date_field} is supplied"
            )
        return explicit_date, explicit_refs
    return fallback_date, (explicit_refs or fallback_refs)


class CsvSourceBackedOutcomeIndex:
    """Verified legal/business outcome labels with field-level knowledge provenance."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._labels: dict[str, SourceBackedOutcomeLabel] = {}
        self._load()

    def _load(self) -> None:
        required_columns = {
            "security_id",
            "ticker",
            "analysis_date",
            "outcome_known_date",
            "survived_12m",
            "existing_common_survived_12m",
            "normalized_within_3y",
            "equity_multiple_3y",
            "evidence_refs",
            "verification_status",
            "verifier",
            "verified_on",
            "evidence_reopened",
        }
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            missing = sorted(required_columns - fields)
            if missing:
                raise ValueError("source outcomes CSV missing required columns: " + ", ".join(missing))
            for row_number, raw in enumerate(reader, 2):
                security_id = str(raw.get("security_id") or "").strip()
                if not security_id:
                    continue
                ticker = str(raw.get("ticker") or "").strip()
                analysis_date = _parse_date(raw.get("analysis_date"), field="analysis_date", row=row_number)
                outcome_known = _parse_date(raw.get("outcome_known_date"), field="outcome_known_date", row=row_number)
                if outcome_known < analysis_date:
                    raise ValueError(
                        f"source outcomes CSV row {row_number}: outcome_known_date precedes analysis_date"
                    )

                status = str(raw.get("verification_status") or "").strip().lower()
                if status != "verified":
                    raise ValueError(
                        f"source outcomes CSV row {row_number}: verification_status must be verified"
                    )
                verifier = str(raw.get("verifier") or "").strip()
                if not verifier:
                    raise ValueError(f"source outcomes CSV row {row_number}: verifier is required")
                verified_on = _parse_date(raw.get("verified_on"), field="verified_on", row=row_number)
                reopened = _bool(
                    raw.get("evidence_reopened"), field="evidence_reopened", row=row_number, required=True
                )
                if reopened is not True:
                    raise ValueError(
                        f"source outcomes CSV row {row_number}: evidence_reopened must be true for verified labels"
                    )
                evidence_refs = _refs(raw.get("evidence_refs"))
                if not evidence_refs:
                    raise ValueError(f"source outcomes CSV row {row_number}: evidence_refs are required")

                survived = _bool(raw.get("survived_12m"), field="survived_12m", row=row_number)
                common_survived = _bool(
                    raw.get("existing_common_survived_12m"),
                    field="existing_common_survived_12m",
                    row=row_number,
                )
                normalized = _bool(
                    raw.get("normalized_within_3y"), field="normalized_within_3y", row=row_number
                )
                multiple = _float(raw.get("equity_multiple_3y"), field="equity_multiple_3y", row=row_number)
                if all(value is None for value in (survived, common_survived, normalized, multiple)):
                    raise ValueError(
                        f"source outcomes CSV row {row_number}: at least one outcome field must be resolved"
                    )

                survived_known, survived_refs = _metric_provenance(
                    raw,
                    metric="survived_12m",
                    value=survived,
                    fallback_date=outcome_known,
                    fallback_refs=evidence_refs,
                    row=row_number,
                )
                common_known, common_refs = _metric_provenance(
                    raw,
                    metric="existing_common_survived_12m",
                    value=common_survived,
                    fallback_date=outcome_known,
                    fallback_refs=evidence_refs,
                    row=row_number,
                )
                normalized_known, normalized_refs = _metric_provenance(
                    raw,
                    metric="normalized_within_3y",
                    value=normalized,
                    fallback_date=outcome_known,
                    fallback_refs=evidence_refs,
                    row=row_number,
                )
                multiple_known, multiple_refs = _metric_provenance(
                    raw,
                    metric="equity_multiple_3y",
                    value=multiple,
                    fallback_date=outcome_known,
                    fallback_refs=evidence_refs,
                    row=row_number,
                )

                metric_dates = tuple(
                    item
                    for item in (survived_known, common_known, normalized_known, multiple_known)
                    if item is not None
                )
                if any(item < analysis_date for item in metric_dates):
                    raise ValueError(
                        f"source outcomes CSV row {row_number}: metric known date precedes analysis_date"
                    )
                if metric_dates and outcome_known < max(metric_dates):
                    raise ValueError(
                        f"source outcomes CSV row {row_number}: outcome_known_date must be on or after all metric known dates"
                    )
                if survived_known is not None and survived_known < _add_years(analysis_date, 1):
                    raise ValueError(
                        f"source outcomes CSV row {row_number}: survived_12m cannot be known before the +12m horizon"
                    )
                if common_known is not None and common_known < _add_years(analysis_date, 1):
                    raise ValueError(
                        f"source outcomes CSV row {row_number}: existing_common_survived_12m cannot be known before the +12m horizon"
                    )
                if normalized is False and normalized_known is not None and normalized_known < _add_years(analysis_date, 3):
                    raise ValueError(
                        f"source outcomes CSV row {row_number}: normalized_within_3y=false cannot be known before the +3y horizon"
                    )
                if multiple_known is not None and multiple_known < _add_years(analysis_date, 3):
                    raise ValueError(
                        f"source outcomes CSV row {row_number}: equity_multiple_3y cannot be known before the +3y horizon"
                    )
                if verified_on < outcome_known:
                    raise ValueError(
                        f"source outcomes CSV row {row_number}: verified_on cannot precede outcome_known_date"
                    )

                label = SourceBackedOutcomeLabel(
                    security_id=security_id,
                    ticker=ticker,
                    analysis_date=analysis_date,
                    outcome_known_date=outcome_known,
                    survived_12m=survived,
                    survived_12m_known_date=survived_known,
                    survived_12m_evidence_refs=survived_refs,
                    existing_common_survived_12m=common_survived,
                    existing_common_survived_12m_known_date=common_known,
                    existing_common_survived_12m_evidence_refs=common_refs,
                    normalized_within_3y=normalized,
                    normalized_within_3y_known_date=normalized_known,
                    normalized_within_3y_evidence_refs=normalized_refs,
                    equity_multiple_3y=multiple,
                    equity_multiple_3y_known_date=multiple_known,
                    equity_multiple_3y_evidence_refs=multiple_refs,
                    industry_group=_optional(raw.get("industry_group")),
                    impairment_type=_optional(raw.get("impairment_type")),
                    leverage_bucket=_optional(raw.get("leverage_bucket")),
                    evidence_refs=evidence_refs,
                    verifier=verifier,
                    verified_on=verified_on,
                    evidence_reopened=True,
                    notes=_optional(raw.get("notes")),
                )
                if label.case_key in self._labels:
                    raise ValueError(f"duplicate source outcome case key: {label.case_key}")
                self._labels[label.case_key] = label

    def known_labels(self, cutoff: date) -> tuple[SourceBackedOutcomeLabel, ...]:
        output: list[SourceBackedOutcomeLabel] = []
        for key in sorted(self._labels):
            known = self._labels[key].as_known(cutoff)
            if known is not None:
                output.append(known)
        return tuple(output)


def _key(item: JointDistressSeed) -> str:
    return f"{item.equity.security.security_id}|{item.equity.analysis_date.isoformat()}"


def _rate(values: Iterable[bool]) -> float | None:
    rows = tuple(values)
    return None if not rows else sum(rows) / len(rows)


def _summarize_source_group(
    group: str,
    cohort_rows: Iterable[JointDistressSeed],
    labels: dict[str, SourceBackedOutcomeLabel],
) -> SourceOutcomeRateGroup:
    cohort = tuple(cohort_rows)
    matched = [(item, labels[_key(item)]) for item in cohort if _key(item) in labels]
    survived = [label.survived_12m for _, label in matched if label.survived_12m is not None]
    common = [
        label.existing_common_survived_12m
        for _, label in matched
        if label.existing_common_survived_12m is not None
    ]
    normalized = [
        label.normalized_within_3y
        for _, label in matched
        if label.normalized_within_3y is not None
    ]
    multiples = [
        float(label.equity_multiple_3y)
        for _, label in matched
        if label.equity_multiple_3y is not None
    ]
    return SourceOutcomeRateGroup(
        group=group,
        cohort_case_count=len(cohort),
        source_labeled_case_count=len(matched),
        survived_12m_resolved_count=len(survived),
        survived_12m_rate=_rate(bool(value) for value in survived),
        common_12m_resolved_count=len(common),
        existing_common_survival_rate=_rate(bool(value) for value in common),
        normalized_3y_resolved_count=len(normalized),
        normalized_within_3y_rate=_rate(bool(value) for value in normalized),
        equity_multiple_3y_resolved_count=len(multiples),
        three_x_3y_rate=_rate(value >= 3.0 for value in multiples),
        median_equity_multiple_3y=(median(multiples) if multiples else None),
        case_keys=tuple(_key(item) for item, _ in matched),
    )


def compare_source_outcome_base_rates(
    joint: JointReplayRun,
    outcomes: CsvSourceBackedOutcomeIndex,
    knowledge_cutoff: date,
) -> SourceOutcomeBaseRateComparison:
    known = {label.case_key: label for label in outcomes.known_labels(knowledge_cutoff)}
    cohort_keys = {_key(item) for item in joint.candidates}
    unmatched_known = sorted(set(known) - cohort_keys)

    buckets: dict[str, list[JointDistressSeed]] = {
        "fresh_credit_stress": [],
        "fresh_credit_no_stress": [],
        "no_fresh_credit": [],
    }
    for item in joint.candidates:
        if item.fresh_credit_instrument_count <= 0:
            bucket = "no_fresh_credit"
        elif item.any_credit_stress:
            bucket = "fresh_credit_stress"
        else:
            bucket = "fresh_credit_no_stress"
        buckets[bucket].append(item)

    groups = tuple(
        _summarize_source_group(name, buckets[name], known)
        for name in ("fresh_credit_stress", "fresh_credit_no_stress", "no_fresh_credit")
    )
    warnings: list[str] = []
    if unmatched_known:
        warnings.append(
            "source-backed outcome labels with at least one metric known by the cutoff but outside this replay cohort were ignored: "
            + ", ".join(unmatched_known)
        )
    return SourceOutcomeBaseRateComparison(
        knowledge_cutoff=knowledge_cutoff,
        cohort_case_count=len(joint.candidates),
        known_source_label_count=sum(group.source_labeled_case_count for group in groups),
        groups=groups,
        semantics=(
            "source-backed outcome fields are admitted metric-by-metric only when that metric's known date is on or before the knowledge cutoff",
            "legacy rows without metric-specific provenance fall back to outcome_known_date plus the row-level evidence refs",
            "metric-specific known dates require metric-specific evidence refs so later evidence cannot be used to backdate knowledge",
            "group denominators expose both cohort_case_count and source_labeled_case_count so missing legal/business outcome coverage is not treated as failure",
            "no_fresh_credit is a coverage/liquidity category and must not be interpreted as healthy credit",
        ),
        warnings=tuple(warnings),
    )


def source_outcome_base_rate_to_dict(comparison: SourceOutcomeBaseRateComparison) -> dict[str, object]:
    return {
        "knowledge_cutoff": comparison.knowledge_cutoff.isoformat(),
        "cohort_case_count": comparison.cohort_case_count,
        "known_source_label_count": comparison.known_source_label_count,
        "groups": [asdict(group) for group in comparison.groups],
        "semantics": list(comparison.semantics),
        "warnings": list(comparison.warnings),
    }

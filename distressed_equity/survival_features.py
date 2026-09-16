from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import date
import math
from pathlib import Path
from statistics import median
from typing import Iterable, Literal

from .credit_replay import JointDistressSeed, JointReplayRun
from .source_outcomes import CsvSourceBackedOutcomeIndex, SourceBackedOutcomeLabel


FeatureDimension = Literal[
    "liquidity_runway",
    "nearest_maturity",
    "covenant_headroom",
    "net_leverage",
    "impairment_type",
]


@dataclass(frozen=True)
class SurvivalFeatureSnapshot:
    security_id: str
    analysis_date: date
    evidence_known_date: date
    liquidity_runway_months: float | None
    nearest_maturity_months: float | None
    covenant_headroom_pct: float | None
    net_leverage: float | None
    impairment_type: str | None
    evidence_refs: tuple[str, ...]
    verifier: str
    verified_on: date
    evidence_reopened: bool
    notes: str | None = None

    @property
    def case_key(self) -> str:
        return f"{self.security_id}|{self.analysis_date.isoformat()}"


@dataclass(frozen=True)
class FeatureStratumRate:
    dimension: str
    bucket: str
    credit_group: str
    cohort_case_count: int
    feature_snapshot_count: int
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
class FeatureStrataComparison:
    knowledge_cutoff: date
    cohort_case_count: int
    feature_snapshot_count: int
    known_source_label_count: int
    strata: tuple[FeatureStratumRate, ...]
    semantics: tuple[str, ...]
    warnings: tuple[str, ...]


def _optional(value: str | None) -> str | None:
    clean = str(value or "").strip()
    return clean or None


def _parse_date(value: str | None, *, field: str, row: int) -> date:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"survival features CSV row {row}: {field} is required")
    try:
        return date.fromisoformat(clean[:10])
    except ValueError as exc:
        raise ValueError(f"survival features CSV row {row}: {field} must be YYYY-MM-DD") from exc


def _bool(value: str | None, *, field: str, row: int) -> bool:
    clean = str(value or "").strip().lower()
    if clean in {"true", "1", "yes", "y"}:
        return True
    if clean in {"false", "0", "no", "n"}:
        return False
    raise ValueError(f"survival features CSV row {row}: {field} must be true/false")


def _float(value: str | None, *, field: str, row: int, non_negative: bool) -> float | None:
    clean = str(value or "").strip()
    if not clean:
        return None
    try:
        numeric = float(clean)
    except ValueError as exc:
        raise ValueError(f"survival features CSV row {row}: {field} must be numeric") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"survival features CSV row {row}: {field} must be finite")
    if non_negative and numeric < 0:
        raise ValueError(f"survival features CSV row {row}: {field} must be non-negative")
    return numeric


class CsvSurvivalFeatureIndex:
    """Verified T0 survival features keyed by permanent security_id + analysis_date.

    Numeric definitions are not invented here. This layer only validates point-in-
    time provenance and buckets already-calculated source-backed values.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._snapshots: dict[str, SurvivalFeatureSnapshot] = {}
        self._load()

    def _load(self) -> None:
        required = {
            "security_id",
            "analysis_date",
            "evidence_known_date",
            "liquidity_runway_months",
            "nearest_maturity_months",
            "covenant_headroom_pct",
            "net_leverage",
            "impairment_type",
            "evidence_refs",
            "verification_status",
            "verifier",
            "verified_on",
            "evidence_reopened",
        }
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            missing = sorted(required - fields)
            if missing:
                raise ValueError("survival features CSV missing required columns: " + ", ".join(missing))
            for row_number, raw in enumerate(reader, 2):
                security_id = str(raw.get("security_id") or "").strip()
                if not security_id:
                    continue
                analysis_date = _parse_date(raw.get("analysis_date"), field="analysis_date", row=row_number)
                evidence_known_date = _parse_date(
                    raw.get("evidence_known_date"), field="evidence_known_date", row=row_number
                )
                if evidence_known_date > analysis_date:
                    raise ValueError(
                        f"survival features CSV row {row_number}: evidence_known_date is after analysis_date"
                    )
                status = str(raw.get("verification_status") or "").strip().lower()
                if status != "verified":
                    raise ValueError(
                        f"survival features CSV row {row_number}: verification_status must be verified"
                    )
                verifier = str(raw.get("verifier") or "").strip()
                if not verifier:
                    raise ValueError(f"survival features CSV row {row_number}: verifier is required")
                verified_on = _parse_date(raw.get("verified_on"), field="verified_on", row=row_number)
                reopened = _bool(raw.get("evidence_reopened"), field="evidence_reopened", row=row_number)
                if not reopened:
                    raise ValueError(
                        f"survival features CSV row {row_number}: evidence_reopened must be true"
                    )
                refs = tuple(
                    item.strip()
                    for item in str(raw.get("evidence_refs") or "").split(";")
                    if item.strip()
                )
                if not refs:
                    raise ValueError(f"survival features CSV row {row_number}: evidence_refs are required")

                liquidity = _float(
                    raw.get("liquidity_runway_months"),
                    field="liquidity_runway_months",
                    row=row_number,
                    non_negative=True,
                )
                maturity = _float(
                    raw.get("nearest_maturity_months"),
                    field="nearest_maturity_months",
                    row=row_number,
                    non_negative=True,
                )
                headroom = _float(
                    raw.get("covenant_headroom_pct"),
                    field="covenant_headroom_pct",
                    row=row_number,
                    non_negative=False,
                )
                leverage = _float(
                    raw.get("net_leverage"),
                    field="net_leverage",
                    row=row_number,
                    non_negative=False,
                )
                impairment = _optional(raw.get("impairment_type"))
                if all(value is None for value in (liquidity, maturity, headroom, leverage, impairment)):
                    raise ValueError(
                        f"survival features CSV row {row_number}: at least one T0 feature must be resolved"
                    )

                snapshot = SurvivalFeatureSnapshot(
                    security_id=security_id,
                    analysis_date=analysis_date,
                    evidence_known_date=evidence_known_date,
                    liquidity_runway_months=liquidity,
                    nearest_maturity_months=maturity,
                    covenant_headroom_pct=headroom,
                    net_leverage=leverage,
                    impairment_type=(impairment.lower() if impairment else None),
                    evidence_refs=refs,
                    verifier=verifier,
                    verified_on=verified_on,
                    evidence_reopened=True,
                    notes=_optional(raw.get("notes")),
                )
                if snapshot.case_key in self._snapshots:
                    raise ValueError(f"duplicate survival feature case key: {snapshot.case_key}")
                self._snapshots[snapshot.case_key] = snapshot

    def get(self, security_id: str, analysis_date: date) -> SurvivalFeatureSnapshot | None:
        return self._snapshots.get(f"{security_id}|{analysis_date.isoformat()}")

    def snapshots(self) -> tuple[SurvivalFeatureSnapshot, ...]:
        return tuple(self._snapshots[key] for key in sorted(self._snapshots))


def liquidity_runway_bucket(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 6:
        return "<6m"
    if value < 12:
        return "6-<12m"
    if value < 24:
        return "12-<24m"
    return ">=24m"


def nearest_maturity_bucket(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 12:
        return "<12m"
    if value < 24:
        return "12-<24m"
    if value < 36:
        return "24-<36m"
    return ">=36m"


def covenant_headroom_bucket(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 0:
        return "breach/<0%"
    if value < 10:
        return "0-<10%"
    if value < 25:
        return "10-<25%"
    return ">=25%"


def net_leverage_bucket(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 2:
        return "<2x"
    if value < 4:
        return "2-<4x"
    if value < 6:
        return "4-<6x"
    return ">=6x"


def feature_bucket(snapshot: SurvivalFeatureSnapshot | None, dimension: FeatureDimension) -> str:
    if snapshot is None:
        return "unknown"
    if dimension == "liquidity_runway":
        return liquidity_runway_bucket(snapshot.liquidity_runway_months)
    if dimension == "nearest_maturity":
        return nearest_maturity_bucket(snapshot.nearest_maturity_months)
    if dimension == "covenant_headroom":
        return covenant_headroom_bucket(snapshot.covenant_headroom_pct)
    if dimension == "net_leverage":
        return net_leverage_bucket(snapshot.net_leverage)
    return snapshot.impairment_type or "unknown"


def _credit_group(item: JointDistressSeed) -> str:
    if item.fresh_credit_instrument_count <= 0:
        return "no_fresh_credit"
    return "fresh_credit_stress" if item.any_credit_stress else "fresh_credit_no_stress"


def _case_key(item: JointDistressSeed) -> str:
    return f"{item.equity.security.security_id}|{item.equity.analysis_date.isoformat()}"


def _rate(values: Iterable[bool]) -> float | None:
    rows = tuple(values)
    return None if not rows else sum(rows) / len(rows)


def _summarize(
    dimension: FeatureDimension,
    bucket: str,
    credit_group: str,
    cohort: Iterable[JointDistressSeed],
    features: CsvSurvivalFeatureIndex,
    labels: dict[str, SourceBackedOutcomeLabel],
) -> FeatureStratumRate:
    rows = tuple(cohort)
    snapshots = [
        features.get(item.equity.security.security_id, item.equity.analysis_date)
        for item in rows
    ]
    feature_count = sum(snapshot is not None for snapshot in snapshots)
    matched = [(item, labels[_case_key(item)]) for item in rows if _case_key(item) in labels]
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
    return FeatureStratumRate(
        dimension=dimension,
        bucket=bucket,
        credit_group=credit_group,
        cohort_case_count=len(rows),
        feature_snapshot_count=feature_count,
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
        case_keys=tuple(_case_key(item) for item in rows),
    )


def compare_feature_strata_base_rates(
    joint: JointReplayRun,
    features: CsvSurvivalFeatureIndex,
    outcomes: CsvSourceBackedOutcomeIndex,
    knowledge_cutoff: date,
    *,
    dimensions: Iterable[FeatureDimension] = (
        "liquidity_runway",
        "nearest_maturity",
        "covenant_headroom",
        "net_leverage",
        "impairment_type",
    ),
) -> FeatureStrataComparison:
    dims = tuple(dimensions)
    if len(set(dims)) != len(dims):
        raise ValueError("feature strata dimensions must be unique")
    allowed = {
        "liquidity_runway",
        "nearest_maturity",
        "covenant_headroom",
        "net_leverage",
        "impairment_type",
    }
    unknown = [item for item in dims if item not in allowed]
    if unknown:
        raise ValueError(f"unsupported feature strata dimension(s): {unknown}")

    labels = {label.case_key: label for label in outcomes.known_labels(knowledge_cutoff)}
    cohort_keys = {_case_key(item) for item in joint.candidates}
    feature_keys = {snapshot.case_key for snapshot in features.snapshots()}
    outside_features = sorted(feature_keys - cohort_keys)
    outside_labels = sorted(set(labels) - cohort_keys)

    grouped: dict[tuple[FeatureDimension, str, str], list[JointDistressSeed]] = {}
    for item in joint.candidates:
        snapshot = features.get(item.equity.security.security_id, item.equity.analysis_date)
        credit = _credit_group(item)
        for dimension in dims:
            bucket = feature_bucket(snapshot, dimension)
            grouped.setdefault((dimension, bucket, credit), []).append(item)

    strata = tuple(
        _summarize(dimension, bucket, credit, rows, features, labels)
        for (dimension, bucket, credit), rows in sorted(grouped.items())
    )
    warnings: list[str] = []
    if outside_features:
        warnings.append(
            "verified survival feature rows outside this replay cohort were ignored: " + ", ".join(outside_features)
        )
    if outside_labels:
        warnings.append(
            "source-backed outcome labels outside this replay cohort were ignored: " + ", ".join(outside_labels)
        )
    return FeatureStrataComparison(
        knowledge_cutoff=knowledge_cutoff,
        cohort_case_count=len(joint.candidates),
        feature_snapshot_count=sum(
            features.get(item.equity.security.security_id, item.equity.analysis_date) is not None
            for item in joint.candidates
        ),
        known_source_label_count=sum(_case_key(item) in labels for item in joint.candidates),
        strata=strata,
        semantics=(
            "survival feature values are accepted only from verified rows whose evidence_known_date is on or before the T0 analysis_date",
            "bucket boundaries are deterministic descriptive strata; they do not imply universal causal thresholds",
            "covenant_headroom_pct is consumed as an upstream verified percentage-point value because covenant definitions vary by issuer",
            "unknown feature buckets preserve missing-feature coverage instead of treating missing data as a favorable or adverse state",
            "source-backed outcome denominators include only labels known by the knowledge cutoff; missing outcomes remain missing",
        ),
        warnings=tuple(warnings),
    )


def feature_strata_to_dict(comparison: FeatureStrataComparison) -> dict[str, object]:
    return {
        "knowledge_cutoff": comparison.knowledge_cutoff.isoformat(),
        "cohort_case_count": comparison.cohort_case_count,
        "feature_snapshot_count": comparison.feature_snapshot_count,
        "known_source_label_count": comparison.known_source_label_count,
        "strata": [asdict(item) for item in comparison.strata],
        "semantics": list(comparison.semantics),
        "warnings": list(comparison.warnings),
    }

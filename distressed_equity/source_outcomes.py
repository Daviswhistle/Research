from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, replace
from datetime import date
import math
from pathlib import Path
from statistics import median
from typing import Iterable

from .credit_replay import JointDistressSeed, JointReplayRun


_COMPANY_TERMINAL_EVENT_TYPES = frozenset({
    "liquidation",
    "dissolution",
    "permanent_cessation",
})
_COMMON_TERMINAL_EVENT_TYPES = frozenset({
    "cancellation",
    "extinguishment",
    "cash_acquisition_closed",
    "final_liquidation_distribution",
})
_FINAL_SHAREHOLDER_PAYOFF_EVENT_TYPES = frozenset({
    "fixed_cash_acquisition_closed",
    "final_liquidation_distribution",
    "common_extinguished_no_distribution",
})


@dataclass(frozen=True)
class TerminalOutcomeFact:
    event_type: str
    event_date: date
    known_date: date
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class FinalShareholderPayoffFact:
    event_type: str
    event_date: date
    known_date: date
    payoff_multiple: float
    evidence_refs: tuple[str, ...]


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
    company_terminal_fact: TerminalOutcomeFact | None
    common_terminal_fact: TerminalOutcomeFact | None
    final_shareholder_payoff_fact: FinalShareholderPayoffFact | None
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
        """Return only outcome fields and provenance knowable by `cutoff`.

        Row-level evidence can include later documents supporting later metrics.
        The cutoff-safe view therefore rebuilds `evidence_refs` exclusively from
        the metric-specific refs admitted by the cutoff instead of retaining the
        original row-wide evidence set. Terminal/payoff facts are retained only
        when they were known by the cutoff and support an admitted outcome metric.
        """

        updates: dict[str, object] = {}
        known_dates: list[date] = []
        admitted_refs: list[str] = []
        admitted_values: dict[str, object | None] = {}
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
                refs = getattr(self, refs_field)
                updates[value_field] = value
                updates[date_field] = known_date
                updates[refs_field] = refs
                admitted_values[value_field] = value
                known_dates.append(known_date)
                admitted_refs.extend(refs)
            else:
                updates[value_field] = None
                updates[date_field] = None
                updates[refs_field] = ()
                admitted_values[value_field] = None
        if not known_dates:
            return None

        company_terminal_admitted = (
            self.company_terminal_fact is not None
            and self.company_terminal_fact.known_date <= cutoff
            and (
                admitted_values["survived_12m"] is False
                or admitted_values["normalized_within_3y"] is False
            )
        )
        common_terminal_admitted = (
            self.common_terminal_fact is not None
            and self.common_terminal_fact.known_date <= cutoff
            and admitted_values["existing_common_survived_12m"] is False
        )
        final_payoff_admitted = (
            self.final_shareholder_payoff_fact is not None
            and self.final_shareholder_payoff_fact.known_date <= cutoff
            and admitted_values["equity_multiple_3y"] is not None
        )
        updates["company_terminal_fact"] = self.company_terminal_fact if company_terminal_admitted else None
        updates["common_terminal_fact"] = self.common_terminal_fact if common_terminal_admitted else None
        updates["final_shareholder_payoff_fact"] = (
            self.final_shareholder_payoff_fact if final_payoff_admitted else None
        )
        updates["outcome_known_date"] = max(known_dates)
        updates["evidence_refs"] = tuple(dict.fromkeys(admitted_refs))
        # Free-form row notes may summarize later evidence/outcomes and therefore
        # are intentionally withheld from the cutoff-safe partial view.
        updates["notes"] = None
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


def _terminal_fact(
    raw: dict[str, str | None],
    *,
    prefix: str,
    allowed_types: frozenset[str],
    analysis_date: date,
    row: int,
) -> TerminalOutcomeFact | None:
    type_field = f"{prefix}_terminal_event_type"
    date_field = f"{prefix}_terminal_event_date"
    known_field = f"{prefix}_terminal_event_known_date"
    refs_field = f"{prefix}_terminal_event_evidence_refs"
    event_type = str(raw.get(type_field) or "").strip().lower()
    raw_date = str(raw.get(date_field) or "").strip()
    raw_known = str(raw.get(known_field) or "").strip()
    refs = _refs(raw.get(refs_field))
    if not any((event_type, raw_date, raw_known, refs)):
        return None
    if not event_type or not raw_date or not raw_known or not refs:
        raise ValueError(
            f"source outcomes CSV row {row}: {prefix} terminal fact requires event_type, event_date, event_known_date, and evidence_refs"
        )
    if event_type not in allowed_types:
        raise ValueError(
            f"source outcomes CSV row {row}: {type_field} must be one of {sorted(allowed_types)}"
        )
    event_date = _parse_date(raw_date, field=date_field, row=row)
    known_date = _parse_date(raw_known, field=known_field, row=row)
    if event_date < analysis_date:
        raise ValueError(
            f"source outcomes CSV row {row}: {date_field} cannot precede analysis_date"
        )
    if known_date < event_date:
        raise ValueError(
            f"source outcomes CSV row {row}: {known_field} cannot precede terminal event date"
        )
    return TerminalOutcomeFact(
        event_type=event_type,
        event_date=event_date,
        known_date=known_date,
        evidence_refs=refs,
    )


def _final_shareholder_payoff_fact(
    raw: dict[str, str | None],
    *,
    analysis_date: date,
    row: int,
) -> FinalShareholderPayoffFact | None:
    type_field = "final_shareholder_payoff_event_type"
    date_field = "final_shareholder_payoff_event_date"
    known_field = "final_shareholder_payoff_known_date"
    multiple_field = "final_shareholder_payoff_multiple"
    refs_field = "final_shareholder_payoff_evidence_refs"
    event_type = str(raw.get(type_field) or "").strip().lower()
    raw_date = str(raw.get(date_field) or "").strip()
    raw_known = str(raw.get(known_field) or "").strip()
    raw_multiple = str(raw.get(multiple_field) or "").strip()
    refs = _refs(raw.get(refs_field))
    if not any((event_type, raw_date, raw_known, raw_multiple, refs)):
        return None
    if not event_type or not raw_date or not raw_known or not raw_multiple or not refs:
        raise ValueError(
            f"source outcomes CSV row {row}: final shareholder payoff fact requires event_type, event_date, known_date, payoff_multiple, and evidence_refs"
        )
    if event_type not in _FINAL_SHAREHOLDER_PAYOFF_EVENT_TYPES:
        raise ValueError(
            f"source outcomes CSV row {row}: {type_field} must be one of {sorted(_FINAL_SHAREHOLDER_PAYOFF_EVENT_TYPES)}"
        )
    event_date = _parse_date(raw_date, field=date_field, row=row)
    known_date = _parse_date(raw_known, field=known_field, row=row)
    payoff_multiple = _float(raw_multiple, field=multiple_field, row=row)
    assert payoff_multiple is not None
    if event_date < analysis_date:
        raise ValueError(
            f"source outcomes CSV row {row}: {date_field} cannot precede analysis_date"
        )
    if known_date < event_date:
        raise ValueError(
            f"source outcomes CSV row {row}: {known_field} cannot precede final payoff event date"
        )
    if event_type == "common_extinguished_no_distribution" and payoff_multiple != 0:
        raise ValueError(
            f"source outcomes CSV row {row}: common_extinguished_no_distribution requires payoff_multiple=0"
        )
    return FinalShareholderPayoffFact(
        event_type=event_type,
        event_date=event_date,
        known_date=known_date,
        payoff_multiple=payoff_multiple,
        evidence_refs=refs,
    )


def _validate_early_false(
    *,
    metric: str,
    value: bool | None,
    known_date: date | None,
    metric_refs: tuple[str, ...],
    horizon: date,
    terminal_fact: TerminalOutcomeFact | None,
    row: int,
) -> None:
    if known_date is None or known_date >= horizon:
        return
    if value is not False:
        raise ValueError(
            f"source outcomes CSV row {row}: {metric} cannot be known before its nominal horizon unless an explicit terminal fact irreversibly establishes false"
        )
    if terminal_fact is None:
        raise ValueError(
            f"source outcomes CSV row {row}: early {metric}=false requires an explicit verified terminal fact"
        )
    if terminal_fact.event_date > horizon:
        raise ValueError(
            f"source outcomes CSV row {row}: terminal event for {metric} occurs after the metric horizon"
        )
    if terminal_fact.known_date > known_date:
        raise ValueError(
            f"source outcomes CSV row {row}: {metric} known date precedes supporting terminal-event knowledge"
        )
    missing_refs = sorted(set(terminal_fact.evidence_refs) - set(metric_refs))
    if missing_refs:
        raise ValueError(
            f"source outcomes CSV row {row}: {metric} evidence_refs must include terminal-event evidence refs: {', '.join(missing_refs)}"
        )


def _validate_final_payoff(
    *,
    multiple: float | None,
    known_date: date | None,
    metric_refs: tuple[str, ...],
    horizon: date,
    payoff_fact: FinalShareholderPayoffFact | None,
    row: int,
) -> None:
    if payoff_fact is not None and multiple is None:
        raise ValueError(
            f"source outcomes CSV row {row}: final shareholder payoff fact requires equity_multiple_3y"
        )
    if multiple is None or known_date is None:
        return
    if payoff_fact is None:
        if known_date < horizon:
            raise ValueError(
                f"source outcomes CSV row {row}: equity_multiple_3y cannot be known before the +3y horizon without an explicit final shareholder-payoff model"
            )
        return
    if not math.isclose(payoff_fact.payoff_multiple, multiple, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError(
            f"source outcomes CSV row {row}: final shareholder payoff multiple does not match equity_multiple_3y"
        )
    if payoff_fact.known_date > known_date:
        raise ValueError(
            f"source outcomes CSV row {row}: equity_multiple_3y known date precedes final-payoff knowledge"
        )
    if payoff_fact.event_date > horizon:
        raise ValueError(
            f"source outcomes CSV row {row}: final shareholder payoff event occurs after the +3y horizon"
        )
    missing_refs = sorted(set(payoff_fact.evidence_refs) - set(metric_refs))
    if missing_refs:
        raise ValueError(
            f"source outcomes CSV row {row}: equity_multiple_3y evidence_refs must include final-payoff evidence refs: {', '.join(missing_refs)}"
        )


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
                final_payoff = _final_shareholder_payoff_fact(
                    raw,
                    analysis_date=analysis_date,
                    row=row_number,
                )
                if final_payoff is not None and multiple is None:
                    raise ValueError(
                        f"source outcomes CSV row {row_number}: final shareholder payoff fact requires equity_multiple_3y"
                    )
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

                company_terminal = _terminal_fact(
                    raw,
                    prefix="company",
                    allowed_types=_COMPANY_TERMINAL_EVENT_TYPES,
                    analysis_date=analysis_date,
                    row=row_number,
                )
                common_terminal = _terminal_fact(
                    raw,
                    prefix="common",
                    allowed_types=_COMMON_TERMINAL_EVENT_TYPES,
                    analysis_date=analysis_date,
                    row=row_number,
                )
                if company_terminal is not None and not (survived is False or normalized is False):
                    raise ValueError(
                        f"source outcomes CSV row {row_number}: company terminal fact requires survived_12m=false or normalized_within_3y=false"
                    )
                if common_terminal is not None and common_survived is not False:
                    raise ValueError(
                        f"source outcomes CSV row {row_number}: common terminal fact requires existing_common_survived_12m=false"
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

                _validate_early_false(
                    metric="survived_12m",
                    value=survived,
                    known_date=survived_known,
                    metric_refs=survived_refs,
                    horizon=_add_years(analysis_date, 1),
                    terminal_fact=company_terminal,
                    row=row_number,
                )
                _validate_early_false(
                    metric="existing_common_survived_12m",
                    value=common_survived,
                    known_date=common_known,
                    metric_refs=common_refs,
                    horizon=_add_years(analysis_date, 1),
                    terminal_fact=common_terminal,
                    row=row_number,
                )
                if normalized is False:
                    _validate_early_false(
                        metric="normalized_within_3y",
                        value=normalized,
                        known_date=normalized_known,
                        metric_refs=normalized_refs,
                        horizon=_add_years(analysis_date, 3),
                        terminal_fact=company_terminal,
                        row=row_number,
                    )
                _validate_final_payoff(
                    multiple=multiple,
                    known_date=multiple_known,
                    metric_refs=multiple_refs,
                    horizon=_add_years(analysis_date, 3),
                    payoff_fact=final_payoff,
                    row=row_number,
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
                    company_terminal_fact=company_terminal,
                    common_terminal_fact=common_terminal,
                    final_shareholder_payoff_fact=final_payoff,
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
            "cutoff-safe labels rebuild row-level evidence_refs from only the admitted metric evidence; later row-wide evidence and free-form notes are withheld",
            "legacy rows without metric-specific provenance fall back to outcome_known_date plus the row-level evidence refs",
            "metric-specific known dates require metric-specific evidence refs so later evidence cannot be used to backdate knowledge",
            "early false survival/common/normalization outcomes are admitted only with explicit metric-matched terminal-event provenance; no event-type inference is performed from generic labels",
            "three-year equity multiples may be admitted before the nominal horizon only when an explicit verified final shareholder-payoff fact fixes the complete payoff and matches the metric provenance",
            "terminal common-survival facts and final shareholder-payoff facts remain separate evidence layers and are never inferred from one another",
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

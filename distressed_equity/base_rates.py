from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
from statistics import median
from typing import Iterable


@dataclass(frozen=True)
class BaseRateCase:
    case_id: str
    ticker: str
    analysis_date: date
    outcome_known_date: date
    industry_group: str | None = None
    impairment_type: str | None = None
    leverage_bucket: str | None = None
    survived_12m: bool | None = None
    existing_common_survived_12m: bool | None = None
    normalized_within_3y: bool | None = None
    equity_multiple_3y: float | None = None
    notes: str | None = None


@dataclass(frozen=True)
class BaseRateSummary:
    available_case_count: int
    matched_case_count: int
    survived_12m_rate: float | None
    existing_common_survival_rate: float | None
    normalized_within_3y_rate: float | None
    three_x_within_3y_rate: float | None
    median_equity_multiple_3y: float | None
    case_ids: tuple[str, ...]


def _rate(values: Iterable[bool | None]) -> float | None:
    resolved = [value for value in values if value is not None]
    if not resolved:
        return None
    return sum(bool(value) for value in resolved) / len(resolved)


class BaseRateLibrary:
    """Outcome library with an explicit `outcome_known_date` anti-look-ahead gate."""

    def __init__(self, cases: Iterable[BaseRateCase]) -> None:
        self.cases = tuple(cases)

    def available_as_of(
        self,
        cutoff: date,
        *,
        exclude_case_ids: Iterable[str] = (),
    ) -> tuple[BaseRateCase, ...]:
        excluded = set(exclude_case_ids)
        return tuple(
            case
            for case in self.cases
            if case.outcome_known_date <= cutoff and case.case_id not in excluded
        )

    def summarize(
        self,
        cutoff: date,
        *,
        industry_group: str | None = None,
        impairment_type: str | None = None,
        leverage_bucket: str | None = None,
        exclude_case_ids: Iterable[str] = (),
    ) -> BaseRateSummary:
        available = self.available_as_of(cutoff, exclude_case_ids=exclude_case_ids)
        matched = [
            case
            for case in available
            if (industry_group is None or case.industry_group == industry_group)
            and (impairment_type is None or case.impairment_type == impairment_type)
            and (leverage_bucket is None or case.leverage_bucket == leverage_bucket)
        ]
        multiples = [
            case.equity_multiple_3y
            for case in matched
            if case.equity_multiple_3y is not None
        ]
        return BaseRateSummary(
            available_case_count=len(available),
            matched_case_count=len(matched),
            survived_12m_rate=_rate(case.survived_12m for case in matched),
            existing_common_survival_rate=_rate(
                case.existing_common_survived_12m for case in matched
            ),
            normalized_within_3y_rate=_rate(
                case.normalized_within_3y for case in matched
            ),
            three_x_within_3y_rate=_rate(
                (
                    None
                    if case.equity_multiple_3y is None
                    else case.equity_multiple_3y >= 3.0
                )
                for case in matched
            ),
            median_equity_multiple_3y=(median(multiples) if multiples else None),
            case_ids=tuple(case.case_id for case in matched),
        )


def case_from_dict(raw: dict[str, object]) -> BaseRateCase:
    return BaseRateCase(
        case_id=str(raw["case_id"]),
        ticker=str(raw.get("ticker") or ""),
        analysis_date=date.fromisoformat(str(raw["analysis_date"])),
        outcome_known_date=date.fromisoformat(str(raw["outcome_known_date"])),
        industry_group=(str(raw["industry_group"]) if raw.get("industry_group") is not None else None),
        impairment_type=(str(raw["impairment_type"]) if raw.get("impairment_type") is not None else None),
        leverage_bucket=(str(raw["leverage_bucket"]) if raw.get("leverage_bucket") is not None else None),
        survived_12m=(bool(raw["survived_12m"]) if raw.get("survived_12m") is not None else None),
        existing_common_survived_12m=(
            bool(raw["existing_common_survived_12m"])
            if raw.get("existing_common_survived_12m") is not None
            else None
        ),
        normalized_within_3y=(
            bool(raw["normalized_within_3y"])
            if raw.get("normalized_within_3y") is not None
            else None
        ),
        equity_multiple_3y=(
            float(raw["equity_multiple_3y"])
            if raw.get("equity_multiple_3y") is not None
            else None
        ),
        notes=(str(raw["notes"]) if raw.get("notes") is not None else None),
    )


def load_base_rate_library(path: str | Path) -> BaseRateLibrary:
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        if path.suffix.lower() == ".jsonl":
            rows = [json.loads(line) for line in handle if line.strip()]
        else:
            payload = json.load(handle)
            rows = payload if isinstance(payload, list) else payload["cases"]
    return BaseRateLibrary(case_from_dict(row) for row in rows)

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping


class EventCategory(str, Enum):
    ACQUISITION_CONTROL = "acquisition_control"
    MERGER_REORGANIZATION = "merger_reorganization"
    PORTFOLIO_PIVOT = "portfolio_pivot"
    FINANCING_DILUTION = "financing_dilution"
    EXECUTION_VALIDATION = "execution_validation"
    GOVERNANCE_CHANGE = "governance_change"
    DIVESTITURE = "divestiture"
    OTHER = "other"


class AlertBand(str, Enum):
    URGENT = "urgent"
    REVIEW = "review"
    WATCH = "watch"
    LOW = "low"


@dataclass(frozen=True)
class Disclosure:
    corp_cls: str
    corp_name: str
    corp_code: str
    stock_code: str
    report_nm: str
    rcept_no: str
    flr_nm: str
    rcept_dt: str
    rm: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_api(cls, row: Mapping[str, Any]) -> "Disclosure":
        return cls(
            corp_cls=str(row.get("corp_cls") or "").strip(),
            corp_name=str(row.get("corp_name") or "").strip(),
            corp_code=str(row.get("corp_code") or "").strip(),
            stock_code=str(row.get("stock_code") or "").strip(),
            report_nm=str(row.get("report_nm") or "").strip(),
            rcept_no=str(row.get("rcept_no") or "").strip(),
            flr_nm=str(row.get("flr_nm") or "").strip(),
            rcept_dt=str(row.get("rcept_dt") or "").strip(),
            rm=str(row.get("rm") or "").strip(),
            raw=dict(row),
        )

    @property
    def viewer_url(self) -> str:
        return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={self.rcept_no}"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["raw"] = dict(self.raw)
        payload["viewer_url"] = self.viewer_url
        return payload


@dataclass(frozen=True)
class EventClassification:
    category: EventCategory
    subtype: str
    base_attention: float
    structured_kind: str | None = None
    requires_document: bool = False
    is_correction: bool = False
    normalized_report_name: str = ""
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["category"] = self.category.value
        return payload


@dataclass(frozen=True)
class RelatedEvent:
    rcept_no: str
    rcept_dt: str
    category: EventCategory
    subtype: str
    facts: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rcept_no": self.rcept_no,
            "rcept_dt": self.rcept_dt,
            "category": self.category.value,
            "subtype": self.subtype,
            "facts": dict(self.facts),
        }


@dataclass(frozen=True)
class ScoredCandidate:
    disclosure: Disclosure
    classification: EventClassification
    facts: Mapping[str, Any]
    attention_score: float
    financing_risk_score: float
    band: AlertBand
    reasons: tuple[str, ...]
    risk_reasons: tuple[str, ...]
    cluster_tags: tuple[str, ...] = ()
    related_receipts: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "disclosure": self.disclosure.to_dict(),
            "classification": self.classification.to_dict(),
            "facts": dict(self.facts),
            "attention_score": self.attention_score,
            "financing_risk_score": self.financing_risk_score,
            "band": self.band.value,
            "reasons": list(self.reasons),
            "risk_reasons": list(self.risk_reasons),
            "cluster_tags": list(self.cluster_tags),
            "related_receipts": list(self.related_receipts),
            "warnings": list(self.warnings),
        }

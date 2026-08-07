from __future__ import annotations

import re

from .models import EventCategory, EventClassification


_CORRECTION_PREFIX_RE = re.compile(r"^(?:\[[^\]]+\]\s*)+")
_CORRECTION_WORDS = ("기재정정", "첨부정정", "첨부추가", "발행조건확정", "정정")


def strip_report_prefixes(report_name: str) -> tuple[str, bool]:
    original = str(report_name or "").strip()
    match = _CORRECTION_PREFIX_RE.match(original)
    if not match:
        return original, False
    prefix = match.group(0)
    cleaned = original[match.end() :].strip()
    is_correction = any(word in prefix for word in _CORRECTION_WORDS)
    return cleaned, is_correction


def compact_report_name(report_name: str) -> str:
    cleaned, _ = strip_report_prefixes(report_name)
    return re.sub(r"[\sㆍ·・\-_/]", "", cleaned)


def _classification(
    *,
    category: EventCategory,
    subtype: str,
    base_attention: float,
    report_name: str,
    structured_kind: str | None = None,
    requires_document: bool = False,
    tags: tuple[str, ...] = (),
) -> EventClassification:
    normalized, is_correction = strip_report_prefixes(report_name)
    return EventClassification(
        category=category,
        subtype=subtype,
        base_attention=float(base_attention),
        structured_kind=structured_kind,
        requires_document=bool(requires_document),
        is_correction=is_correction,
        normalized_report_name=normalized,
        tags=tags,
    )


def classify_report(report_name: str) -> EventClassification:
    compact = compact_report_name(report_name)

    if any(
        token in compact
        for token in (
            "타법인주식및출자증권양수결정",
            "타법인주식및출자증권취득결정",
            "영업양수결정",
            "최대주주변경을수반하는주식양수도계약체결",
        )
    ):
        structured = "acquisition" if "타법인주식및출자증권" in compact else None
        return _classification(
            category=EventCategory.ACQUISITION_CONTROL,
            subtype="equity_or_business_acquisition",
            base_attention=25,
            report_name=report_name,
            structured_kind=structured,
            requires_document=structured is None,
            tags=("transformation_core", "control_change_candidate"),
        )

    if any(
        token in compact
        for token in (
            "회사합병결정",
            "회사분할합병결정",
            "회사분할결정",
            "주식교환이전결정",
            "주식의포괄적교환이전",
        )
    ):
        return _classification(
            category=EventCategory.MERGER_REORGANIZATION,
            subtype="merger_or_reorganization",
            base_attention=40,
            report_name=report_name,
            requires_document=True,
            tags=("transformation_core",),
        )

    if any(
        token in compact
        for token in (
            "타법인주식및출자증권양도결정",
            "영업양도결정",
            "유형자산양도결정",
        )
    ):
        return _classification(
            category=EventCategory.DIVESTITURE,
            subtype="business_or_asset_divestiture",
            base_attention=20,
            report_name=report_name,
            requires_document=True,
            tags=("portfolio_change",),
        )

    if "전환사채권발행결정" in compact:
        return _classification(
            category=EventCategory.FINANCING_DILUTION,
            subtype="convertible_bond",
            base_attention=5,
            report_name=report_name,
            structured_kind="cb",
            tags=("dilution", "financing"),
        )

    if "신주인수권부사채권발행결정" in compact:
        return _classification(
            category=EventCategory.FINANCING_DILUTION,
            subtype="bond_with_warrants",
            base_attention=5,
            report_name=report_name,
            structured_kind="bw",
            tags=("dilution", "financing"),
        )

    if "교환사채권발행결정" in compact:
        return _classification(
            category=EventCategory.FINANCING_DILUTION,
            subtype="exchangeable_bond",
            base_attention=4,
            report_name=report_name,
            requires_document=True,
            tags=("financing",),
        )

    if "유상증자결정" in compact or "유무상증자결정" in compact:
        return _classification(
            category=EventCategory.FINANCING_DILUTION,
            subtype="paid_in_capital_increase",
            base_attention=5,
            report_name=report_name,
            structured_kind="equity_issuance",
            tags=("dilution", "financing"),
        )

    if any(
        token in compact
        for token in (
            "단기차입금증가결정",
            "장기차입금증가결정",
            "채무보증결정",
            "담보제공결정",
            "금전대여결정",
        )
    ):
        return _classification(
            category=EventCategory.FINANCING_DILUTION,
            subtype="debt_or_guarantee",
            base_attention=4,
            report_name=report_name,
            requires_document=True,
            tags=("financing", "balance_sheet_risk"),
        )

    if any(
        token in compact
        for token in (
            "사업목적추가",
            "정관변경",
            "신규시설투자",
            "시설투자",
            "생산시설증설",
            "유형자산양수결정",
            "신규사업진출",
        )
    ):
        return _classification(
            category=EventCategory.PORTFOLIO_PIVOT,
            subtype="business_scope_or_capacity_change",
            base_attention=28,
            report_name=report_name,
            requires_document=True,
            tags=("portfolio_change",),
        )

    if any(
        token in compact
        for token in (
            "주주총회소집공고",
            "주주총회소집결의",
        )
    ):
        return _classification(
            category=EventCategory.PORTFOLIO_PIVOT,
            subtype="shareholder_meeting_agenda",
            base_attention=0,
            report_name=report_name,
            requires_document=True,
            tags=("agenda_scan",),
        )

    if any(
        token in compact
        for token in (
            "단일판매공급계약체결",
            "공급계약체결",
            "기술이전계약",
            "공동개발계약",
            "수주계약",
            "투자판단관련주요경영사항",
        )
    ):
        return _classification(
            category=EventCategory.EXECUTION_VALIDATION,
            subtype="commercial_validation",
            base_attention=18,
            report_name=report_name,
            requires_document=True,
            tags=("execution_evidence",),
        )

    if any(
        token in compact
        for token in (
            "최대주주변경",
            "대표이사변경",
            "경영권변경",
        )
    ):
        return _classification(
            category=EventCategory.GOVERNANCE_CHANGE,
            subtype="control_or_management_change",
            base_attention=12,
            report_name=report_name,
            requires_document=True,
            tags=("governance",),
        )

    return _classification(
        category=EventCategory.OTHER,
        subtype="other",
        base_attention=0,
        report_name=report_name,
    )

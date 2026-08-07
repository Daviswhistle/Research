from __future__ import annotations

from typing import Any, Iterable, Mapping

from .models import (
    AlertBand,
    Disclosure,
    EventCategory,
    EventClassification,
    RelatedEvent,
    ScoredCandidate,
)


def _as_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(float(value), upper))


def _positive_sum(facts: Mapping[str, Any], keys: Iterable[str]) -> float:
    total = 0.0
    for key in keys:
        value = _as_float(facts.get(key))
        if value is not None and value > 0:
            total += value
    return total


def _band(attention_score: float) -> AlertBand:
    if attention_score >= 75:
        return AlertBand.URGENT
    if attention_score >= 55:
        return AlertBand.REVIEW
    if attention_score >= 35:
        return AlertBand.WATCH
    return AlertBand.LOW


def _score_ratio(
    value: float | None,
    *,
    levels: tuple[tuple[float, float], ...],
) -> float:
    if value is None:
        return 0.0
    for threshold, score in levels:
        if value >= threshold:
            return score
    return 0.0


def _has_confirmed_pivot(subtype: str, facts: Mapping[str, Any]) -> bool:
    if str(subtype) != "shareholder_meeting_agenda":
        return True
    phrases = facts.get("business_purpose_phrases")
    return isinstance(phrases, list) and bool(phrases)


def score_candidate(
    *,
    disclosure: Disclosure,
    classification: EventClassification,
    facts: Mapping[str, Any],
    related_events: Iterable[RelatedEvent] = (),
    warnings: Iterable[str] = (),
) -> ScoredCandidate:
    attention = float(classification.base_attention)
    risk = 0.0
    reasons: list[str] = []
    risk_reasons: list[str] = []
    cluster_tags: list[str] = []

    related = tuple(related_events)
    related_categories = {event.category for event in related}
    related_receipts = tuple(event.rcept_no for event in related if event.rcept_no)
    related_confirmed_pivot = any(
        event.category == EventCategory.PORTFOLIO_PIVOT
        and _has_confirmed_pivot(event.subtype, event.facts)
        for event in related
    )
    current_confirmed_pivot = (
        classification.category == EventCategory.PORTFOLIO_PIVOT
        and _has_confirmed_pivot(classification.subtype, facts)
    )

    if classification.base_attention > 0:
        reasons.append(
            f"{classification.category.value}/{classification.subtype} 기본 주목도 "
            f"{classification.base_attention:.0f}점"
        )
    if classification.is_correction:
        reasons.append("정정·조건확정 공시이므로 기존 사건의 가정 변경 여부를 확인해야 함")

    if classification.category == EventCategory.ACQUISITION_CONTROL:
        if bool(facts.get("control_acquisition")):
            attention += 20
            reasons.append("경영권 또는 지배력 확보가 수반되는 인수")

        asset_ratio = _as_float(facts.get("asset_ratio_pct"))
        asset_points = _score_ratio(
            asset_ratio,
            levels=((100, 20), (50, 15), (20, 10), (10, 5)),
        )
        if asset_points:
            attention += asset_points
            reasons.append(f"인수금액이 총자산의 {asset_ratio:.1f}%")
        if asset_ratio is not None and asset_ratio >= 50:
            risk += 12 if asset_ratio < 100 else 20
            risk_reasons.append(f"총자산 대비 인수 규모가 {asset_ratio:.1f}%로 큼")

        equity_ratio = _as_float(facts.get("equity_ratio_pct"))
        equity_points = _score_ratio(
            equity_ratio,
            levels=((100, 15), (50, 10), (20, 5)),
        )
        if equity_points:
            attention += equity_points
            reasons.append(f"인수금액이 자기자본의 {equity_ratio:.1f}%")
        if equity_ratio is not None and equity_ratio >= 50:
            risk += 12 if equity_ratio < 100 else 20
            risk_reasons.append(f"자기자본 대비 인수 규모가 {equity_ratio:.1f}%로 큼")

        if bool(facts.get("new_business_language")):
            attention += 8
            reasons.append("신규사업·사업다각화 목적이 명시됨")
        if str(facts.get("target_business") or "").strip():
            attention += 2
            reasons.append("인수대상 주요사업이 구조화 공시에 명시됨")
        if bool(facts.get("debt_like_payment")):
            risk += 15
            risk_reasons.append("거래대금 지급에 차입·사채·인수금융 표현이 포함됨")
        put_option = str(facts.get("put_option") or "").strip()
        if put_option not in {"", "-", "N", "부", "아니오", "미해당", "해당없음", "없음"}:
            risk += 5
            risk_reasons.append("풋옵션 등 추가 계약조건이 존재할 수 있음")

    if classification.category == EventCategory.FINANCING_DILUTION:
        dilution_ratio = _as_float(facts.get("dilution_ratio_pct"))
        dilution_risk = _score_ratio(
            dilution_ratio,
            levels=((50, 40), (30, 30), (15, 20), (5, 10)),
        )
        if dilution_risk:
            risk += dilution_risk
            attention += min(dilution_risk * 0.25, 10)
            risk_reasons.append(f"잠재 희석률이 {dilution_ratio:.1f}%")
            reasons.append("대규모 자금조달은 변신 거래의 크기를 보여주는 보조 신호")

        acquisition_funding = _positive_sum(
            facts,
            ("funds_business_acquisition", "funds_other_company_securities"),
        )
        if acquisition_funding > 0:
            attention += 15
            reasons.append("자금 사용 목적에 영업양수·타법인 증권 취득이 포함됨")
            cluster_tags.append("transformation_financing")

        debt_repayment = _as_float(facts.get("funds_debt_repayment"))
        if debt_repayment is not None and debt_repayment > 0:
            risk += 10
            risk_reasons.append("조달자금 일부가 채무상환에 사용됨")

        issue_method = str(facts.get("issue_method") or "")
        if "제3자" in issue_method or "사모" in issue_method:
            risk += 5
            risk_reasons.append(f"발행방법이 {issue_method or '제3자배정·사모'}")

    if classification.category == EventCategory.PORTFOLIO_PIVOT:
        phrases = facts.get("business_purpose_phrases")
        if isinstance(phrases, list) and phrases:
            if classification.subtype == "shareholder_meeting_agenda":
                attention += 35
                reasons.append("주총 안건 원문에서 사업목적·신규사업 관련 문구를 확인")
            else:
                attention += 15
                reasons.append("원문에서 사업목적·신규사업 관련 문구를 확인")
        if facts.get("document_financial_snapshot"):
            attention += 3
            reasons.append("원문에서 재무수치 후보를 추출해 후속 검증 가능")

    if classification.category == EventCategory.EXECUTION_VALIDATION:
        if EventCategory.ACQUISITION_CONTROL in related_categories:
            attention += 20
            reasons.append("최근 인수 이후 공급계약·상업화 증거가 이어짐")
            cluster_tags.append("execution_confirmation")
        elif related_confirmed_pivot:
            attention += 12
            reasons.append("최근 사업전환 공시 이후 실행 증거가 이어짐")
            cluster_tags.append("pivot_confirmation")

    current_is_acquisition = classification.category == EventCategory.ACQUISITION_CONTROL
    current_is_financing = classification.category == EventCategory.FINANCING_DILUTION
    if (
        current_is_acquisition
        and EventCategory.FINANCING_DILUTION in related_categories
    ) or (
        current_is_financing
        and EventCategory.ACQUISITION_CONTROL in related_categories
    ):
        attention += 10
        risk += 20
        reasons.append("30일 내 인수와 자금조달 공시가 결합됨")
        risk_reasons.append("레버리지·메자닌을 동반한 변신 거래일 가능성")
        cluster_tags.append("leveraged_transformation")

    if (
        current_is_acquisition
        and related_confirmed_pivot
    ) or (
        current_confirmed_pivot
        and EventCategory.ACQUISITION_CONTROL in related_categories
    ):
        attention += 10
        reasons.append("인수와 사업범위 변경이 함께 나타남")
        cluster_tags.append("portfolio_pivot_confirmation")

    if (
        classification.category == EventCategory.GOVERNANCE_CHANGE
        and EventCategory.ACQUISITION_CONTROL in related_categories
    ) or (
        current_is_acquisition
        and EventCategory.GOVERNANCE_CHANGE in related_categories
    ):
        attention += 8
        risk += 5
        reasons.append("경영권·대표 변경이 인수 사건과 인접")
        risk_reasons.append("거버넌스 재편이 동시에 진행됨")
        cluster_tags.append("governance_transition")

    attention = round(_clamp(attention), 1)
    risk = round(_clamp(risk), 1)
    return ScoredCandidate(
        disclosure=disclosure,
        classification=classification,
        facts=dict(facts),
        attention_score=attention,
        financing_risk_score=risk,
        band=_band(attention),
        reasons=tuple(reasons),
        risk_reasons=tuple(risk_reasons),
        cluster_tags=tuple(dict.fromkeys(cluster_tags)),
        related_receipts=related_receipts,
        warnings=tuple(str(item) for item in warnings),
    )

from transformation_scanner.models import Disclosure, EventCategory, RelatedEvent
from transformation_scanner.scoring import score_candidate
from transformation_scanner.taxonomy import classify_report


def _disclosure(report_nm: str, *, rcept_no: str = "20251208000001") -> Disclosure:
    return Disclosure(
        corp_cls="K",
        corp_name="성호전자",
        corp_code="00123456",
        stock_code="043260",
        report_nm=report_nm,
        rcept_no=rcept_no,
        flr_nm="성호전자",
        rcept_dt="20251208",
    )


def test_sungho_like_leveraged_control_acquisition_is_urgent_and_high_risk() -> None:
    disclosure = _disclosure("주요사항보고서(타법인 주식 및 출자증권 양수결정)")
    classification = classify_report(disclosure.report_nm)
    related = RelatedEvent(
        rcept_no="20251207000001",
        rcept_dt="20251207",
        category=EventCategory.FINANCING_DILUTION,
        subtype="convertible_bond",
        facts={"dilution_ratio_pct": 40.0},
    )

    candidate = score_candidate(
        disclosure=disclosure,
        classification=classification,
        facts={
            "target_name": "에이디에스테크",
            "target_business": "광통신 액티브 정렬 장비",
            "asset_ratio_pct": 150.0,
            "equity_ratio_pct": 220.0,
            "post_stake_pct": 87.5,
            "purpose": "경영권 확보 및 신규사업 진출",
            "payment_terms": "전환사채 및 인수금융으로 조달",
            "control_acquisition": True,
            "new_business_language": True,
            "debt_like_payment": True,
        },
        related_events=(related,),
    )

    assert candidate.band.value == "urgent"
    assert candidate.attention_score >= 90
    assert candidate.financing_risk_score >= 70
    assert "leveraged_transformation" in candidate.cluster_tags


def test_small_passive_equity_purchase_is_not_title_only_watch() -> None:
    disclosure = _disclosure(
        "주요사항보고서(타법인 주식 및 출자증권 양수결정)",
        rcept_no="20251208000003",
    )
    classification = classify_report(disclosure.report_nm)

    candidate = score_candidate(
        disclosure=disclosure,
        classification=classification,
        facts={
            "target_business": "일반 제조업",
            "asset_ratio_pct": 2.0,
            "equity_ratio_pct": 3.0,
            "post_stake_pct": 4.0,
            "control_acquisition": False,
        },
    )

    assert candidate.band.value == "low"
    assert candidate.attention_score == 27


def test_small_plain_cb_is_not_mistaken_for_a_transformation() -> None:
    disclosure = _disclosure(
        "주요사항보고서(전환사채권 발행결정)",
        rcept_no="20251208000002",
    )
    classification = classify_report(disclosure.report_nm)

    candidate = score_candidate(
        disclosure=disclosure,
        classification=classification,
        facts={
            "dilution_ratio_pct": 3.0,
            "funds_operations": 1_000_000_000,
            "issue_method": "사모",
        },
    )

    assert candidate.band.value == "low"
    assert candidate.attention_score < 20
    assert candidate.financing_risk_score == 5


def test_supply_contract_after_acquisition_becomes_watch_candidate() -> None:
    disclosure = _disclosure("단일판매ㆍ공급계약체결", rcept_no="20251220000001")
    classification = classify_report(disclosure.report_nm)
    related = RelatedEvent(
        rcept_no="20251208000001",
        rcept_dt="20251208",
        category=EventCategory.ACQUISITION_CONTROL,
        subtype="equity_or_business_acquisition",
    )

    candidate = score_candidate(
        disclosure=disclosure,
        classification=classification,
        facts={},
        related_events=(related,),
    )

    assert candidate.band.value == "watch"
    assert "execution_confirmation" in candidate.cluster_tags


def test_shareholder_meeting_agenda_needs_business_purpose_evidence() -> None:
    disclosure = _disclosure("주주총회소집공고", rcept_no="20251221000001")
    classification = classify_report(disclosure.report_nm)

    without_evidence = score_candidate(
        disclosure=disclosure,
        classification=classification,
        facts={},
    )
    with_evidence = score_candidate(
        disclosure=disclosure,
        classification=classification,
        facts={"business_purpose_phrases": ["사업목적 추가 | AI 데이터센터 장비 제조"]},
    )

    assert without_evidence.band.value == "low"
    assert without_evidence.attention_score == 0
    assert with_evidence.band.value == "watch"
    assert with_evidence.attention_score == 35


def test_unconfirmed_meeting_agenda_does_not_confirm_later_supply_contract() -> None:
    disclosure = _disclosure("단일판매ㆍ공급계약체결", rcept_no="20251222000001")
    classification = classify_report(disclosure.report_nm)
    unrelated_agenda = RelatedEvent(
        rcept_no="20251221000001",
        rcept_dt="20251221",
        category=EventCategory.PORTFOLIO_PIVOT,
        subtype="shareholder_meeting_agenda",
        facts={},
    )

    candidate = score_candidate(
        disclosure=disclosure,
        classification=classification,
        facts={},
        related_events=(unrelated_agenda,),
    )

    assert candidate.band.value == "low"
    assert "pivot_confirmation" not in candidate.cluster_tags

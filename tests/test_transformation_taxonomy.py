from transformation_scanner.models import EventCategory
from transformation_scanner.taxonomy import classify_report


def test_classifies_corrected_acquisition_as_core_transformation() -> None:
    classification = classify_report(
        "[기재정정]주요사항보고서(타법인 주식 및 출자증권 양수결정)"
    )

    assert classification.category == EventCategory.ACQUISITION_CONTROL
    assert classification.structured_kind == "acquisition"
    assert classification.is_correction is True
    assert classification.base_attention == 25


def test_classifies_dilutive_financing_and_validation_separately() -> None:
    cb = classify_report("주요사항보고서(전환사채권 발행결정)")
    supply = classify_report("단일판매ㆍ공급계약체결")

    assert cb.category == EventCategory.FINANCING_DILUTION
    assert cb.structured_kind == "cb"
    assert supply.category == EventCategory.EXECUTION_VALIDATION
    assert supply.base_attention == 18


def test_shareholder_meeting_notice_requires_document_evidence() -> None:
    classification = classify_report("주주총회소집공고")

    assert classification.category == EventCategory.PORTFOLIO_PIVOT
    assert classification.subtype == "shareholder_meeting_agenda"
    assert classification.base_attention == 0
    assert classification.requires_document is True

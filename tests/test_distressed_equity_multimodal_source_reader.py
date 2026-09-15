from datetime import date

from distressed_equity.multimodal_source_reader import (
    build_multimodal_source_tasks,
    multimodal_source_manifest,
    multimodal_source_result_template,
)
from distressed_equity.sec_instruments import (
    FilingDocument,
    SecInstrumentPacket,
    sec_instrument_packet_to_dict,
)


def visual_document(name: str = "scan.pdf") -> FilingDocument:
    return FilingDocument(
        sequence=2,
        description="Scanned debt schedule",
        document=name,
        document_type="EX-4.1",
        size=50_000,
        url=f"https://www.sec.gov/Archives/example/{name}",
        source_accession="0000000001-22-000001",
        filing_date=date(2022, 12, 1),
        filing_form="8-K",
    )


def packet_with_warning(warning: str) -> SecInstrumentPacket:
    return SecInstrumentPacket(
        ticker="TEST",
        company_name="Test Co",
        analysis_date=date(2022, 12, 31),
        documents=(visual_document(),),
        spans=(),
        candidates=(),
        warnings=(warning,),
    )


def test_visual_defer_builds_source_reading_task_not_bulk_ocr_contract():
    packet = packet_with_warning(
        "VISUAL_EXTRACTION_REQUIRED|accession=0000000001-22-000001|document=scan.pdf|"
        "document_type=EX-4.1|media_type=pdf|url=https://www.sec.gov/Archives/example/scan.pdf|"
        "reason=native_text_layer_unavailable_or_too_sparse"
    )
    tasks = build_multimodal_source_tasks(packet)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.media_type == "pdf"
    assert "investment/survival analysis" in task.objective
    assert any("Do not convert every row or cell into JSON" in item for item in task.guardrails)
    assert any("minimal deterministic-engine fields" in item for item in task.questions)

    template = multimodal_source_result_template(task)
    assert set(template) >= {
        "findings",
        "disconfirming_or_ambiguous_evidence",
        "engine_patches",
        "unresolved",
    }
    assert "instruments" not in template
    assert "debt_schedule" not in template
    assert template["engine_patches"] == []


def test_serialized_source_packet_contains_multimodal_manifest():
    packet = packet_with_warning(
        "VISUAL_EXTRACTION_REQUIRED|accession=0000000001-22-000001|document=scan.pdf|"
        "document_type=EX-4.1|media_type=pdf|url=https://www.sec.gov/Archives/example/scan.pdf|"
        "reason=native_text_layer_unavailable_or_too_sparse"
    )
    payload = sec_instrument_packet_to_dict(packet)
    manifest = payload["multimodal_source_reading"]
    assert manifest["strategy"] == "read_source_first_structure_on_demand"
    assert manifest["task_count"] == 1
    result_template = manifest["tasks"][0]["result_template"]
    assert result_template["findings"] == []
    assert result_template["engine_patches"] == []
    assert "instruments" not in result_template


def test_native_pdf_success_does_not_create_visual_reader_task():
    packet = SecInstrumentPacket(
        ticker="TEST",
        company_name="Test Co",
        analysis_date=date(2022, 12, 31),
        documents=(visual_document(),),
        spans=(),
        candidates=(),
        warnings=(
            "PDF_NATIVE_TEXT_EXTRACTED|accession=0000000001-22-000001|document=scan.pdf|"
            "document_type=EX-4.1|url=https://www.sec.gov/Archives/example/scan.pdf|"
            "page_count=1|fragment_count=12|character_count=200|candidate_count=2",
        ),
    )
    assert build_multimodal_source_tasks(packet) == ()
    assert multimodal_source_manifest(packet)["task_count"] == 0


def test_duplicate_visual_warnings_create_one_task_per_source():
    warning = (
        "VISUAL_EXTRACTION_REQUIRED|accession=0000000001-22-000001|document=scan.pdf|"
        "document_type=EX-4.1|media_type=pdf|url=https://www.sec.gov/Archives/example/scan.pdf|"
        "reason=native_text_layer_unavailable_or_too_sparse"
    )
    packet = SecInstrumentPacket(
        ticker="TEST",
        company_name="Test Co",
        analysis_date=date(2022, 12, 31),
        documents=(visual_document(),),
        spans=(),
        candidates=(),
        warnings=(warning, warning),
    )
    assert len(build_multimodal_source_tasks(packet)) == 1

from __future__ import annotations

from datetime import date

import pytest

from distressed_equity import sec_instruments
from distressed_equity import contract_graph, named_entity_contracts, source_graph
from distressed_equity.document_debt_extraction import NativePdfTextPayload, VisualExtractionRequiredError
from distressed_equity.sec_instruments import FilingDocument


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(items: list[tuple[float, float, str]]) -> bytes:
    commands = []
    for x, y, text in items:
        commands.extend(["BT", "/F1 10 Tf", f"1 0 0 1 {x:g} {y:g} Tm", f"({_escape(text)}) Tj", "ET"])
    stream = ("\n".join(commands) + ("\n" if commands else "")).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    parts = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(sum(len(part) for part in parts))
        parts.append(f"{number} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
    xref = sum(len(part) for part in parts)
    parts.append(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    parts.append(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        parts.append(f"{offset:010d} 00000 n \n".encode("ascii"))
    parts.append(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return b"".join(parts)


class Response:
    def __init__(self, content: bytes):
        self.content = content
        self.text = "should-not-be-used-for-pdf"

    def raise_for_status(self):
        return None


class Session:
    def __init__(self, url: str, content: bytes):
        self.url = url
        self.content = content

    def get(self, url, headers=None, timeout=None):
        assert url == self.url
        return Response(self.content)


class Client:
    user_agent = "Research test@example.com"

    def __init__(self, url: str, content: bytes):
        self.session = Session(url, content)

    def _throttle(self):
        return None


def pdf_document() -> FilingDocument:
    return FilingDocument(
        sequence=2,
        description="Indenture relating to Senior Secured Notes",
        document="notes.pdf",
        document_type="EX-4.1",
        size=50_000,
        url="https://www.sec.gov/Archives/example/notes.pdf",
        source_accession="0000000001-22-000001",
        filing_date=date(2022, 12, 1),
        filing_form="8-K",
    )


def table_pdf() -> bytes:
    return make_pdf(
        [
            (40, 750, "Instrument"),
            (300, 750, "Principal ($ millions)"),
            (390, 750, "Maturity"),
            (470, 750, "Coupon"),
            (530, 750, "CUSIP"),
            (40, 725, "5.25% Senior Secured Notes due 2028"),
            (300, 725, "500"),
            (390, 725, "2028-06-15"),
            (470, 725, "5.25%"),
            (530, 725, "123456789"),
        ]
    )


def test_graph_contract_and_named_entity_modules_bind_same_document_aware_helpers():
    assert source_graph._get_text is sec_instruments._get_text
    assert contract_graph._get_text is sec_instruments._get_text
    assert named_entity_contracts._get_text is sec_instruments._get_text
    assert source_graph.extract_source_candidates is sec_instruments.extract_source_candidates
    assert contract_graph.extract_source_candidates is sec_instruments.extract_source_candidates
    assert named_entity_contracts.extract_source_candidates is sec_instruments.extract_source_candidates


def test_source_graph_bound_helpers_preserve_native_pdf_layout_candidates():
    document = pdf_document()
    client = Client(document.url, table_pdf())
    payload = source_graph._get_text(client, document.url)
    assert isinstance(payload, NativePdfTextPayload)
    spans, candidates = source_graph.extract_source_candidates(payload, document, max_candidates=10)
    rows = [candidate for candidate in candidates if candidate.cluster_status == "pdf_layout_row"]
    assert len(rows) == 1
    row = rows[0]
    assert row.snapshot_template["principal"] == 500_000_000
    assert row.snapshot_template["maturity_date"] == "2028-06-15"
    assert row.snapshot_template["coupon_pct"] == 5.25
    assert row.snapshot_template["cusip"] == "123456789"
    assert spans and row.source_span_ids[0] in {span.span_id for span in spans}


def test_scanned_like_pdf_raises_machine_readable_visual_requirement_on_graph_path():
    document = pdf_document()
    client = Client(document.url, make_pdf([]))
    with pytest.raises(VisualExtractionRequiredError) as exc_info:
        source_graph._get_text(client, document.url)
    assert str(exc_info.value).startswith("VISUAL_EXTRACTION_REQUIRED|")
    assert "media_type=pdf" in str(exc_info.value)

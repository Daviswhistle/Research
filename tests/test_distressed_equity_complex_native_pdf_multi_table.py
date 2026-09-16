from datetime import date

from distressed_equity import sec_instruments
from distressed_equity.native_pdf_debt_extraction import extract_native_pdf_debt_candidates
from distressed_equity.sec_instruments import FilingDocument


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf(items):
    commands = []
    for x, y, text in items:
        commands += ["BT", "/F1 10 Tf", f"1 0 0 1 {x:g} {y:g} Tm", f"({_escape(text)}) Tj", "ET"]
    stream = ("\n".join(commands) + "\n").encode("latin-1")
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
        parts.append(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = sum(len(part) for part in parts)
    parts.append(f"xref\n0 {len(objects) + 1}\n".encode())
    parts.append(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        parts.append(f"{offset:010d} 00000 n \n".encode())
    parts.append(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return b"".join(parts)


def _document():
    return FilingDocument(
        sequence=2, description="Debt Schedule", document="notes.pdf", document_type="EX-4.1",
        size=50_000, url="https://www.sec.gov/notes.pdf", source_accession="0000000001-22-000001",
        filing_date=date(2022, 12, 1), filing_form="8-K",
    )


def test_two_transposed_tables_on_one_pdf_page_reusing_same_marker_keep_local_definitions():
    result = extract_native_pdf_debt_candidates(
        sec_instruments,
        _pdf([
            (40, 750, "Term"), (200, 750, "5.25% Senior Notes due 2028 (1)"),
            (40, 725, "Principal ($ millions)"), (200, 725, "500"),
            (40, 700, "Maturity"), (200, 700, "2028-06-15"),
            (40, 675, "CUSIP"), (200, 675, "123456789"),
            (40, 650, "(1) First-note covenant footnote."),
            # A second, independent table resets numbering and reuses marker (1).
            (40, 600, "Term"), (200, 600, "6.00% Senior Notes due 2030 (1)"),
            (40, 575, "Principal ($ millions)"), (200, 575, "400"),
            (40, 550, "Maturity"), (200, 550, "2030-06-15"),
            (40, 525, "CUSIP"), (200, 525, "987654321"),
            (40, 500, "(1) Second-note collateral footnote."),
        ]),
        _document(),
        max_candidates=20,
    )
    columns = [candidate for candidate in result.candidates if candidate.cluster_status == "pdf_layout_column"]
    assert len(columns) == 2
    by_cusip = {candidate.snapshot_template["cusip"]: candidate for candidate in columns}
    assert by_cusip["123456789"].snapshot_template["principal"] == 500_000_000
    assert by_cusip["987654321"].snapshot_template["principal"] == 400_000_000

    span_text = {span.span_id: span.text for span in result.spans}
    first_evidence = "\n".join(span_text[ref] for ref in by_cusip["123456789"].source_span_ids)
    second_evidence = "\n".join(span_text[ref] for ref in by_cusip["987654321"].source_span_ids)
    assert "First-note covenant footnote" in first_evidence
    assert "Second-note collateral footnote" not in first_evidence
    assert "Second-note collateral footnote" in second_evidence
    assert "First-note covenant footnote" not in second_evidence
    assert any(":table1" in ref for ref in by_cusip["123456789"].source_span_ids)
    assert any(":table2" in ref for ref in by_cusip["987654321"].source_span_ids)

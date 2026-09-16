from datetime import date

from distressed_equity import sec_instruments
from distressed_equity.native_pdf_debt_extraction import extract_native_pdf_debt_candidates
from distressed_equity.sec_instruments import FilingDocument


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(items: list[tuple[float, float, str]]) -> bytes:
    commands: list[str] = []
    for x, y, text in items:
        commands.extend([
            "BT", "/F1 10 Tf", f"1 0 0 1 {x:g} {y:g} Tm",
            f"({_pdf_escape(text)}) Tj", "ET",
        ])
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
        parts.append(f"{number} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
    xref = sum(len(part) for part in parts)
    parts.append(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    parts.append(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        parts.append(f"{offset:010d} 00000 n \n".encode("ascii"))
    parts.append(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    return b"".join(parts)


def document():
    return FilingDocument(
        sequence=2,
        description="Debt Schedule",
        document="notes.pdf",
        document_type="EX-4.1",
        size=50_000,
        url="https://www.sec.gov/Archives/example/notes.pdf",
        source_accession="0000000001-22-000001",
        filing_date=date(2022, 12, 1),
        filing_form="8-K",
    )


def extract(items):
    return extract_native_pdf_debt_candidates(
        sec_instruments,
        make_pdf(items),
        document(),
        max_candidates=20,
    )


def test_transposed_native_pdf_maps_instruments_by_x_column():
    result = extract([
        (40, 750, "Term"),
        (200, 750, "5.25% Senior Secured Notes due 2028"),
        (410, 750, "6.00% Senior Notes due 2030"),
        (40, 725, "Principal ($ millions)"), (200, 725, "500"), (410, 725, "400"),
        (40, 700, "Maturity"), (200, 700, "2028-06-15"), (410, 700, "2030-06-15"),
        (40, 675, "Coupon"), (200, 675, "5.25%"), (410, 675, "6.00%"),
        (40, 650, "CUSIP"), (200, 650, "123456789"), (410, 650, "987654321"),
    ])
    columns = [candidate for candidate in result.candidates if candidate.cluster_status == "pdf_layout_column"]
    assert len(columns) == 2
    by_cusip = {candidate.snapshot_template["cusip"]: candidate for candidate in columns}
    assert by_cusip["123456789"].snapshot_template["principal"] == 500_000_000
    assert by_cusip["123456789"].snapshot_template["maturity_date"] == "2028-06-15"
    assert by_cusip["123456789"].snapshot_template["coupon_pct"] == 5.25
    assert by_cusip["987654321"].snapshot_template["principal"] == 400_000_000
    assert "transposed_native_pdf_table" in by_cusip["123456789"].cluster_basis


def test_native_pdf_blank_name_continuation_row_is_one_candidate():
    result = extract([
        (40, 750, "Instrument"), (300, 750, "Principal ($ millions)"),
        (390, 750, "Maturity"), (500, 750, "CUSIP"),
        (40, 725, "Term Loan B"), (300, 725, "800"), (500, 725, "111111111"),
        (390, 700, "2028-12-15"),
    ])
    groups = [candidate for candidate in result.candidates if candidate.cluster_status == "pdf_layout_row_group"]
    assert len(groups) == 1
    candidate = groups[0]
    assert candidate.snapshot_template["principal"] == 800_000_000
    assert candidate.snapshot_template["maturity_date"] == "2028-12-15"
    assert candidate.snapshot_template["cusip"] == "111111111"
    assert len(candidate.source_span_ids) == 2


def test_native_pdf_conflicting_continuation_values_remain_unresolved():
    result = extract([
        (40, 750, "Instrument"), (300, 750, "Principal ($ millions)"), (420, 750, "Maturity"),
        (40, 725, "Term Loan B"), (300, 725, "500"), (420, 725, "2028"),
        (300, 700, "400"),
    ])
    groups = [candidate for candidate in result.candidates if candidate.cluster_status == "pdf_layout_row_group"]
    assert len(groups) == 1
    candidate = groups[0]
    assert candidate.snapshot_template["principal"] is None
    assert candidate.snapshot_template["maturity_year"] == 2028
    assert any("conflicting high-confidence principal" in warning for warning in candidate.warnings)


def test_native_pdf_footnote_marker_is_stripped_from_amount_and_linked():
    result = extract([
        (40, 750, "Instrument"), (300, 750, "Principal ($ millions)"), (430, 750, "Maturity"),
        (40, 725, "5.25% Senior Notes due 2028 (1)"), (300, 725, "500 (1)"), (430, 725, "2028"),
        (40, 680, "(1) Amount is face value before unamortized discount."),
    ])
    rows = [candidate for candidate in result.candidates if candidate.cluster_status == "pdf_layout_row"]
    assert len(rows) == 1
    candidate = rows[0]
    assert candidate.snapshot_template["principal"] == 500_000_000
    assert any("linked footnote marker" in warning for warning in candidate.warnings)
    assert any("PDF PAGE FOOTNOTE" in span.text for span in result.spans if span.span_id in candidate.source_span_ids)


def test_native_pdf_total_row_is_not_an_instrument_candidate():
    result = extract([
        (40, 750, "Instrument"), (300, 750, "Principal ($ millions)"), (430, 750, "Maturity"),
        (40, 725, "5.25% Senior Notes due 2028"), (300, 725, "500"), (430, 725, "2028"),
        (40, 700, "Total debt"), (300, 700, "500"),
    ])
    rows = [candidate for candidate in result.candidates if candidate.cluster_status.startswith("pdf_layout")]
    assert len(rows) == 1
    assert rows[0].snapshot_template["name"] == "5.25% Senior Notes due 2028"

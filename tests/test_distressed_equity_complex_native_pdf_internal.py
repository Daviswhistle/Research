from datetime import date

from distressed_equity import native_pdf_debt_extraction as native
from distressed_equity import sec_instruments
from distressed_equity.complex_native_pdf_debt_extraction import (
    _complex_extract,
    _field_row,
    _page_footnotes,
    _row_oriented_page,
    _transposed_header,
    _transposed_page,
)
from distressed_equity.sec_instruments import FilingDocument


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf(items):
    commands = []
    for x, y, text in items:
        commands += ["BT", "/F1 10 Tf", f"1 0 0 1 {x:g} {y:g} Tm", f"({_pdf_escape(text)}) Tj", "ET"]
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


def _geometry(items):
    pdf = _pdf(items)
    fragments = native.extract_pdf_text_fragments(pdf)
    rows = native.layout_rows(fragments)
    return pdf, fragments, rows


def _rows_debug(rows):
    return [
        {
            "y": row.y,
            "text": row.text,
            "fragments": [(fragment.x, fragment.text) for fragment in row.fragments],
            "anchors": native._header_anchors(row),
            "field_row": _field_row(row),
        }
        for row in rows
    ]


def test_internal_row_oriented_stages():
    pdf, fragments, rows = _geometry([
        (40, 750, "Instrument"), (300, 750, "Principal ($ millions)"),
        (390, 750, "Maturity"), (500, 750, "CUSIP"),
        (40, 725, "Term Loan B"), (300, 725, "800"), (500, 725, "111111111"),
        (390, 700, "2028-12-15"),
    ])
    assert len(rows) == 3, _rows_debug(rows)
    anchors = native._header_anchors(rows[0])
    assert [item[2] for item in anchors] == ["name", "principal", "maturity", "cusip"], _rows_debug(rows)
    assert native._column_values(rows[1], anchors) == ("Term Loan B", "800", "", "111111111"), _rows_debug(rows)
    assert native._column_values(rows[2], anchors) == ("", "", "2028-12-15", ""), _rows_debug(rows)
    direct = _row_oriented_page(
        native,
        sec_instruments,
        list(rows),
        _document(),
        _page_footnotes(native, rows, _document()).get(1, {}),
        max_candidates=20,
    )
    assert direct is not None and len(direct[1]) == 1, _rows_debug(rows)
    assert direct[1][0].cluster_status == "pdf_layout_row_group", direct[1]
    result = _complex_extract(native, sec_instruments, pdf, _document(), max_candidates=20)
    assert [candidate.cluster_status for candidate in result.candidates] == ["pdf_layout_row_group"], result.candidates


def test_internal_transposed_stages():
    pdf, fragments, rows = _geometry([
        (40, 750, "Term"), (200, 750, "5.25% Senior Secured Notes due 2028"),
        (410, 750, "6.00% Senior Notes due 2030"),
        (40, 725, "Principal ($ millions)"), (200, 725, "500"), (410, 725, "400"),
        (40, 700, "Maturity"), (200, 700, "2028-06-15"), (410, 700, "2030-06-15"),
        (40, 675, "Coupon"), (200, 675, "5.25%"), (410, 675, "6.00%"),
    ])
    assert len(rows) == 4, _rows_debug(rows)
    field_rows = [(index, _field_row(row)) for index, row in enumerate(rows) if _field_row(row)]
    assert [value[1][1] for value in field_rows] == ["principal", "maturity", "coupon"], _rows_debug(rows)
    header = _transposed_header(native, list(rows), 1, sec_instruments)
    assert header is not None and len(header[1]) == 2, _rows_debug(rows)
    direct = _transposed_page(
        native,
        sec_instruments,
        list(rows),
        _document(),
        _page_footnotes(native, rows, _document()).get(1, {}),
        max_candidates=20,
    )
    assert direct is not None and len(direct[1]) == 2, _rows_debug(rows)
    assert all(candidate.cluster_status == "pdf_layout_column" for candidate in direct[1])
    result = _complex_extract(native, sec_instruments, pdf, _document(), max_candidates=20)
    assert len([candidate for candidate in result.candidates if candidate.cluster_status == "pdf_layout_column"]) == 2, result.candidates

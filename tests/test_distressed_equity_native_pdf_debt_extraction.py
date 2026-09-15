from __future__ import annotations

from datetime import date

from distressed_equity.sec import SecFiling
from distressed_equity.sec_instruments import build_sec_instrument_packet, filing_index_url


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(items: list[tuple[float, float, str]]) -> bytes:
    commands: list[str] = []
    for x, y, text in items:
        commands.extend(
            [
                "BT",
                "/F1 10 Tf",
                f"1 0 0 1 {x:g} {y:g} Tm",
                f"({_pdf_escape(text)}) Tj",
                "ET",
            ]
        )
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
    parts.append(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    return b"".join(parts)


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
            (40, 700, "6.00% Senior Secured Notes due 2030"),
            (300, 700, "400"),
            (390, 700, "2030-06-15"),
            (470, 700, "6.00%"),
            (530, 700, "987654321"),
        ]
    )


def prose_pdf() -> bytes:
    return make_pdf(
        [
            (50, 750, "5.25% Senior Secured Notes due 2028"),
            (50, 730, "The Notes are issued in an aggregate principal amount of $500 million."),
            (50, 710, "The Notes bear CUSIP No. 123456789 and are senior secured obligations."),
        ]
    )


class Response:
    def __init__(self, *, text: str = "", content: bytes = b""):
        self.text = text
        self.content = content

    def raise_for_status(self):
        return None


class PdfSession:
    def __init__(self, index_url: str, pdf_url: str, pdf_bytes: bytes):
        self.index_url = index_url
        self.pdf_url = pdf_url
        self.pdf_bytes = pdf_bytes
        self.requested: list[str] = []

    def get(self, url, headers=None, timeout=None):
        self.requested.append(url)
        if url == self.index_url:
            return Response(
                text="""
                <html><body><table>
                  <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
                  <tr><td>2</td><td>Indenture relating to Senior Secured Notes</td><td><a href="notes.pdf">notes.pdf</a></td><td>EX-4.1</td><td>50000</td></tr>
                </table></body></html>
                """
            )
        if url == self.pdf_url:
            return Response(content=self.pdf_bytes)
        raise AssertionError(url)


class PdfClient:
    user_agent = "Research test@example.com"

    def __init__(self, index_url: str, pdf_url: str, pdf_bytes: bytes):
        self.session = PdfSession(index_url, pdf_url, pdf_bytes)

    def _throttle(self):
        return None


def filing() -> SecFiling:
    return SecFiling(
        cik="0000000001",
        accession_number="0000000001-22-000001",
        filing_date=date(2022, 12, 1),
        form="8-K",
        primary_document="main.htm",
    )


def packet_for(pdf_bytes: bytes):
    item = filing()
    index_url = filing_index_url(item)
    pdf_url = index_url.rsplit("/", 1)[0] + "/notes.pdf"
    client = PdfClient(index_url, pdf_url, pdf_bytes)
    packet = build_sec_instrument_packet(
        client,
        ticker="TEST",
        company_name="Test Co",
        analysis_date=date(2022, 12, 31),
        filings=(item,),
        filing_limit=5,
    )
    return packet, client, pdf_url


def test_native_pdf_table_preserves_coordinate_row_column_pairing():
    packet, client, pdf_url = packet_for(table_pdf())
    rows = [candidate for candidate in packet.candidates if candidate.cluster_status == "pdf_layout_row"]
    assert len(rows) == 2
    by_cusip = {candidate.snapshot_template["cusip"]: candidate for candidate in rows}
    assert by_cusip["123456789"].snapshot_template["principal"] == 500_000_000
    assert by_cusip["123456789"].snapshot_template["maturity_date"] == "2028-06-15"
    assert by_cusip["123456789"].snapshot_template["coupon_pct"] == 5.25
    assert by_cusip["987654321"].snapshot_template["principal"] == 400_000_000
    assert by_cusip["987654321"].snapshot_template["maturity_date"] == "2030-06-15"
    assert by_cusip["987654321"].snapshot_template["coupon_pct"] == 6.0
    assert all("coordinate_mapped_columns" in candidate.cluster_basis for candidate in rows)
    assert all(":p1:r" in candidate.source_span_ids[0] for candidate in rows)
    assert not any(
        warning.startswith("VISUAL_EXTRACTION_REQUIRED|") and "document=notes.pdf" in warning
        for warning in packet.warnings
    )
    assert any(
        warning.startswith("PDF_NATIVE_TEXT_EXTRACTED|") and "candidate_count=2" in warning
        for warning in packet.warnings
    )
    assert pdf_url in client.session.requested


def test_native_pdf_prose_preserves_page_level_source_refs():
    packet, _, _ = packet_for(prose_pdf())
    candidates = [candidate for candidate in packet.candidates if candidate.cluster_status.startswith("pdf_native_prose")]
    assert candidates
    candidate = next(
        candidate
        for candidate in candidates
        if candidate.snapshot_template.get("principal") == 500_000_000
    )
    assert candidate.snapshot_template["cusip"] == "123456789"
    assert all(":p1:s" in source_ref for source_ref in candidate.source_span_ids)
    assert "same_pdf_page" in candidate.cluster_basis


def test_blank_scanned_like_pdf_retains_visual_deferral_and_adds_native_unavailable_status():
    packet, _, _ = packet_for(make_pdf([]))
    assert not packet.candidates
    assert any(
        warning.startswith("VISUAL_EXTRACTION_REQUIRED|")
        and "media_type=pdf" in warning
        and "document=notes.pdf" in warning
        for warning in packet.warnings
    )
    assert any(
        warning.startswith("PDF_NATIVE_TEXT_UNAVAILABLE|")
        and "document=notes.pdf" in warning
        for warning in packet.warnings
    )
    assert not any(warning.startswith("PDF_NATIVE_TEXT_EXTRACTED|") for warning in packet.warnings)

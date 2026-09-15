from datetime import date

from distressed_equity.sec import SecFiling
from distressed_equity.sec_instruments import (
    FilingDocument,
    build_sec_instrument_packet,
    extract_source_candidates,
    filing_index_url,
)


def document(*, description="Debt Schedule", name="ex41.htm", document_type="EX-4.1"):
    return FilingDocument(
        sequence=2,
        description=description,
        document=name,
        document_type=document_type,
        size=50_000,
        url=f"https://www.sec.gov/Archives/example/{name}",
        source_accession="0000000001-22-000001",
        filing_date=date(2022, 12, 1),
        filing_form="8-K",
    )


def test_html_debt_table_preserves_row_column_pairing_and_units():
    html = """
    <html><body>
      <p>The following table summarizes outstanding debt.</p>
      <table>
        <tr>
          <th>Instrument</th>
          <th>Principal Amount ($ in millions)</th>
          <th>Maturity</th>
          <th>Coupon</th>
          <th>CUSIP</th>
        </tr>
        <tr>
          <td>5.25% Senior Secured Notes due 2028</td>
          <td>500</td>
          <td>June 15, 2028</td>
          <td>5.25%</td>
          <td>123456789</td>
        </tr>
        <tr>
          <td>6.00% Senior Secured Notes due 2030</td>
          <td>400</td>
          <td>June 15, 2030</td>
          <td>6.00%</td>
          <td>987654321</td>
        </tr>
      </table>
    </body></html>
    """
    spans, candidates = extract_source_candidates(html, document(), max_candidates=10)
    rows = [candidate for candidate in candidates if candidate.cluster_status == "table_row"]
    assert len(rows) == 2

    by_cusip = {candidate.snapshot_template["cusip"]: candidate for candidate in rows}
    assert by_cusip["123456789"].snapshot_template["principal"] == 500_000_000
    assert by_cusip["123456789"].snapshot_template["maturity_date"] == "2028-06-15"
    assert by_cusip["123456789"].snapshot_template["coupon_pct"] == 5.25
    assert by_cusip["987654321"].snapshot_template["principal"] == 400_000_000
    assert by_cusip["987654321"].snapshot_template["maturity_date"] == "2030-06-15"
    assert by_cusip["987654321"].snapshot_template["coupon_pct"] == 6.0

    table_span_ids = {span.span_id for span in spans if ":t1:r" in span.span_id}
    assert table_span_ids == {row.source_span_ids[0] for row in rows}
    assert all("same_html_table_row" in row.cluster_basis for row in rows)
    assert not any(
        candidate.snapshot_template.get("cusip") == "123456789"
        and candidate.snapshot_template.get("principal") == 400_000_000
        for candidate in rows
    )


def test_multirow_headers_and_rowspan_keep_same_instrument_columns_aligned():
    html = """
    <table>
      <tr>
        <th rowspan="2">Instrument</th>
        <th colspan="2">Terms</th>
        <th rowspan="2">CUSIP</th>
      </tr>
      <tr>
        <th>Principal ($ millions)</th>
        <th>Maturity</th>
      </tr>
      <tr>
        <td>5.25% Senior Secured Notes due 2028</td>
        <td>500</td>
        <td>2028-06-15</td>
        <td>123456789</td>
      </tr>
    </table>
    """
    _, candidates = extract_source_candidates(html, document(), max_candidates=10)
    rows = [candidate for candidate in candidates if candidate.cluster_status == "table_row"]
    assert len(rows) == 1
    row = rows[0]
    assert row.snapshot_template["principal"] == 500_000_000
    assert row.snapshot_template["maturity_date"] == "2028-06-15"
    assert row.snapshot_template["cusip"] == "123456789"


def test_table_body_is_not_flattened_back_into_prose_candidates():
    html = """
    <html><body>
      <p>Debt instruments are summarized below.</p>
      <table>
        <tr><th>Instrument</th><th>Principal ($ millions)</th><th>CUSIP</th></tr>
        <tr><td>5.25% Senior Secured Notes due 2028</td><td>500</td><td>123456789</td></tr>
        <tr><td>6.00% Senior Secured Notes due 2030</td><td>400</td><td>987654321</td></tr>
      </table>
    </body></html>
    """
    _, candidates = extract_source_candidates(html, document(), max_candidates=10)
    table_rows = [candidate for candidate in candidates if candidate.cluster_status == "table_row"]
    prose = [candidate for candidate in candidates if candidate.cluster_status != "table_row"]
    assert len(table_rows) == 2
    assert not any(candidate.snapshot_template.get("cusip") in {"123456789", "987654321"} for candidate in prose)


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class VisualSession:
    def __init__(self, index_url):
        self.index_url = index_url
        self.requested = []

    def get(self, url, headers=None, timeout=None):
        self.requested.append(url)
        if url == self.index_url:
            return FakeResponse(
                """
                <html><body><table>
                  <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
                  <tr><td>1</td><td>FORM 8-K</td><td><a href="main.htm">main.htm</a></td><td>8-K</td><td>1000</td></tr>
                  <tr><td>2</td><td>Indenture for Senior Notes</td><td><a href="notes.pdf">notes.pdf</a></td><td>EX-4.1</td><td>50000</td></tr>
                </table></body></html>
                """
            )
        raise AssertionError(f"fixture does not provide PDF bytes: {url}")


class VisualClient:
    user_agent = "Research test@example.com"

    def __init__(self, index_url):
        self.session = VisualSession(index_url)

    def _throttle(self):
        return None


def test_pdf_without_usable_binary_fixture_remains_deferred_after_native_probe():
    filing = SecFiling(
        cik="0000000001",
        accession_number="0000000001-22-000001",
        filing_date=date(2022, 12, 1),
        form="8-K",
        primary_document="main.htm",
    )
    index_url = filing_index_url(filing)
    client = VisualClient(index_url)
    packet = build_sec_instrument_packet(
        client,
        ticker="TEST",
        company_name="Test Co",
        analysis_date=date(2022, 12, 31),
        filings=(filing,),
        filing_limit=5,
    )
    assert any(document.document == "notes.pdf" for document in packet.documents)
    assert not packet.candidates
    assert any(
        warning.startswith("VISUAL_EXTRACTION_REQUIRED|")
        and "media_type=pdf" in warning
        and "document=notes.pdf" in warning
        for warning in packet.warnings
    )
    assert any(
        warning.startswith("PDF_NATIVE_TEXT_EXTRACTION_FAILED|")
        and "document=notes.pdf" in warning
        for warning in packet.warnings
    )
    pdf_url = index_url.rsplit("/", 1)[0] + "/notes.pdf"
    assert client.session.requested == [index_url, pdf_url]


class ImageHeavySession:
    def __init__(self, index_url, exhibit_url):
        self.index_url = index_url
        self.exhibit_url = exhibit_url

    def get(self, url, headers=None, timeout=None):
        if url == self.index_url:
            return FakeResponse(
                """
                <html><body><table>
                  <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
                  <tr><td>2</td><td>Indenture for Senior Notes</td><td><a href="scan.htm">scan.htm</a></td><td>EX-4.1</td><td>50000</td></tr>
                </table></body></html>
                """
            )
        if url == self.exhibit_url:
            return FakeResponse('<html><body><img src="page1.png"><img src="page2.png"></body></html>')
        raise AssertionError(url)


class ImageHeavyClient:
    user_agent = "Research test@example.com"

    def __init__(self, index_url, exhibit_url):
        self.session = ImageHeavySession(index_url, exhibit_url)

    def _throttle(self):
        return None


def test_image_heavy_html_is_deferred_when_visible_text_is_too_sparse():
    filing = SecFiling(
        cik="0000000001",
        accession_number="0000000001-22-000001",
        filing_date=date(2022, 12, 1),
        form="8-K",
        primary_document="main.htm",
    )
    index_url = filing_index_url(filing)
    exhibit_url = index_url.rsplit("/", 1)[0] + "/scan.htm"
    packet = build_sec_instrument_packet(
        ImageHeavyClient(index_url, exhibit_url),
        ticker="TEST",
        company_name="Test Co",
        analysis_date=date(2022, 12, 31),
        filings=(filing,),
    )
    assert not packet.candidates
    assert any(
        warning.startswith("VISUAL_EXTRACTION_REQUIRED|")
        and "media_type=image_heavy_html" in warning
        for warning in packet.warnings
    )

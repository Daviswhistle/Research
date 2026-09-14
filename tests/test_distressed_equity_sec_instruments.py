from datetime import date

from distressed_equity.sec import SecFiling
from distressed_equity.sec_instruments import (
    build_sec_instrument_packet,
    extract_source_candidates,
    filing_index_url,
    parse_filing_documents,
    select_debt_documents,
)


INDEX_HTML = """
<html><body>
<table class="tableFile" summary="Document Format Files">
<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
<tr><td>1</td><td>FORM 8-K</td><td><a href="main.htm">main.htm</a></td><td>8-K</td><td>10000</td></tr>
<tr><td>2</td><td>Indenture relating to 5.25% Senior Secured Notes due 2028</td><td><a href="ex41.htm">ex41.htm</a></td><td>EX-4.1</td><td>50000</td></tr>
<tr><td>3</td><td>Credit Agreement Amendment</td><td><a href="ex101.htm">ex101.htm</a></td><td>EX-10.1</td><td>80000</td></tr>
<tr><td>4</td><td>Certification</td><td><a href="ex311.htm">ex311.htm</a></td><td>EX-31.1</td><td>9000</td></tr>
</table>
</body></html>
"""

NOTES_HTML = """
<html><body>
<p>This Indenture governs the 5.25% Senior Secured Notes due 2028.</p>
<p>The Notes are issued in an aggregate principal amount of $500 million and mature on June 15, 2028.</p>
<p>The Notes bear CUSIP No. 123456789 and are senior secured obligations.</p>
</body></html>
"""

CREDIT_HTML = """
<html><body>
<p>This amendment modifies the revolving credit facility.</p>
<p>Aggregate commitments of $300 million bear interest at SOFR plus 2.50%.</p>
</body></html>
"""


def filing(filing_date=date(2022, 12, 1)):
    return SecFiling(
        cik="0000000001",
        accession_number="0000000001-22-000001",
        filing_date=filing_date,
        form="8-K",
        primary_document="main.htm",
    )


def test_filing_index_parser_and_debt_document_selection():
    item = filing()
    documents = parse_filing_documents(INDEX_HTML, item)
    assert [document.document_type for document in documents] == ["8-K", "EX-4.1", "EX-10.1", "EX-31.1"]
    selected = select_debt_documents(documents)
    assert [document.document_type for document in selected] == ["EX-4.1", "EX-10.1"]
    assert selected[0].url.endswith("/ex41.htm")


def test_explicit_notes_terms_produce_source_backed_high_confidence_fields():
    item = filing()
    documents = parse_filing_documents(INDEX_HTML, item)
    notes = next(document for document in documents if document.document_type == "EX-4.1")
    spans, candidates = extract_source_candidates(NOTES_HTML, notes)
    assert spans
    assert candidates
    candidate = candidates[0]
    proposals = {proposal.field: proposal for proposal in candidate.proposals}
    assert proposals["coupon_pct"].value == 5.25
    assert proposals["maturity_year"].value == 2028
    assert proposals["principal"].value == 500_000_000
    assert proposals["cusip"].value == "123456789"
    assert candidate.snapshot_template["coupon_pct"] == 5.25
    assert candidate.snapshot_template["maturity_year"] == 2028
    assert candidate.snapshot_template["principal"] == 500_000_000
    assert candidate.source_span_ids[0] == proposals["principal"].source_span_id


def test_credit_facility_extracts_commitment_and_sofr_spread():
    item = filing()
    documents = parse_filing_documents(INDEX_HTML, item)
    credit = next(document for document in documents if document.document_type == "EX-10.1")
    _, candidates = extract_source_candidates(CREDIT_HTML, credit)
    assert candidates
    proposals = {proposal.field: proposal.value for proposal in candidates[0].proposals}
    assert proposals["commitment"] == 300_000_000
    assert proposals["benchmark"] == "SOFR"
    assert proposals["spread_bps"] == 250.0


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class FakeSession:
    def get(self, url, headers=None, timeout=None):
        if url.endswith("-index.html"):
            return FakeResponse(INDEX_HTML)
        if url.endswith("ex41.htm"):
            return FakeResponse(NOTES_HTML)
        if url.endswith("ex101.htm"):
            return FakeResponse(CREDIT_HTML)
        raise AssertionError(url)


class FakeClient:
    user_agent = "Research test@example.com"
    session = FakeSession()

    def _throttle(self):
        return None


def test_packet_rejects_future_filings_and_retains_source_urls():
    historical = filing(date(2022, 12, 1))
    future = SecFiling(
        cik="0000000001",
        accession_number="0000000001-23-000002",
        filing_date=date(2023, 1, 2),
        form="8-K",
        primary_document="main.htm",
    )
    packet = build_sec_instrument_packet(
        FakeClient(),
        ticker="TEST",
        company_name="Test Co",
        analysis_date=date(2022, 12, 31),
        filings=(future, historical),
        filing_limit=10,
    )
    assert packet.documents
    assert all(document.source_accession == historical.accession_number for document in packet.documents)
    assert all(span.published_on <= date(2022, 12, 31) for span in packet.spans)
    assert any(candidate.document_url.endswith("ex41.htm") for candidate in packet.candidates)

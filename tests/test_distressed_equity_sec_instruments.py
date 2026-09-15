from datetime import date

from distressed_equity.sec import SecFiling
from distressed_equity.sec_instruments import (
    FilingDocument,
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


def source_document(*, description, document_type="EX-4.1", document="ex41.htm"):
    return FilingDocument(
        sequence=2,
        description=description,
        document=document,
        document_type=document_type,
        size=50_000,
        url=f"https://www.sec.gov/Archives/example/{document}",
        source_accession="0000000001-22-000001",
        filing_date=date(2022, 12, 1),
        filing_form="8-K",
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
    assert proposals["principal"].source_span_id in candidate.source_span_ids


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


def test_distant_note_terms_cluster_under_specific_document_identity():
    notes = source_document(
        description="Indenture relating to 5.25% Senior Secured Notes due 2028"
    )
    filler = "".join(f"<p>Boilerplate covenant section {index}.</p>" for index in range(12))
    html = (
        "<html><body>"
        "<p>This Indenture governs the 5.25% Senior Secured Notes due 2028.</p>"
        + filler
        + "<p>The senior secured notes were issued in an aggregate principal amount of $500 million.</p>"
        + filler
        + "<p>The senior secured notes mature on June 15, 2028.</p>"
        + filler
        + "<p>The senior secured notes bear CUSIP No. 123456789.</p>"
        + "</body></html>"
    )
    spans, candidates = extract_source_candidates(html, notes)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.cluster_status == "linked_spans"
    assert len(candidate.source_span_ids) >= 3
    assert set(candidate.source_span_ids) <= {span.span_id for span in spans}
    assert candidate.snapshot_template["principal"] == 500_000_000
    assert candidate.snapshot_template["maturity_date"] == "2028-06-15"
    assert candidate.snapshot_template["cusip"] == "123456789"
    assert "specific_document_note_identity" in candidate.cluster_basis


def test_two_note_series_in_one_generic_exhibit_do_not_cross_cluster():
    document = source_document(description="Indenture", document="multi-notes.htm")
    filler = "".join(f"<p>General indenture provision {index}.</p>" for index in range(6))
    html = (
        "<html><body>"
        "<p>5.25% Senior Secured Notes due 2028. CUSIP No. 123456789.</p>"
        + filler
        + "<p>5.25% Senior Secured Notes due 2028 have aggregate principal amount of $500 million.</p>"
        + filler
        + "<p>6.00% Senior Secured Notes due 2030. CUSIP No. 987654321.</p>"
        + filler
        + "<p>6.00% Senior Secured Notes due 2030 have aggregate principal amount of $400 million.</p>"
        + "</body></html>"
    )
    _, candidates = extract_source_candidates(html, document)
    assert candidates
    cusip_sets = [
        {proposal.value for proposal in candidate.proposals if proposal.field == "cusip"}
        for candidate in candidates
    ]
    assert {"123456789"} in cusip_sets
    assert {"987654321"} in cusip_sets
    assert all(values != {"123456789", "987654321"} for values in cusip_sets)
    assert not any(
        {proposal.value for proposal in candidate.proposals if proposal.field == "coupon_pct"}
        == {5.25, 6.0}
        for candidate in candidates
    )


def test_conflicting_explicit_cusips_hard_split_even_with_specific_document_title():
    document = source_document(
        description="Indenture relating to 5.25% Senior Secured Notes due 2028",
        document="conflict.htm",
    )
    filler = "".join(f"<p>Supplemental provision {index}.</p>" for index in range(8))
    html = (
        "<html><body>"
        "<p>This Indenture governs the 5.25% Senior Secured Notes due 2028.</p>"
        + filler
        + "<p>The senior secured notes bear CUSIP No. 123456789.</p>"
        + filler
        + "<p>The senior secured notes bear CUSIP No. 987654321.</p>"
        + "</body></html>"
    )
    _, candidates = extract_source_candidates(html, document)
    assert len(candidates) >= 2
    assert all(
        len({proposal.value for proposal in candidate.proposals if proposal.field == "cusip"}) <= 1
        for candidate in candidates
    )


def test_generic_credit_agreement_does_not_merge_revolver_and_term_loan():
    document = source_document(
        description="Credit Agreement",
        document_type="EX-10.1",
        document="credit.htm",
    )
    filler = "".join(f"<p>General credit agreement provision {index}.</p>" for index in range(8))
    html = (
        "<html><body>"
        "<p>The revolving credit facility has aggregate commitments of $300 million.</p>"
        + filler
        + "<p>The term loan facility has a principal amount of $450 million.</p>"
        + "</body></html>"
    )
    _, candidates = extract_source_candidates(html, document)
    types = {candidate.snapshot_template["instrument_type"] for candidate in candidates}
    assert "revolving_credit_facility" in types
    assert "term_loan" in types
    assert not any(
        {proposal.value for proposal in candidate.proposals if proposal.field == "instrument_type"}
        >= {"revolving_credit_facility", "term_loan"}
        for candidate in candidates
    )


def test_standalone_cusip_paragraph_is_retained_as_cluster_evidence():
    document = source_document(
        description="Indenture relating to 5.25% Senior Secured Notes due 2028",
        document="standalone-id.htm",
    )
    filler = "".join(f"<p>Unrelated boilerplate text {index}.</p>" for index in range(10))
    html = (
        "<html><body>"
        "<p>This Indenture governs the 5.25% Senior Secured Notes due 2028.</p>"
        + filler
        + "<p>CUSIP No. 123456789.</p>"
        + "</body></html>"
    )
    _, candidates = extract_source_candidates(html, document)
    assert any(
        proposal.field == "cusip" and proposal.value == "123456789"
        for candidate in candidates
        for proposal in candidate.proposals
    )


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

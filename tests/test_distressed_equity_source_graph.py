from datetime import date

from distressed_equity.sec import SecFiling
from distressed_equity.sec_instruments import SecInstrumentPacket, filing_index_url
from distressed_equity.source_graph import (
    expand_instrument_packet_with_references,
    extract_source_references,
    resolve_source_document_graph,
)


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, mapping):
        self.mapping = mapping

    def get(self, url, **kwargs):
        if url not in self.mapping:
            raise AssertionError(f"unexpected URL: {url}")
        return FakeResponse(self.mapping[url])


class FakeClient:
    user_agent = "Research test@example.com"

    def __init__(self, filings, mapping):
        self._filings = tuple(filings)
        self.session = FakeSession(mapping)

    def _throttle(self):
        return None

    def filings_as_of(self, cik, analysis_date, forms=()):
        allowed = set(forms)
        return tuple(
            item
            for item in self._filings
            if item.filing_date <= analysis_date and (not allowed or item.form in allowed)
        )


def filing(accession, filed, form, primary):
    return SecFiling(
        cik="0000000001",
        accession_number=accession,
        filing_date=filed,
        form=form,
        primary_document=primary,
        report_date=filed,
    )


def target_index_html():
    return """
    <html><body><table>
      <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
      <tr><td>1</td><td>Current Report</td><td><a href="old8k.htm">old8k.htm</a></td><td>8-K</td><td>1000</td></tr>
      <tr><td>2</td><td>Credit Agreement</td><td><a href="ex10-1.htm">ex10-1.htm</a></td><td>EX-10.1</td><td>5000</td></tr>
    </table></body></html>
    """


def test_extract_reference_from_form_date_and_exhibit():
    refs = extract_source_references(
        "The Credit Agreement is incorporated by reference to Exhibit 10.1 to the Company's Current Report on Form 8-K filed on May 15, 2020.",
        source_node_id="filing:current:primary",
        source_accession="0000000001-22-000001",
        source_published_on=date(2022, 11, 1),
    )
    assert len(refs) == 1
    assert refs[0].cited_form == "8-K"
    assert refs[0].cited_filing_date == date(2020, 5, 15)
    assert refs[0].cited_exhibit == "10.1"
    assert refs[0].confidence == "high"


def test_resolver_follows_old_8k_exhibit_and_expands_instrument_packet():
    current = filing("0000000001-22-000001", date(2022, 11, 1), "10-Q", "current.htm")
    old = filing("0000000001-20-000010", date(2020, 5, 15), "8-K", "old8k.htm")
    current_text = """
    <html><body><p>
    The Credit Agreement is incorporated by reference to Exhibit 10.1 to the Company's Current Report on Form 8-K filed on May 15, 2020.
    </p></body></html>
    """
    exhibit_text = """
    <html><body>
    <p>This Credit Agreement provides aggregate commitments of $500 million under a senior secured revolving credit facility.</p>
    <p>The facility matures on May 15, 2025 and bears interest at SOFR plus 2.00%.</p>
    </body></html>
    """
    exhibit_url = filing_index_url(old).rsplit("/", 1)[0] + "/ex10-1.htm"
    mapping = {
        current.archive_url: current_text,
        filing_index_url(old): target_index_html(),
        old.archive_url: "<html><body>No further references.</body></html>",
        exhibit_url: exhibit_text,
    }
    client = FakeClient((current, old), mapping)
    packet = SecInstrumentPacket(
        ticker="TEST",
        company_name="Test Co",
        analysis_date=date(2022, 12, 31),
        documents=(),
        spans=(),
        candidates=(),
        warnings=(),
    )

    expanded = expand_instrument_packet_with_references(
        client,
        packet=packet,
        cik="0000000001",
        seed_filings=(current,),
        max_depth=3,
    )
    resolved = [edge for edge in expanded.graph.edges if edge.status == "resolved"]
    assert len(resolved) == 1
    assert resolved[0].target_accession == old.accession_number
    assert any(doc.document_type == "EX-10.1" for doc in expanded.graph.resolved_documents)
    assert expanded.packet.candidates
    template = expanded.packet.candidates[0].snapshot_template
    assert template["commitment"] == 500_000_000


def test_resolver_blocks_target_that_postdates_source_even_when_within_analysis_cutoff():
    source = filing("0000000001-21-000001", date(2021, 1, 1), "10-Q", "source.htm")
    future = filing("0000000001-22-000999", date(2022, 1, 1), "8-K", "future.htm")
    source_text = (
        "Reference is made to Exhibit 10.1 to Form 8-K, accession number "
        "0000000001-22-000999."
    )
    client = FakeClient(
        (source, future),
        {source.archive_url: source_text},
    )
    graph = resolve_source_document_graph(
        client,
        cik="0000000001",
        analysis_date=date(2023, 1, 1),
        seed_filings=(source,),
    )
    assert any(edge.status == "blocked_temporal" for edge in graph.edges)


def test_resolver_blocks_explicit_reference_date_after_cutoff_before_matching():
    source = filing("0000000001-22-000001", date(2022, 1, 1), "10-Q", "source.htm")
    source_text = (
        "The agreement is incorporated by reference to Exhibit 10.1 to Form 8-K "
        "filed on January 5, 2023."
    )
    client = FakeClient((source,), {source.archive_url: source_text})
    graph = resolve_source_document_graph(
        client,
        cik="0000000001",
        analysis_date=date(2022, 12, 31),
        seed_filings=(source,),
    )
    assert any(edge.status == "blocked_cutoff" for edge in graph.edges)

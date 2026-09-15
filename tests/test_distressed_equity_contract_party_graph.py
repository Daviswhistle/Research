from datetime import date

from distressed_equity.contract_graph import enhance_expanded_packet_with_contract_identity
from distressed_equity.sec import SecFiling
from distressed_equity.sec_instruments import SecInstrumentPacket, filing_index_url
from distressed_equity.source_graph import expand_instrument_packet_with_references


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


def index_html(primary, exhibit):
    return f"""
    <html><body><table>
      <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
      <tr><td>1</td><td>Current Report</td><td><a href="{primary}">{primary}</a></td><td>8-K</td><td>1000</td></tr>
      <tr><td>2</td><td>Credit Agreement</td><td><a href="{exhibit}">{exhibit}</a></td><td>EX-10.1</td><td>5000</td></tr>
    </table></body></html>
    """


def test_graph_keeps_same_date_facilities_separate_by_borrower():
    current = filing("0000000001-22-000001", date(2022, 11, 1), "10-Q", "current.htm")
    old_a = filing("0000000001-19-000010", date(2019, 5, 6), "8-K", "old-a.htm")
    old_b = filing("0000000001-19-000011", date(2019, 5, 7), "8-K", "old-b.htm")
    base_a = filing_index_url(old_a).rsplit("/", 1)[0]
    base_b = filing_index_url(old_b).rsplit("/", 1)[0]
    exhibit_a = base_a + "/ex10-1a.htm"
    exhibit_b = base_b + "/ex10-1b.htm"
    mapping = {
        current.archive_url: (
            "<html><body>"
            "ABC Borrower LLC, as Borrower, is party to a Credit Agreement dated May 3, 2019. "
            "XYZ Borrower LLC, as Borrower, is party to a Credit Agreement dated May 3, 2019."
            "</body></html>"
        ),
        filing_index_url(old_a): index_html("old-a.htm", "ex10-1a.htm"),
        filing_index_url(old_b): index_html("old-b.htm", "ex10-1b.htm"),
        exhibit_a: (
            "<html><body>CREDIT AGREEMENT dated as of May 3, 2019 among "
            "ABC Borrower LLC, as Borrower.</body></html>"
        ),
        exhibit_b: (
            "<html><body>CREDIT AGREEMENT dated as of May 3, 2019 among "
            "XYZ Borrower LLC, as Borrower.</body></html>"
        ),
    }
    client = FakeClient((current, old_a, old_b), mapping)
    packet = SecInstrumentPacket(
        ticker="TEST",
        company_name="Test Co",
        analysis_date=date(2022, 12, 31),
        documents=(),
        spans=(),
        candidates=(),
        warnings=(),
    )
    base = expand_instrument_packet_with_references(
        client,
        packet=packet,
        cik="0000000001",
        seed_filings=(current,),
        max_depth=2,
    )
    enhanced = enhance_expanded_packet_with_contract_identity(
        client,
        expanded=base,
        cik="0000000001",
        seed_filings=(current,),
        max_depth=2,
    )
    resolved = [edge for edge in enhanced.graph.edges if edge.status == "resolved_by_contract_identity"]
    assert len(resolved) == 2
    assert {edge.target_accession for edge in resolved} == {old_a.accession_number, old_b.accession_number}

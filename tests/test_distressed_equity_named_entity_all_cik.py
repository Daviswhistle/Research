from datetime import date

from distressed_equity.named_entity_contracts import resolve_named_entity_contracts
from distressed_equity.sec import SecFiling
from distressed_equity.sec_cik_lookup import SEC_CIK_LOOKUP_URL
from distressed_equity.sec_instruments import SecInstrumentPacket, filing_index_url
from distressed_equity.source_graph import SourceGraphNode


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

    def __init__(self, target, mapping):
        self.target = target
        self.session = FakeSession(mapping)

    def _throttle(self):
        return None

    def ticker_map(self):
        return {}

    def submissions(self, cik):
        key = str(cik).zfill(10)
        if key == self.target.cik:
            return {"name": "Private Finance LLC", "formerNames": []}
        raise AssertionError(f"unexpected submissions CIK: {cik}")

    def filings_as_of(self, cik, analysis_date, forms=()):
        key = str(cik).zfill(10)
        allowed = set(forms)
        values = (self.target,) if key == self.target.cik else ()
        return tuple(
            item
            for item in values
            if item.filing_date <= analysis_date and (not allowed or item.form in allowed)
        )


def test_non_ticker_sec_filer_can_be_candidate_from_official_cumulative_cik_list():
    target = SecFiling(
        cik="0002222222",
        accession_number="0002222222-19-000003",
        filing_date=date(2019, 5, 10),
        form="8-K",
        primary_document="current.htm",
        report_date=date(2019, 5, 10),
    )
    source_url = "https://example.test/source.htm"
    base = filing_index_url(target).rsplit("/", 1)[0]
    exhibit_url = base + "/ex10-1.htm"
    mapping = {
        SEC_CIK_LOOKUP_URL: (
            "OTHER COMPANY INC.:0004444444:\n"
            "PRIVATE FINANCE LLC:0002222222:\n"
        ),
        source_url: (
            "Credit Agreement dated May 3, 2019 among "
            "Private Finance LLC, as Borrower."
        ),
        filing_index_url(target): """
            <html><body><table>
              <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
              <tr><td>1</td><td>Current Report</td><td><a href="current.htm">current.htm</a></td><td>8-K</td><td>1000</td></tr>
              <tr><td>2</td><td>Credit Agreement</td><td><a href="ex10-1.htm">ex10-1.htm</a></td><td>EX-10.1</td><td>5000</td></tr>
            </table></body></html>
        """,
        exhibit_url: (
            "Credit Agreement dated May 3, 2019 among "
            "Private Finance LLC, as Borrower. Aggregate commitments of $450 million."
        ),
    }
    client = FakeClient(target, mapping)
    source = SourceGraphNode(
        node_id="filing:1111111111-22-000001:primary",
        node_type="filing_primary",
        accession_number="1111111111-22-000001",
        filing_date=date(2022, 6, 30),
        form="10-Q",
        url=source_url,
        document_type="10-Q",
        description="primary filing document",
        depth=0,
    )
    packet = SecInstrumentPacket(
        ticker="ROOT",
        company_name="Root Co",
        analysis_date=date(2022, 12, 31),
        documents=(),
        spans=(),
        candidates=(),
        warnings=(),
    )

    result = resolve_named_entity_contracts(
        client,
        packet=packet,
        source_nodes=(source,),
        root_cik="1111111111",
    )

    resolution = result.graph.resolutions[0]
    assert resolution.status == "resolved_named_entity_contract"
    assert resolution.candidate_ciks == ("0002222222",)
    assert resolution.target_document_url == exhibit_url
    assert any(
        item.cik == "0002222222"
        and item.source_kind == "sec_cik_lookup_data_candidate_only"
        for item in result.graph.candidates
    )
    assert any(document.url == exhibit_url for document in result.packet.documents)

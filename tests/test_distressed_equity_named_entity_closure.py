from datetime import date

from distressed_equity.cross_cik import CrossCikExpandedPacket, CrossCikGraph
from distressed_equity.legal_name_alias import LegalNameAliasGraph
from distressed_equity.named_entity_closure import close_named_entity_cross_cik
from distressed_equity.named_entity_contracts import (
    NamedEntityContractExpansion,
    NamedEntityContractGraph,
    NamedEntityContractResolution,
)
from distressed_equity.sec import SecFiling
from distressed_equity.sec_instruments import FilingDocument, SecInstrumentPacket, filing_index_url


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

    def __init__(self, filing, mapping):
        self.filing = filing
        self.session = FakeSession(mapping)

    def _throttle(self):
        return None

    def filings_as_of(self, cik, analysis_date, forms=()):
        key = str(cik).zfill(10)
        if key != self.filing.cik or self.filing.filing_date > analysis_date:
            return ()
        if forms and self.filing.form not in set(forms):
            return ()
        return (self.filing,)


def test_named_entity_resolved_exhibit_restarts_explicit_cross_cik_closure():
    cutoff = date(2022, 12, 31)
    root_cik = "0001111111"
    named_cik = "0002222222"
    foreign_cik = "0003333333"

    named_url = (
        "https://www.sec.gov/Archives/edgar/data/2222222/"
        "000222222222000001/named-credit.htm"
    )
    named_document = FilingDocument(
        sequence=2,
        description="Named borrower credit agreement",
        document="named-credit.htm",
        document_type="EX-10.1",
        size=5000,
        url=named_url,
        source_accession="0002222222-22-000001",
        filing_date=date(2022, 6, 30),
        filing_form="8-K",
    )

    foreign_filing = SecFiling(
        cik=foreign_cik,
        accession_number="0003333333-22-000010",
        filing_date=date(2022, 5, 15),
        form="8-K",
        primary_document="foreign8k.htm",
        report_date=date(2022, 5, 15),
    )
    foreign_base = filing_index_url(foreign_filing).rsplit("/", 1)[0]
    foreign_exhibit = foreign_base + "/ex10-b.htm"
    explicit_url = (
        "https://www.sec.gov/Archives/edgar/data/3333333/"
        "000333333322000010/ex10-b.htm"
    )

    mapping = {
        named_url: (
            "<html><body>Reference is made to the agreement filed as exhibit at "
            f"{explicit_url}</body></html>"
        ),
        filing_index_url(foreign_filing): (
            '<html><body><table>'
            '<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>'
            '<tr><td>1</td><td>Primary</td><td><a href="foreign8k.htm">foreign8k.htm</a></td><td>8-K</td><td>1</td></tr>'
            '<tr><td>2</td><td>Credit Agreement</td><td><a href="ex10-b.htm">ex10-b.htm</a></td><td>EX-10.2</td><td>1</td></tr>'
            '</table></body></html>'
        ),
        foreign_exhibit: "<html><body>Aggregate commitments of $700 million.</body></html>",
    }
    client = FakeClient(foreign_filing, mapping)

    packet = SecInstrumentPacket(
        ticker="ROOT",
        company_name="Root Co",
        analysis_date=cutoff,
        documents=(named_document,),
        spans=(),
        candidates=(),
        warnings=(),
    )
    named_resolution = NamedEntityContractResolution(
        resolution_id="named-entity-resolution:1",
        source_node_id="filing:0001111111-22-000001:primary",
        source_cik=root_cik,
        source_accession="0001111111-22-000001",
        source_published_on=date(2022, 6, 30),
        source_depth=0,
        contract_kind="credit_agreement",
        execution_date=date(2019, 5, 3),
        party_role="borrower",
        party_name="Named Finance LLC",
        normalized_party_name="named finance llc",
        candidate_ciks=(named_cik,),
        confirmed_ciks=(named_cik,),
        status="resolved_named_entity_contract",
        reason="unique source-date SEC name confirmation plus unique contract identity match",
        target_cik=named_cik,
        target_accession=named_document.source_accession,
        target_document_url=named_url,
    )
    named_graph = NamedEntityContractGraph(
        analysis_date=cutoff,
        candidates=(),
        resolutions=(named_resolution,),
        resolved_documents=(named_document,),
        warnings=(),
    )
    named_expansion = NamedEntityContractExpansion(packet=packet, graph=named_graph)

    base_graph = CrossCikGraph(
        root_cik=root_cik,
        analysis_date=cutoff,
        references=(),
        resolutions=(),
        resolved_documents=(),
        entity_nodes=(),
        entity_roles=(),
        entity_relations=(),
        warnings=(),
    )
    cross_expanded = CrossCikExpandedPacket(packet=packet, cross_cik_graph=base_graph)
    legal_graphs = tuple(
        LegalNameAliasGraph(
            cik=cik,
            analysis_date=cutoff,
            canonical_name_as_of=None,
            records=(),
            transitions=(),
            warnings=(),
        )
        for cik in (root_cik, named_cik, foreign_cik)
    )

    closed = close_named_entity_cross_cik(
        client,
        cross_expanded=cross_expanded,
        named_expansion=named_expansion,
        legal_name_graphs=legal_graphs,
        max_depth=2,
        max_nodes=20,
    )

    resolved = [
        item
        for item in closed.cross_expanded.cross_cik_graph.resolutions
        if item.status == "resolved_cross_cik" and item.target_cik == foreign_cik
    ]
    assert len(resolved) == 1
    assert resolved[0].source_depth == 1
    assert resolved[0].target_depth == 2
    assert resolved[0].source_cik == named_cik
    assert resolved[0].target_document_url == foreign_exhibit
    assert any(
        document.url == foreign_exhibit
        for document in closed.cross_expanded.packet.documents
    )
    # The name-origin boundary remains separate provenance; it was not forged into
    # a resolved_cross_cik edge for the named CIK itself.
    assert not any(
        item.target_cik == named_cik
        for item in closed.cross_expanded.cross_cik_graph.resolutions
    )

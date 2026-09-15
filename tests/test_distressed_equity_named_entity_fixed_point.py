from datetime import date

from distressed_equity.cross_cik import CrossCikExpandedPacket, CrossCikGraph
from distressed_equity.legal_name_alias import LegalNameAliasGraph
from distressed_equity.named_entity_closure import close_named_entity_fixed_point
from distressed_equity.named_entity_contracts import (
    NamedEntityContractExpansion,
    NamedEntityContractGraph,
    NamedEntityContractResolution,
)
from distressed_equity.sec import SecFiling
from distressed_equity.sec_instruments import FilingDocument, SecInstrumentPacket, filing_index_url
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

    def __init__(self, *, filings_by_cik, submissions, mapping, ticker_map=None):
        self.filings_by_cik = filings_by_cik
        self._submissions = submissions
        self.session = FakeSession(mapping)
        self._ticker_map = ticker_map or {}

    def _throttle(self):
        return None

    def ticker_map(self):
        return self._ticker_map

    def submissions(self, cik):
        return self._submissions[str(cik).zfill(10)]

    def filings_as_of(self, cik, analysis_date, forms=()):
        key = str(cik).zfill(10)
        allowed = set(forms)
        return tuple(
            item
            for item in self.filings_by_cik.get(key, ())
            if item.filing_date <= analysis_date and (not allowed or item.form in allowed)
        )


def filing(cik, accession, filed, form="8-K", primary="current.htm"):
    return SecFiling(
        cik=str(cik).zfill(10),
        accession_number=accession,
        filing_date=filed,
        form=form,
        primary_document=primary,
        report_date=filed,
    )


def index_html(primary, exhibit, exhibit_type="EX-10.1"):
    return f"""
    <html><body><table>
      <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
      <tr><td>1</td><td>Current Report</td><td><a href="{primary}">{primary}</a></td><td>8-K</td><td>1000</td></tr>
      <tr><td>2</td><td>Credit Agreement</td><td><a href="{exhibit}">{exhibit}</a></td><td>{exhibit_type}</td><td>5000</td></tr>
    </table></body></html>
    """


def empty_legal_graph(cik, cutoff):
    return LegalNameAliasGraph(
        cik=str(cik).zfill(10),
        analysis_date=cutoff,
        canonical_name_as_of=None,
        records=(),
        transitions=(),
        warnings=(),
    )


def initial_named_expansion(*, cutoff, root_cik, named_cik, named_document, source_depth=0):
    packet = SecInstrumentPacket(
        ticker="ROOT",
        company_name="Root Co",
        analysis_date=cutoff,
        documents=(named_document,),
        spans=(),
        candidates=(),
        warnings=(),
    )
    resolution = NamedEntityContractResolution(
        resolution_id="named-entity-resolution:1",
        source_node_id="filing:0001111111-22-000001:primary",
        source_cik=root_cik,
        source_accession="0001111111-22-000001",
        source_published_on=date(2022, 6, 30),
        source_depth=source_depth,
        contract_kind="credit_agreement",
        execution_date=date(2020, 1, 1),
        party_role="borrower",
        party_name="Named A LLC",
        normalized_party_name="named a llc",
        candidate_ciks=(named_cik,),
        confirmed_ciks=(named_cik,),
        status="resolved_named_entity_contract",
        reason="unique source-date SEC name confirmation plus unique contract identity match",
        target_cik=named_cik,
        target_accession=named_document.source_accession,
        target_document_url=named_document.url,
    )
    graph = NamedEntityContractGraph(
        analysis_date=cutoff,
        candidates=(),
        resolutions=(resolution,),
        resolved_documents=(named_document,),
        warnings=(),
    )
    return NamedEntityContractExpansion(packet=packet, graph=graph)


def base_cross(packet, root_cik, cutoff):
    return CrossCikExpandedPacket(
        packet=packet,
        cross_cik_graph=CrossCikGraph(
            root_cik=root_cik,
            analysis_date=cutoff,
            references=(),
            resolutions=(),
            resolved_documents=(),
            entity_nodes=(),
            entity_roles=(),
            entity_relations=(),
            warnings=(),
        ),
    )


def test_fixed_point_resolves_name_only_after_new_explicit_hop():
    cutoff = date(2022, 12, 31)
    root_cik = "0001111111"
    named_a_cik = "0002222222"
    explicit_b_cik = "0003333333"
    named_c_cik = "0004444444"

    named_a_url = (
        "https://www.sec.gov/Archives/edgar/data/2222222/"
        "000222222222000001/named-a.htm"
    )
    named_a_document = FilingDocument(
        sequence=2,
        description="Named A credit agreement",
        document="named-a.htm",
        document_type="EX-10.1",
        size=5000,
        url=named_a_url,
        source_accession="0002222222-22-000001",
        filing_date=date(2022, 6, 30),
        filing_form="8-K",
    )

    b_filing = filing(
        explicit_b_cik,
        "0003333333-22-000010",
        date(2022, 5, 15),
        primary="b8k.htm",
    )
    b_base = filing_index_url(b_filing).rsplit("/", 1)[0]
    b_exhibit = b_base + "/b-credit.htm"
    b_explicit_url = (
        "https://www.sec.gov/Archives/edgar/data/3333333/"
        "000333333322000010/b-credit.htm"
    )

    c_filing = filing(
        named_c_cik,
        "0004444444-19-000003",
        date(2019, 5, 10),
        primary="c8k.htm",
    )
    c_base = filing_index_url(c_filing).rsplit("/", 1)[0]
    c_exhibit = c_base + "/c-credit.htm"

    mapping = {
        named_a_url: (
            "<html><body>Reference is made to the agreement filed as exhibit at "
            f"{b_explicit_url}</body></html>"
        ),
        filing_index_url(b_filing): index_html("b8k.htm", "b-credit.htm", "EX-10.2"),
        b_exhibit: (
            "<html><body>Credit Agreement dated May 3, 2019 among "
            "C Finance LLC, as Borrower.</body></html>"
        ),
        filing_index_url(c_filing): index_html("c8k.htm", "c-credit.htm"),
        c_exhibit: (
            "<html><body>C Finance LLC, as Borrower, is party to the "
            "CREDIT AGREEMENT dated as of May 3, 2019. Aggregate commitments "
            "of $425 million.</body></html>"
        ),
    }
    client = FakeClient(
        filings_by_cik={
            explicit_b_cik: (b_filing,),
            named_c_cik: (c_filing,),
        },
        submissions={
            named_c_cik: {"name": "C Finance LLC", "formerNames": []},
        },
        mapping=mapping,
        ticker_map={"CFIN": (named_c_cik, "C Finance LLC")},
    )
    initial = initial_named_expansion(
        cutoff=cutoff,
        root_cik=root_cik,
        named_cik=named_a_cik,
        named_document=named_a_document,
    )
    root_source = SourceGraphNode(
        node_id="filing:0001111111-22-000001:primary",
        node_type="filing_primary",
        accession_number="0001111111-22-000001",
        filing_date=date(2022, 6, 30),
        form="10-Q",
        url="https://example.test/root.htm",
        document_type="10-Q",
        description="root",
        depth=0,
    )
    legal_graphs = tuple(
        empty_legal_graph(cik, cutoff)
        for cik in (root_cik, named_a_cik, explicit_b_cik, named_c_cik)
    )

    closed = close_named_entity_fixed_point(
        client,
        cross_expanded=base_cross(initial.packet, root_cik, cutoff),
        named_expansion=initial,
        initial_source_nodes=(root_source,),
        legal_name_graphs=legal_graphs,
        max_depth=3,
        max_nodes=30,
    )

    named_resolved = [
        item
        for item in closed.named_expansion.graph.resolutions
        if item.status == "resolved_named_entity_contract"
    ]
    assert {item.target_cik for item in named_resolved} == {named_a_cik, named_c_cik}
    c_resolution = next(item for item in named_resolved if item.target_cik == named_c_cik)
    assert c_resolution.source_cik == explicit_b_cik
    assert c_resolution.source_depth == 2
    assert c_resolution.target_document_url == c_exhibit
    assert any(
        item.status == "resolved_cross_cik" and item.target_cik == explicit_b_cik
        for item in closed.cross_expanded.cross_cik_graph.resolutions
    )
    assert not any(
        item.status == "resolved_cross_cik" and item.target_cik == named_c_cik
        for item in closed.cross_expanded.cross_cik_graph.resolutions
    )
    assert any(document.url == c_exhibit for document in closed.cross_expanded.packet.documents)
    assert closed.iterations == 2


def test_fixed_point_does_not_chain_name_only_without_explicit_hop():
    cutoff = date(2022, 12, 31)
    root_cik = "0001111111"
    named_a_cik = "0002222222"
    named_c_cik = "0004444444"
    named_a_url = (
        "https://www.sec.gov/Archives/edgar/data/2222222/"
        "000222222222000001/named-a.htm"
    )
    named_a_document = FilingDocument(
        sequence=2,
        description="Named A credit agreement",
        document="named-a.htm",
        document_type="EX-10.1",
        size=5000,
        url=named_a_url,
        source_accession="0002222222-22-000001",
        filing_date=date(2022, 6, 30),
        filing_form="8-K",
    )
    mapping = {
        named_a_url: (
            "<html><body>Credit Agreement dated May 3, 2019 among "
            "C Finance LLC, as Borrower.</body></html>"
        )
    }
    client = FakeClient(
        filings_by_cik={},
        submissions={},
        mapping=mapping,
        ticker_map={"CFIN": (named_c_cik, "C Finance LLC")},
    )
    initial = initial_named_expansion(
        cutoff=cutoff,
        root_cik=root_cik,
        named_cik=named_a_cik,
        named_document=named_a_document,
    )
    root_source = SourceGraphNode(
        node_id="filing:0001111111-22-000001:primary",
        node_type="filing_primary",
        accession_number="0001111111-22-000001",
        filing_date=date(2022, 6, 30),
        form="10-Q",
        url="https://example.test/root.htm",
        document_type="10-Q",
        description="root",
        depth=0,
    )
    closed = close_named_entity_fixed_point(
        client,
        cross_expanded=base_cross(initial.packet, root_cik, cutoff),
        named_expansion=initial,
        initial_source_nodes=(root_source,),
        legal_name_graphs=(
            empty_legal_graph(root_cik, cutoff),
            empty_legal_graph(named_a_cik, cutoff),
        ),
        max_depth=3,
        max_nodes=20,
    )

    resolved = [
        item
        for item in closed.named_expansion.graph.resolutions
        if item.status == "resolved_named_entity_contract"
    ]
    assert [item.target_cik for item in resolved] == [named_a_cik]
    assert not closed.cross_expanded.cross_cik_graph.resolutions
    assert closed.iterations == 1

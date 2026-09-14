from datetime import date

from distressed_equity.cross_cik import (
    CrossCikExpandedPacket,
    CrossCikGraph,
    CrossCikReference,
    CrossCikResolution,
)
from distressed_equity.foreign_contract_graph import expand_foreign_contract_identities
from distressed_equity.legal_name_alias import build_legal_name_alias_graph_from_submissions
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

    def __init__(self, filings_by_cik, mapping):
        self.filings_by_cik = filings_by_cik
        self.session = FakeSession(mapping)

    def _throttle(self):
        return None

    def submissions(self, cik):
        key = str(cik).zfill(10)
        return {"name": f"CIK {key} Co", "formerNames": []}

    def filings_as_of(self, cik, analysis_date, forms=()):
        key = str(cik).zfill(10)
        allowed = set(forms)
        return tuple(
            filing
            for filing in self.filings_by_cik.get(key, ())
            if filing.filing_date <= analysis_date and (not allowed or filing.form in allowed)
        )


def filing(cik, accession, filed, form, primary):
    return SecFiling(
        cik=str(cik).zfill(10),
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


def foreign_alias_graph(cik="2222222222"):
    return build_legal_name_alias_graph_from_submissions(
        {
            "name": "Borrower New LLC",
            "formerNames": [
                {"name": "Borrower Old LLC", "from": "2010-01-01", "to": "2020-06-01"},
            ],
        },
        cik=cik,
        analysis_date=date(2022, 12, 31),
    )


def cross_expanded_fixture():
    root_cik = "1111111111"
    foreign_cik = "2222222222"
    entry_filing = filing(foreign_cik, "2222222222-22-000010", date(2022, 5, 15), "8-K", "entry8k.htm")
    entry = FilingDocument(
        sequence=2,
        description="Credit facility amendment discussion",
        document="ex10-entry.htm",
        document_type="EX-10.2",
        size=5000,
        url="https://www.sec.gov/Archives/edgar/data/2222222222/222222222222000010/ex10-entry.htm",
        source_accession=entry_filing.accession_number,
        filing_date=entry_filing.filing_date,
        filing_form=entry_filing.form,
    )
    reference = CrossCikReference(
        reference_id="root:xcik-1",
        source_node_id="filing:root:primary",
        source_cik=root_cik,
        source_accession="1111111111-22-000001",
        source_published_on=date(2022, 11, 1),
        target_cik=foreign_cik,
        reference_text="explicit foreign SEC archive locator",
        cited_accession=entry_filing.accession_number,
        cited_url=entry.url,
    )
    resolution = CrossCikResolution(
        resolution_id="xcik-resolution:1",
        reference_id=reference.reference_id,
        source_node_id=reference.source_node_id,
        source_cik=root_cik,
        target_cik=foreign_cik,
        status="resolved_cross_cik",
        reason="explicit target CIK and exact exhibit",
        target_accession=entry_filing.accession_number,
        target_document_url=entry.url,
    )
    graph = CrossCikGraph(
        root_cik=root_cik,
        analysis_date=date(2022, 12, 31),
        references=(reference,),
        resolutions=(resolution,),
        resolved_documents=(entry,),
        entity_nodes=(),
        entity_roles=(),
        entity_relations=(),
        warnings=(),
    )
    packet = SecInstrumentPacket(
        ticker="PARENT",
        company_name="Parent Co",
        analysis_date=date(2022, 12, 31),
        documents=(entry,),
        spans=(),
        candidates=(),
        warnings=(),
    )
    return CrossCikExpandedPacket(packet=packet, cross_cik_graph=graph), entry_filing, entry


def client_fixture(entry_filing, entry):
    old = filing("2222222222", "2222222222-19-000003", date(2019, 5, 10), "8-K", "old8k.htm")
    old_base = filing_index_url(old).rsplit("/", 1)[0]
    old_exhibit = old_base + "/ex10-1.htm"
    mapping = {
        entry.url: (
            "<html><body>Borrower New LLC, as Borrower, is party to the "
            "Credit Agreement dated May 3, 2019.</body></html>"
        ),
        filing_index_url(old): index_html("old8k.htm", "ex10-1.htm"),
        old_exhibit: (
            "<html><body>Credit Agreement dated May 3, 2019 among "
            "Borrower Old LLC, as Borrower. Aggregate commitments of $900 million "
            "under a revolving credit facility.</body></html>"
        ),
    }
    client = FakeClient({"2222222222": (entry_filing, old)}, mapping)
    return client, old_exhibit


def test_foreign_locatorless_contract_uses_foreign_cik_alias_graph():
    cross_expanded, entry_filing, entry = cross_expanded_fixture()
    client, old_exhibit = client_fixture(entry_filing, entry)
    result = expand_foreign_contract_identities(
        client,
        cross_expanded=cross_expanded,
        legal_name_graphs=(foreign_alias_graph(),),
        max_depth=3,
    )
    assert len(result.graphs) == 1
    graph = result.graphs[0]
    assert graph.target_cik == "2222222222"
    assert graph.cross_cik_hops == 1
    assert graph.local_depth_budget == 2
    assert any(edge.status == "resolved_by_contract_identity" for edge in graph.graph.edges)
    assert any(document.url == old_exhibit for document in result.cross_expanded.packet.documents)
    assert any(
        candidate.snapshot_template.get("commitment") == 900_000_000
        for candidate in result.cross_expanded.packet.candidates
    )


def test_root_aliases_cannot_bridge_a_foreign_contract_party():
    cross_expanded, entry_filing, entry = cross_expanded_fixture()
    client, old_exhibit = client_fixture(entry_filing, entry)
    root_alias = foreign_alias_graph(cik="1111111111")
    result = expand_foreign_contract_identities(
        client,
        cross_expanded=cross_expanded,
        legal_name_graphs=(root_alias,),
        max_depth=3,
    )
    assert len(result.graphs) == 1
    assert any(edge.status == "unresolved_contract_identity" for edge in result.graphs[0].graph.edges)
    assert not any(document.url == old_exhibit for document in result.cross_expanded.packet.documents)


def test_cross_cik_hop_consumes_global_depth_budget():
    cross_expanded, entry_filing, entry = cross_expanded_fixture()
    client, _ = client_fixture(entry_filing, entry)
    result = expand_foreign_contract_identities(
        client,
        cross_expanded=cross_expanded,
        legal_name_graphs=(foreign_alias_graph(),),
        max_depth=1,
    )
    assert result.graphs == ()
    assert any("exhaust max_depth=1" in warning for warning in result.warnings)


def test_locatorless_foreign_contract_can_expose_and_resolve_third_cik_once():
    cross_expanded, entry_filing, entry = cross_expanded_fixture()
    old = filing("2222222222", "2222222222-19-000003", date(2019, 5, 10), "8-K", "old8k.htm")
    old_base = filing_index_url(old).rsplit("/", 1)[0]
    old_exhibit = old_base + "/ex10-1.htm"

    third = filing("3333333333", "3333333333-19-000004", date(2019, 4, 30), "8-K", "third8k.htm")
    third_base = filing_index_url(third).rsplit("/", 1)[0]
    third_exhibit = third_base + "/ex10-b.htm"
    third_href = (
        "https://www.sec.gov/Archives/edgar/data/3333333333/"
        "333333333319000004/ex10-b.htm"
    )

    mapping = {
        entry.url: (
            "<html><body>Borrower New LLC, as Borrower, is party to the "
            "Credit Agreement dated May 3, 2019.</body></html>"
        ),
        filing_index_url(old): index_html("old8k.htm", "ex10-1.htm"),
        old_exhibit: (
            "<html><body>Credit Agreement dated May 3, 2019 among Borrower Old LLC, as Borrower. "
            f'<a href="{third_href}">Third-party guarantee</a>'
            f'<a href="{third_href}">Duplicate third-party guarantee</a>'
            "</body></html>"
        ),
        filing_index_url(third): index_html("third8k.htm", "ex10-b.htm"),
        third_exhibit: "<html><body>$250 million of senior notes mature in 2026.</body></html>",
    }
    client = FakeClient(
        {
            "2222222222": (entry_filing, old),
            "3333333333": (third,),
        },
        mapping,
    )
    result = expand_foreign_contract_identities(
        client,
        cross_expanded=cross_expanded,
        legal_name_graphs=(foreign_alias_graph(),),
        max_depth=4,
    )

    third_resolutions = [
        item for item in result.cross_expanded.cross_cik_graph.resolutions
        if item.status == "resolved_cross_cik" and item.target_cik == "3333333333"
    ]
    assert len(third_resolutions) == 1
    assert any(document.url == third_exhibit for document in result.cross_expanded.packet.documents)
    third_graphs = [item for item in result.graphs if item.target_cik == "3333333333"]
    assert len(third_graphs) == 1
    assert third_graphs[0].entry_global_depth == 3
    assert third_graphs[0].local_depth_budget == 1

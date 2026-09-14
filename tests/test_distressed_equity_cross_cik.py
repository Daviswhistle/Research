from datetime import date

from distressed_equity.cross_cik import (
    expand_packet_with_cross_cik,
    extract_cross_cik_references,
    extract_explicit_legal_entity_evidence,
    resolve_cross_cik_graph,
)
from distressed_equity.sec import SecFiling
from distressed_equity.sec_instruments import SecInstrumentPacket, filing_index_url
from distressed_equity.source_graph import ExpandedInstrumentPacket, SourceDocumentGraph, SourceGraphNode


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

    def filings_as_of(self, cik, analysis_date, forms=()):
        key = str(cik).zfill(10)
        allowed = set(forms)
        return tuple(
            item
            for item in self.filings_by_cik.get(key, ())
            if item.filing_date <= analysis_date and (not allowed or item.form in allowed)
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


def root_node(source, *, depth=0):
    return SourceGraphNode(
        node_id=f"filing:{source.accession_number}:primary",
        node_type="filing_primary",
        accession_number=source.accession_number,
        filing_date=source.filing_date,
        form=source.form,
        url=source.archive_url,
        document_type=source.form,
        description="primary filing document",
        depth=depth,
    )


def empty_expanded(source):
    graph = SourceDocumentGraph(
        cik=source.cik,
        analysis_date=date(2022, 12, 31),
        nodes=(root_node(source),),
        references=(),
        edges=(),
        resolved_documents=(),
        warnings=(),
    )
    packet = SecInstrumentPacket(
        ticker="PARENT",
        company_name="Parent Co",
        analysis_date=date(2022, 12, 31),
        documents=(),
        spans=(),
        candidates=(),
        warnings=(),
    )
    return ExpandedInstrumentPacket(packet=packet, graph=graph)


def test_extracts_foreign_cik_from_raw_sec_archives_href():
    raw = (
        '<a href="https://www.sec.gov/Archives/edgar/data/2222222222/'
        '000222222220000010/ex10-1.htm">Credit Agreement</a>'
    )
    refs = extract_cross_cik_references(
        raw,
        source_node_id="filing:root:primary",
        source_cik="1111111111",
        source_accession="0001111111-22-000001",
        source_published_on=date(2022, 11, 1),
    )
    assert len(refs) == 1
    assert refs[0].target_cik == "2222222222"
    assert refs[0].cited_accession == "0002222222-20-000010"


def test_name_only_reference_never_authorizes_cross_cik_search():
    refs = extract_cross_cik_references(
        "ABC Borrower LLC, as Borrower, is party to the Credit Agreement dated May 3, 2019.",
        source_node_id="filing:root:primary",
        source_cik="1111111111",
        source_accession="0001111111-22-000001",
        source_published_on=date(2022, 11, 1),
    )
    assert refs == ()


def test_cross_cik_resolver_follows_explicit_foreign_archives_link_and_expands_packet():
    source = filing("1111111111", "0001111111-22-000001", date(2022, 11, 1), "10-Q", "parent.htm")
    foreign = filing("2222222222", "0002222222-20-000010", date(2020, 5, 15), "8-K", "borrower8k.htm")
    base = filing_index_url(foreign).rsplit("/", 1)[0]
    exhibit_url = base + "/ex10-1.htm"
    source_html = (
        '<html><body>See <a href="https://www.sec.gov/Archives/edgar/data/2222222222/'
        '000222222220000010/ex10-1.htm">the borrower Credit Agreement</a>.</body></html>'
    )
    exhibit_html = (
        "<html><body><p>Credit Agreement dated May 3, 2020.</p>"
        "<p>Aggregate commitments of $700 million under a revolving credit facility.</p></body></html>"
    )
    mapping = {
        source.archive_url: source_html,
        filing_index_url(foreign): index_html("borrower8k.htm", "ex10-1.htm"),
        exhibit_url: exhibit_html,
        foreign.archive_url: "<html><body>No further references.</body></html>",
    }
    client = FakeClient({"2222222222": (foreign,)}, mapping)
    expanded = expand_packet_with_cross_cik(
        client,
        expanded=empty_expanded(source),
        root_cik=source.cik,
        max_depth=3,
    )
    resolved = [item for item in expanded.cross_cik_graph.resolutions if item.status == "resolved_cross_cik"]
    assert len(resolved) == 1
    assert resolved[0].target_cik == "2222222222"
    assert resolved[0].source_depth == 0
    assert resolved[0].target_depth == 1
    assert any(document.url == exhibit_url for document in expanded.cross_cik_graph.resolved_documents)
    assert any(candidate.snapshot_template.get("commitment") == 700_000_000 for candidate in expanded.packet.candidates)


def test_cross_cik_resolver_preserves_preexisting_source_depth():
    source = filing("1111111111", "0001111111-22-000001", date(2022, 11, 1), "10-Q", "parent.htm")
    foreign = filing("2222222222", "0002222222-20-000010", date(2020, 5, 15), "8-K", "borrower8k.htm")
    base = filing_index_url(foreign).rsplit("/", 1)[0]
    exhibit_url = base + "/ex10-1.htm"
    source_html = (
        '<a href="https://www.sec.gov/Archives/edgar/data/2222222222/'
        '000222222220000010/ex10-1.htm">foreign agreement</a>'
    )
    mapping = {
        source.archive_url: source_html,
        filing_index_url(foreign): index_html("borrower8k.htm", "ex10-1.htm"),
        exhibit_url: "<html><body>No further references.</body></html>",
    }
    client = FakeClient({"2222222222": (foreign,)}, mapping)
    graph = resolve_cross_cik_graph(
        client,
        root_cik=source.cik,
        analysis_date=date(2022, 12, 31),
        source_nodes=(root_node(source, depth=2),),
        max_depth=4,
    )
    resolved = [item for item in graph.resolutions if item.status == "resolved_cross_cik"]
    assert len(resolved) == 1
    assert resolved[0].source_depth == 2
    assert resolved[0].target_depth == 3


def test_nested_same_cik_reference_keeps_global_depth_before_next_foreign_hop():
    root = filing("1111111111", "0001111111-22-000001", date(2022, 11, 1), "10-Q", "parent.htm")
    foreign_a = filing("2222222222", "2222222222-22-000010", date(2022, 5, 15), "8-K", "entry8k.htm")
    old_a = filing("2222222222", "2222222222-19-000003", date(2019, 5, 10), "8-K", "old8k.htm")
    foreign_b = filing("3333333333", "3333333333-19-000004", date(2019, 4, 30), "8-K", "third8k.htm")

    a_base = filing_index_url(foreign_a).rsplit("/", 1)[0]
    a_entry = a_base + "/ex10-entry.htm"
    old_a_base = filing_index_url(old_a).rsplit("/", 1)[0]
    old_a_exhibit = old_a_base + "/ex10-1.htm"
    b_base = filing_index_url(foreign_b).rsplit("/", 1)[0]
    b_exhibit = b_base + "/ex10-b.htm"

    root_html = (
        '<a href="https://www.sec.gov/Archives/edgar/data/2222222222/'
        '222222222222000010/ex10-entry.htm">foreign A entry</a>'
    )
    a_entry_html = (
        "See Exhibit 10.1 to Form 8-K filed on 5/10/2019, accession 2222222222-19-000003."
    )
    old_a_html = (
        '<a href="https://www.sec.gov/Archives/edgar/data/3333333333/'
        '333333333319000004/ex10-b.htm">foreign B entry</a>'
    )
    mapping = {
        root.archive_url: root_html,
        filing_index_url(foreign_a): index_html("entry8k.htm", "ex10-entry.htm"),
        a_entry: a_entry_html,
        filing_index_url(old_a): index_html("old8k.htm", "ex10-1.htm"),
        old_a.archive_url: "<html><body>No references.</body></html>",
        old_a_exhibit: old_a_html,
        filing_index_url(foreign_b): index_html("third8k.htm", "ex10-b.htm"),
        b_exhibit: "<html><body>No further references.</body></html>",
    }
    client = FakeClient(
        {
            "2222222222": (foreign_a, old_a),
            "3333333333": (foreign_b,),
        },
        mapping,
    )
    graph = resolve_cross_cik_graph(
        client,
        root_cik=root.cik,
        analysis_date=date(2022, 12, 31),
        source_nodes=(root_node(root),),
        max_depth=4,
    )
    a_resolution = next(
        item for item in graph.resolutions
        if item.status == "resolved_cross_cik" and item.target_cik == "2222222222"
    )
    b_resolution = next(
        item for item in graph.resolutions
        if item.status == "resolved_cross_cik" and item.target_cik == "3333333333"
    )
    assert a_resolution.source_depth == 0
    assert a_resolution.target_depth == 1
    assert b_resolution.source_depth == 2
    assert b_resolution.target_depth == 3


def test_cross_cik_resolver_blocks_foreign_target_that_postdates_source():
    source = filing("1111111111", "0001111111-21-000001", date(2021, 1, 1), "10-Q", "parent.htm")
    future = filing("2222222222", "0002222222-22-000010", date(2022, 5, 15), "8-K", "future8k.htm")
    source_html = (
        '<a href="https://www.sec.gov/Archives/edgar/data/2222222222/'
        '000222222222000010/ex10-1.htm">future agreement</a>'
    )
    client = FakeClient(
        {"2222222222": (future,)},
        {source.archive_url: source_html},
    )
    graph = resolve_cross_cik_graph(
        client,
        root_cik=source.cik,
        analysis_date=date(2023, 1, 1),
        source_nodes=(root_node(source),),
    )
    blocked = [item for item in graph.resolutions if item.status == "blocked_temporal"]
    assert len(blocked) == 1
    assert blocked[0].source_depth == 0
    assert blocked[0].target_depth == 1


def test_explicit_cik_role_and_subsidiary_relation_are_preserved_as_source_evidence():
    text = (
        "ABC Borrower LLC (CIK No. 2222222222), as Borrower, is a wholly owned subsidiary of the Company."
    )
    nodes, roles, relations = extract_explicit_legal_entity_evidence(
        text,
        source_node_id="filing:root:primary",
        source_cik="1111111111",
        source_accession="0001111111-22-000001",
        source_published_on=date(2022, 11, 1),
    )
    assert {node.cik for node in nodes} == {"1111111111", "2222222222"}
    assert any(role.entity_id == "cik:2222222222" and role.role == "borrower" for role in roles)
    assert any(
        relation.from_entity_id == "cik:2222222222"
        and relation.to_entity_id == "cik:1111111111"
        and relation.relation_type == "wholly_owned_subsidiary_of"
        for relation in relations
    )

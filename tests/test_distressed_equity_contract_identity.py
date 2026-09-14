from datetime import date

from distressed_equity.contract_graph import enhance_expanded_packet_with_contract_identity
from distressed_equity.contract_identity import extract_contract_identities
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


def index_html(primary, exhibit, *, exhibit_description="Credit Agreement", exhibit_type="EX-10.1"):
    return f"""
    <html><body><table>
      <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
      <tr><td>1</td><td>Primary</td><td><a href="{primary}">{primary}</a></td><td>8-K</td><td>1000</td></tr>
      <tr><td>2</td><td>{exhibit_description}</td><td><a href="{exhibit}">{exhibit}</a></td><td>{exhibit_type}</td><td>5000</td></tr>
    </table></body></html>
    """


def empty_packet():
    return SecInstrumentPacket(
        ticker="TEST",
        company_name="Test Co",
        analysis_date=date(2022, 12, 31),
        documents=(),
        spans=(),
        candidates=(),
        warnings=(),
    )


def test_extracts_locatorless_contract_title_and_execution_date():
    identities = extract_contract_identities(
        "The Company remains party to the Amended and Restated Credit Agreement, dated as of May 3, 2019."
    )
    assert len(identities) == 1
    assert identities[0].kind == "credit_agreement"
    assert identities[0].execution_date == date(2019, 5, 3)


def test_locatorless_contract_identity_resolves_unique_historical_exhibit():
    current = filing("0000000001-22-000001", date(2022, 11, 1), "10-Q", "current.htm")
    old = filing("0000000001-19-000010", date(2019, 5, 6), "8-K", "old8k.htm")
    old_base = filing_index_url(old).rsplit("/", 1)[0]
    exhibit_url = old_base + "/ex10-1.htm"

    mapping = {
        current.archive_url: "<html><body>The Company is party to a Credit Agreement dated May 3, 2019.</body></html>",
        filing_index_url(old): index_html("old8k.htm", "ex10-1.htm"),
        exhibit_url: """
            <html><body>
            <p>CREDIT AGREEMENT dated as of May 3, 2019.</p>
            <p>This Credit Agreement provides aggregate commitments of $600 million under a revolving credit facility.</p>
            </body></html>
        """,
    }
    client = FakeClient((current, old), mapping)
    base = expand_instrument_packet_with_references(
        client,
        packet=empty_packet(),
        cik="0000000001",
        seed_filings=(current,),
        max_depth=3,
    )
    assert not base.graph.edges

    enhanced = enhance_expanded_packet_with_contract_identity(
        client,
        expanded=base,
        cik="0000000001",
        seed_filings=(current,),
        max_depth=3,
    )
    resolved = [edge for edge in enhanced.graph.edges if edge.status == "resolved_by_contract_identity"]
    assert len(resolved) == 1
    assert resolved[0].target_accession == old.accession_number
    assert any(document.url == exhibit_url for document in enhanced.graph.resolved_documents)
    assert any(candidate.snapshot_template.get("commitment") == 600_000_000 for candidate in enhanced.packet.candidates)


def test_locatorless_contract_identity_refuses_ambiguous_exhibits():
    current = filing("0000000001-22-000001", date(2022, 11, 1), "10-Q", "current.htm")
    old_a = filing("0000000001-19-000010", date(2019, 5, 6), "8-K", "old-a.htm")
    old_b = filing("0000000001-19-000011", date(2019, 5, 7), "8-K", "old-b.htm")
    base_a = filing_index_url(old_a).rsplit("/", 1)[0]
    base_b = filing_index_url(old_b).rsplit("/", 1)[0]
    mapping = {
        current.archive_url: "<html><body>The Company is party to a Credit Agreement dated May 3, 2019.</body></html>",
        filing_index_url(old_a): index_html("old-a.htm", "ex10-1a.htm"),
        filing_index_url(old_b): index_html("old-b.htm", "ex10-1b.htm"),
        base_a + "/ex10-1a.htm": "<html><body>Credit Agreement dated May 3, 2019.</body></html>",
        base_b + "/ex10-1b.htm": "<html><body>Credit Agreement dated May 3, 2019.</body></html>",
    }
    client = FakeClient((current, old_a, old_b), mapping)
    base = expand_instrument_packet_with_references(
        client,
        packet=empty_packet(),
        cik="0000000001",
        seed_filings=(current,),
        max_depth=3,
    )
    enhanced = enhance_expanded_packet_with_contract_identity(
        client,
        expanded=base,
        cik="0000000001",
        seed_filings=(current,),
        max_depth=3,
    )
    ambiguous = [edge for edge in enhanced.graph.edges if edge.status == "ambiguous_contract_identity"]
    assert len(ambiguous) == 1
    assert "2 SEC exhibits matched" in ambiguous[0].reason
    assert not [edge for edge in enhanced.graph.edges if edge.status == "resolved_by_contract_identity"]


def test_contract_identity_search_does_not_use_filing_after_source_document():
    source = filing("0000000001-19-000001", date(2019, 5, 4), "10-Q", "source.htm")
    later = filing("0000000001-19-000010", date(2019, 5, 6), "8-K", "later.htm")
    mapping = {
        source.archive_url: "<html><body>Credit Agreement dated May 3, 2019.</body></html>",
    }
    client = FakeClient((source, later), mapping)
    base = expand_instrument_packet_with_references(
        client,
        packet=empty_packet(),
        cik="0000000001",
        seed_filings=(source,),
        max_depth=3,
    )
    enhanced = enhance_expanded_packet_with_contract_identity(
        client,
        expanded=base,
        cik="0000000001",
        seed_filings=(source,),
        max_depth=3,
    )
    unresolved = [edge for edge in enhanced.graph.edges if edge.status == "unresolved_contract_identity"]
    assert len(unresolved) == 1
    assert not enhanced.graph.resolved_documents

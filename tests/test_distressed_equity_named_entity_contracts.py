from datetime import date

from distressed_equity.named_entity_contracts import resolve_named_entity_contracts
from distressed_equity.sec import SecFiling
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

    def __init__(self, *, ticker_map, submissions, filings_by_cik, mapping):
        self._ticker_map = ticker_map
        self._submissions = submissions
        self.filings_by_cik = filings_by_cik
        self.session = FakeSession(mapping)

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


def index_html(primary="current.htm", exhibit="ex10-1.htm"):
    return f"""
    <html><body><table>
      <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
      <tr><td>1</td><td>Current Report</td><td><a href="{primary}">{primary}</a></td><td>8-K</td><td>1000</td></tr>
      <tr><td>2</td><td>Credit Agreement</td><td><a href="{exhibit}">{exhibit}</a></td><td>EX-10.1</td><td>5000</td></tr>
    </table></body></html>
    """


def source_node(text_url="https://example.test/source.htm", depth=0):
    return SourceGraphNode(
        node_id="filing:1111111111-22-000001:primary",
        node_type="filing_primary",
        accession_number="1111111111-22-000001",
        filing_date=date(2022, 6, 30),
        form="10-Q",
        url=text_url,
        document_type="10-Q",
        description="primary filing document",
        depth=depth,
    )


def empty_packet():
    return SecInstrumentPacket(
        ticker="ROOT",
        company_name="Root Co",
        analysis_date=date(2022, 12, 31),
        documents=(),
        spans=(),
        candidates=(),
        warnings=(),
    )


def resolved_client(*, duplicate=False, candidate_title="Target Finance LLC", future_name=False, wrong_party=False):
    source_url = "https://example.test/source.htm"
    source_text = (
        "Credit Agreement dated May 3, 2019 among Target Finance LLC, as Borrower."
    )
    target = filing("2222222222", "2222222222-19-000003", date(2019, 5, 10))
    target_base = filing_index_url(target).rsplit("/", 1)[0]
    target_exhibit = target_base + "/ex10-1.htm"
    borrower = "Wrong Finance LLC" if wrong_party else "Target Finance LLC"
    exhibit_text = (
        f"Credit Agreement dated May 3, 2019 among {borrower}, as Borrower. "
        "Aggregate commitments of $600 million under a revolving credit facility."
    )

    ticker_map = {"TGT": ("2222222222", candidate_title)}
    submissions = {
        "2222222222": (
            {
                "name": "Target Finance LLC",
                "formerNames": [
                    {"name": "Legacy Finance LLC", "from": "2010-01-01", "to": "2023-01-01"},
                ],
            }
            if future_name
            else {"name": "Target Finance LLC", "formerNames": []}
        )
    }
    filings_by_cik = {"2222222222": (target,)}
    mapping = {
        source_url: source_text,
        filing_index_url(target): index_html(),
        target_exhibit: exhibit_text,
    }

    second_exhibit = None
    if duplicate:
        second = filing("3333333333", "3333333333-19-000004", date(2019, 5, 11))
        second_base = filing_index_url(second).rsplit("/", 1)[0]
        second_exhibit = second_base + "/ex10-1.htm"
        ticker_map["TGT2"] = ("3333333333", "Target Finance LLC")
        submissions["3333333333"] = {"name": "Target Finance LLC", "formerNames": []}
        filings_by_cik["3333333333"] = (second,)
        mapping[filing_index_url(second)] = index_html()
        mapping[second_exhibit] = exhibit_text

    client = FakeClient(
        ticker_map=ticker_map,
        submissions=submissions,
        filings_by_cik=filings_by_cik,
        mapping=mapping,
    )
    return client, target_exhibit, second_exhibit


def test_exact_registry_name_plus_source_date_name_plus_unique_contract_resolves():
    client, target_exhibit, _ = resolved_client()
    result = resolve_named_entity_contracts(
        client,
        packet=empty_packet(),
        source_nodes=(source_node(),),
        root_cik="1111111111",
    )
    assert len(result.graph.resolutions) == 1
    resolution = result.graph.resolutions[0]
    assert resolution.status == "resolved_named_entity_contract"
    assert resolution.candidate_ciks == ("2222222222",)
    assert resolution.confirmed_ciks == ("2222222222",)
    assert resolution.target_cik == "2222222222"
    assert resolution.target_document_url == target_exhibit
    assert any(document.url == target_exhibit for document in result.packet.documents)
    assert any(
        candidate.snapshot_template.get("commitment") == 600_000_000
        for candidate in result.packet.candidates
    )


def test_two_name_confirmed_ciks_with_matching_contracts_remain_ambiguous():
    client, target_exhibit, second_exhibit = resolved_client(duplicate=True)
    result = resolve_named_entity_contracts(
        client,
        packet=empty_packet(),
        source_nodes=(source_node(),),
        root_cik="1111111111",
    )
    resolution = result.graph.resolutions[0]
    assert resolution.status == "ambiguous_named_entity_contract"
    assert set(resolution.confirmed_ciks) == {"2222222222", "3333333333"}
    assert resolution.target_cik is None
    assert not any(document.url in {target_exhibit, second_exhibit} for document in result.packet.documents)


def test_registry_title_must_match_exact_normalized_legal_name_not_fuzzy():
    client, _, _ = resolved_client(candidate_title="Target Finance Holdings LLC")
    result = resolve_named_entity_contracts(
        client,
        packet=empty_packet(),
        source_nodes=(source_node(),),
        root_cik="1111111111",
    )
    resolution = result.graph.resolutions[0]
    assert resolution.status == "no_candidate_cik"
    assert resolution.candidate_ciks == ()


def test_current_registry_name_is_rejected_when_source_date_name_history_does_not_confirm_it():
    client, _, _ = resolved_client(future_name=True)
    result = resolve_named_entity_contracts(
        client,
        packet=empty_packet(),
        source_nodes=(source_node(),),
        root_cik="1111111111",
    )
    resolution = result.graph.resolutions[0]
    assert resolution.status == "name_not_confirmed_at_source_date"
    assert resolution.candidate_ciks == ("2222222222",)
    assert resolution.confirmed_ciks == ()


def test_name_confirmation_cannot_override_contract_party_mismatch():
    client, target_exhibit, _ = resolved_client(wrong_party=True)
    result = resolve_named_entity_contracts(
        client,
        packet=empty_packet(),
        source_nodes=(source_node(),),
        root_cik="1111111111",
    )
    resolution = result.graph.resolutions[0]
    assert resolution.status == "contract_not_found_in_confirmed_cik"
    assert resolution.confirmed_ciks == ("2222222222",)
    assert not any(document.url == target_exhibit for document in result.packet.documents)

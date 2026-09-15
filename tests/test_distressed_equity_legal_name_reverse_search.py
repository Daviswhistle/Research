from datetime import date

from distressed_equity.contract_identity import extract_contract_identities, reverse_search_contract_identity
from distressed_equity.legal_name_alias import (
    alias_groups_from_graphs,
    build_legal_name_alias_graph_from_submissions,
)
from distressed_equity.sec import SecFiling
from distressed_equity.sec_instruments import filing_index_url


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

    def __init__(self, mapping):
        self.session = FakeSession(mapping)

    def _throttle(self):
        return None


def filing():
    return SecFiling(
        cik="0001326801",
        accession_number="0001326801-20-000010",
        filing_date=date(2020, 5, 15),
        form="8-K",
        primary_document="old8k.htm",
        report_date=date(2020, 5, 15),
    )


def index_html():
    return """
    <html><body><table>
      <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
      <tr><td>1</td><td>Current Report</td><td><a href="old8k.htm">old8k.htm</a></td><td>8-K</td><td>1000</td></tr>
      <tr><td>2</td><td>Credit Agreement</td><td><a href="ex10-1.htm">ex10-1.htm</a></td><td>EX-10.1</td><td>5000</td></tr>
    </table></body></html>
    """


def alias_graph(cutoff):
    return build_legal_name_alias_graph_from_submissions(
        {
            "name": "Meta Platforms, Inc.",
            "formerNames": [
                {"name": "Facebook Inc", "from": "2005-05-06", "to": "2021-10-27"},
            ],
        },
        cik="0001326801",
        analysis_date=cutoff,
    )


def historical_exhibit():
    return (
        "<html><body>Facebook Inc., as Borrower, is party to the "
        "CREDIT AGREEMENT dated as of May 3, 2020.</body></html>"
    )


def test_completed_sec_rename_can_bridge_contract_party_name():
    old = filing()
    base = filing_index_url(old).rsplit("/", 1)[0]
    exhibit = base + "/ex10-1.htm"
    mapping = {
        filing_index_url(old): index_html(),
        exhibit: historical_exhibit(),
    }
    source_identity = extract_contract_identities(
        "Meta Platforms, Inc., as Borrower, is party to the Credit Agreement dated May 3, 2020."
    )[0]
    graph = alias_graph(date(2022, 12, 31))
    result = reverse_search_contract_identity(
        FakeClient(mapping),
        identity=source_identity,
        filings=(old,),
        source_published_on=date(2022, 12, 31),
        alias_groups=alias_groups_from_graphs((graph,)),
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].document == "ex10-1.htm"


def test_rename_after_cutoff_cannot_bridge_party_name():
    old = filing()
    base = filing_index_url(old).rsplit("/", 1)[0]
    exhibit = base + "/ex10-1.htm"
    mapping = {
        filing_index_url(old): index_html(),
        exhibit: historical_exhibit(),
    }
    source_identity = extract_contract_identities(
        "Meta Platforms, Inc., as Borrower, is party to the Credit Agreement dated May 3, 2020."
    )[0]
    graph = alias_graph(date(2020, 12, 31))
    assert graph.alias_names == ("facebook inc",)
    result = reverse_search_contract_identity(
        FakeClient(mapping),
        identity=source_identity,
        filings=(old,),
        source_published_on=date(2020, 12, 31),
        alias_groups=alias_groups_from_graphs((graph,)),
    )
    assert result.candidates == ()

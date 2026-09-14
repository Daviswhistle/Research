from datetime import date

from distressed_equity.contract_identity import ContractIdentity, reverse_search_contract_identity
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


def filing(accession, filed, primary):
    return SecFiling(
        cik="0000000001",
        accession_number=accession,
        filing_date=filed,
        form="8-K",
        primary_document=primary,
        report_date=filed,
    )


def index_html(primary, exhibit, description):
    return f"""
    <html><body><table>
      <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
      <tr><td>1</td><td>Primary</td><td><a href="{primary}">{primary}</a></td><td>8-K</td><td>1000</td></tr>
      <tr><td>2</td><td>{description}</td><td><a href="{exhibit}">{exhibit}</a></td><td>EX-10.1</td><td>5000</td></tr>
    </table></body></html>
    """


def test_original_contract_search_does_not_treat_amendment_as_original_contract():
    amendment = filing("0000000001-19-000020", date(2019, 6, 1), "amendment8k.htm")
    base = filing_index_url(amendment).rsplit("/", 1)[0]
    exhibit_url = base + "/amendment.htm"
    client = FakeClient(
        {
            filing_index_url(amendment): index_html(
                "amendment8k.htm",
                "amendment.htm",
                "Amendment No. 1 to Credit Agreement",
            ),
            exhibit_url: """
                <html><body>
                Amendment No. 1 to that certain Credit Agreement dated May 3, 2019.
                </body></html>
            """,
        }
    )
    result = reverse_search_contract_identity(
        client,
        identity=ContractIdentity(
            title="Credit Agreement",
            kind="credit_agreement",
            execution_date=date(2019, 5, 3),
        ),
        filings=(amendment,),
        source_published_on=date(2020, 1, 1),
    )
    assert result.candidates == ()


def test_original_contract_remains_unique_when_amendment_also_quotes_original_date():
    original = filing("0000000001-19-000010", date(2019, 5, 6), "original8k.htm")
    amendment = filing("0000000001-19-000020", date(2019, 6, 1), "amendment8k.htm")
    original_base = filing_index_url(original).rsplit("/", 1)[0]
    amendment_base = filing_index_url(amendment).rsplit("/", 1)[0]
    original_url = original_base + "/credit.htm"
    amendment_url = amendment_base + "/amendment.htm"
    client = FakeClient(
        {
            filing_index_url(original): index_html("original8k.htm", "credit.htm", "Credit Agreement"),
            filing_index_url(amendment): index_html(
                "amendment8k.htm",
                "amendment.htm",
                "Amendment No. 1 to Credit Agreement",
            ),
            original_url: "<html><body>Credit Agreement dated May 3, 2019.</body></html>",
            amendment_url: "<html><body>Amendment to Credit Agreement dated May 3, 2019.</body></html>",
        }
    )
    result = reverse_search_contract_identity(
        client,
        identity=ContractIdentity(
            title="Credit Agreement",
            kind="credit_agreement",
            execution_date=date(2019, 5, 3),
        ),
        filings=(original, amendment),
        source_published_on=date(2020, 1, 1),
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].url == original_url

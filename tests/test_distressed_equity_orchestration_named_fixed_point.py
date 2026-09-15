from datetime import date

from distressed_equity.orchestration import build_research_bundle
from distressed_equity.sec import SecFiling, SecPointInTimeSnapshot
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


class FakeSecClient:
    user_agent = "Research test@example.com"

    def __init__(self, filings_by_cik, names, mapping, ticker_map):
        self.filings_by_cik = filings_by_cik
        self.names = names
        self.session = FakeSession(mapping)
        self._ticker_map = ticker_map
        self.root = next(iter(filings_by_cik.values()))[0]

    def _throttle(self):
        return None

    def ticker_map(self):
        return self._ticker_map

    def submissions(self, cik):
        key = str(cik).zfill(10)
        if key not in self.names:
            raise AssertionError(f"unexpected submissions CIK: {cik}")
        return {"name": self.names[key], "formerNames": []}

    def snapshot(self, **kwargs):
        cutoff = kwargs["analysis_date"]
        return SecPointInTimeSnapshot(
            ticker="PARENT",
            cik=self.root.cik,
            company_name=self.names[self.root.cik],
            analysis_date=cutoff,
            filings=(self.root,),
            facts={},
            warnings=(),
        )

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


def index_html(primary, exhibit=None, exhibit_type="EX-10.1"):
    row = ""
    if exhibit:
        row = (
            f'<tr><td>2</td><td>Credit Agreement</td><td><a href="{exhibit}">{exhibit}</a></td>'
            f'<td>{exhibit_type}</td><td>6000</td></tr>'
        )
    return f"""
    <html><body><table>
      <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
      <tr><td>1</td><td>Primary</td><td><a href="{primary}">{primary}</a></td><td>8-K</td><td>1000</td></tr>
      {row}
    </table></body></html>
    """


def test_workspace_repeats_named_resolution_after_explicit_hop():
    cutoff = date(2022, 12, 31)
    root = filing(
        "0001111111",
        "0001111111-22-000001",
        date(2022, 6, 30),
        "10-Q",
        "parent.htm",
    )
    named_a = filing(
        "0002222222",
        "0002222222-19-000003",
        date(2019, 5, 10),
        "8-K",
        "a8k.htm",
    )
    explicit_b = filing(
        "0003333333",
        "0003333333-18-000004",
        date(2018, 8, 15),
        "8-K",
        "b8k.htm",
    )
    named_c = filing(
        "0004444444",
        "0004444444-18-000002",
        date(2018, 1, 10),
        "8-K",
        "c8k.htm",
    )

    a_base = filing_index_url(named_a).rsplit("/", 1)[0]
    a_exhibit = a_base + "/a-credit.htm"
    b_base = filing_index_url(explicit_b).rsplit("/", 1)[0]
    b_exhibit = b_base + "/b-credit.htm"
    c_base = filing_index_url(named_c).rsplit("/", 1)[0]
    c_exhibit = c_base + "/c-credit.htm"
    explicit_b_url = (
        "https://www.sec.gov/Archives/edgar/data/3333333/"
        "000333333318000004/b-credit.htm"
    )

    mapping = {
        root.archive_url: (
            "<html><body>Credit Agreement dated May 3, 2019 among "
            "Target Finance LLC, as Borrower.</body></html>"
        ),
        filing_index_url(root): index_html("parent.htm"),
        filing_index_url(named_a): index_html("a8k.htm", "a-credit.htm"),
        a_exhibit: (
            "<html><body>Target Finance LLC, as Borrower, is party to the "
            "CREDIT AGREEMENT dated as of May 3, 2019. Reference is made to the "
            f"related agreement filed as exhibit at {explicit_b_url}</body></html>"
        ),
        filing_index_url(explicit_b): index_html("b8k.htm", "b-credit.htm", "EX-10.2"),
        b_exhibit: (
            "<html><body>Credit Agreement dated January 5, 2018 among "
            "C Finance LLC, as Borrower.</body></html>"
        ),
        filing_index_url(named_c): index_html("c8k.htm", "c-credit.htm"),
        c_exhibit: (
            "<html><body>C Finance LLC, as Borrower, is party to the CREDIT "
            "AGREEMENT dated as of January 5, 2018. Aggregate commitments of "
            "$425 million.</body></html>"
        ),
    }
    filings_by_cik = {
        root.cik: (root,),
        named_a.cik: (named_a,),
        explicit_b.cik: (explicit_b,),
        named_c.cik: (named_c,),
    }
    names = {
        root.cik: "Parent Co",
        named_a.cik: "Target Finance LLC",
        explicit_b.cik: "B Finance LLC",
        named_c.cik: "C Finance LLC",
    }
    client = FakeSecClient(
        filings_by_cik,
        names,
        mapping,
        {
            "TGT": (named_a.cik, "Target Finance LLC"),
            "CFIN": (named_c.cik, "C Finance LLC"),
        },
    )

    bundle = build_research_bundle(
        client,
        ticker="PARENT",
        analysis_date=cutoff,
        reference_depth=3,
    )

    named_graph = bundle.packet["named_entity_contract_graph"]
    resolved_named = [
        item
        for item in named_graph["resolutions"]
        if item["status"] == "resolved_named_entity_contract"
    ]
    assert {item["target_cik"] for item in resolved_named} == {
        named_a.cik,
        named_c.cik,
    }
    c_resolution = next(
        item for item in resolved_named if item["target_cik"] == named_c.cik
    )
    assert c_resolution["source_cik"] == explicit_b.cik
    assert c_resolution["source_depth"] == 2
    assert c_resolution["target_document_url"] == c_exhibit

    cross_graph = bundle.packet["cross_cik_graph"]
    assert cross_graph["named_entity_fixed_point_iterations"] == 2
    assert any(
        item["status"] == "resolved_cross_cik"
        and item["target_cik"] == explicit_b.cik
        for item in cross_graph["resolutions"]
    )
    assert not any(
        item["status"] == "resolved_cross_cik"
        and item["target_cik"] in {named_a.cik, named_c.cik}
        for item in cross_graph["resolutions"]
    )
    debt_packet = bundle.packet["debt_instrument_source_packet"]
    assert any(document["url"] == c_exhibit for document in debt_packet["documents"])
    assert any(
        candidate["snapshot_template"].get("commitment") == 425_000_000
        for candidate in debt_packet["candidates"]
    )

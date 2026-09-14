from datetime import date

from distressed_equity.orchestration import build_research_bundle
from distressed_equity.sec import SecFiling, SecPointInTimeSnapshot
from distressed_equity.sec_cik_lookup import SEC_CIK_LOOKUP_URL
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

    def __init__(self, root, target, mapping):
        self.root = root
        self.target = target
        self.session = FakeSession(mapping)

    def _throttle(self):
        return None

    def ticker_map(self):
        return {}

    def submissions(self, cik):
        key = str(cik).zfill(10)
        if key == self.root.cik:
            return {"name": "Parent Co", "formerNames": []}
        if key == self.target.cik:
            return {"name": "Private Finance LLC", "formerNames": []}
        raise AssertionError(f"unexpected submissions CIK: {cik}")

    def snapshot(self, **kwargs):
        cutoff = kwargs["analysis_date"]
        return SecPointInTimeSnapshot(
            ticker="PARENT",
            cik=self.root.cik,
            company_name="Parent Co",
            analysis_date=cutoff,
            filings=(self.root,),
            facts={},
            warnings=(),
        )

    def filings_as_of(self, cik, analysis_date, forms=()):
        key = str(cik).zfill(10)
        values = (self.root,) if key == self.root.cik else ((self.target,) if key == self.target.cik else ())
        allowed = set(forms)
        return tuple(
            item
            for item in values
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


def index_html(primary, rows=""):
    return f"""
    <html><body><table>
      <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
      <tr><td>1</td><td>Primary</td><td><a href="{primary}">{primary}</a></td><td>8-K</td><td>1000</td></tr>
      {rows}
    </table></body></html>
    """


def test_workspace_resolves_non_ticker_external_party_from_sec_all_cik_candidates():
    root = filing("0001111111", "0001111111-22-000001", date(2022, 6, 30), "10-Q", "parent.htm")
    target = filing("0002222222", "0002222222-19-000003", date(2019, 5, 10), "8-K", "target8k.htm")
    target_base = filing_index_url(target).rsplit("/", 1)[0]
    target_exhibit = target_base + "/ex10-1.htm"
    target_rows = (
        '<tr><td>2</td><td>Credit Agreement</td><td><a href="ex10-1.htm">ex10-1.htm</a></td>'
        '<td>EX-10.1</td><td>6000</td></tr>'
    )
    mapping = {
        SEC_CIK_LOOKUP_URL: "PRIVATE FINANCE LLC:0002222222:\n",
        root.archive_url: (
            "<html><body><p>Credit Agreement dated May 3, 2019 among "
            "Private Finance LLC, as Borrower.</p></body></html>"
        ),
        filing_index_url(root): index_html("parent.htm"),
        filing_index_url(target): index_html("target8k.htm", target_rows),
        target_exhibit: (
            "<html><body>Credit Agreement dated May 3, 2019 among "
            "Private Finance LLC, as Borrower. Aggregate commitments of $650 million."
            "</body></html>"
        ),
    }
    bundle = build_research_bundle(
        FakeSecClient(root, target, mapping),
        ticker="PARENT",
        analysis_date=date(2022, 12, 31),
        reference_depth=3,
    )

    named = bundle.packet["named_entity_contract_graph"]
    assert named is not None
    assert any(
        item["source_kind"] == "sec_cik_lookup_data_candidate_only"
        and item["cik"] == "0002222222"
        for item in named["candidates"]
    )
    resolution = next(
        item for item in named["resolutions"]
        if item["status"] == "resolved_named_entity_contract"
    )
    assert resolution["target_cik"] == "0002222222"
    assert resolution["target_document_url"] == target_exhibit
    assert any(
        item["url"] == target_exhibit
        for item in bundle.packet["debt_instrument_source_packet"]["documents"]
    )

from datetime import date

from distressed_equity.orchestration import build_research_bundle, render_research_summary
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

    def __init__(self, root, target, mapping):
        self.root = root
        self.target = target
        self.session = FakeSession(mapping)

    def _throttle(self):
        return None

    def ticker_map(self):
        return {"TGT": (self.target.cik, "Target Finance LLC")}

    def submissions(self, cik):
        key = str(cik).zfill(10)
        if key == self.root.cik:
            return {"name": "Parent Co", "formerNames": []}
        if key == self.target.cik:
            return {"name": "Target Finance LLC", "formerNames": []}
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
        if key == self.root.cik:
            values = (self.root,)
        elif key == self.target.cik:
            values = (self.target,)
        else:
            values = ()
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


def test_workspace_resolves_name_only_external_party_without_mislabeling_explicit_cross_cik():
    root = filing("0001111111", "0001111111-22-000001", date(2022, 6, 30), "10-Q", "parent.htm")
    target = filing("0002222222", "0002222222-19-000003", date(2019, 5, 10), "8-K", "target8k.htm")
    target_base = filing_index_url(target).rsplit("/", 1)[0]
    target_exhibit = target_base + "/ex10-1.htm"

    root_html = (
        "<html><body><p>$500 million of senior notes mature in 2027.</p>"
        "<p>Credit Agreement dated May 3, 2019 among Target Finance LLC, as Borrower.</p>"
        "</body></html>"
    )
    target_rows = (
        '<tr><td>2</td><td>Credit Agreement</td><td><a href="ex10-1.htm">ex10-1.htm</a></td>'
        '<td>EX-10.1</td><td>6000</td></tr>'
    )
    mapping = {
        root.archive_url: root_html,
        filing_index_url(root): index_html("parent.htm"),
        filing_index_url(target): index_html("target8k.htm", target_rows),
        target_exhibit: (
            "<html><body>Credit Agreement dated May 3, 2019 among "
            "Target Finance LLC, as Borrower. Aggregate commitments of $600 million "
            "under a revolving credit facility.</body></html>"
        ),
    }
    client = FakeSecClient(root, target, mapping)
    bundle = build_research_bundle(
        client,
        ticker="PARENT",
        analysis_date=date(2022, 12, 31),
        reference_depth=3,
    )

    packet = bundle.packet
    named = packet["named_entity_contract_graph"]
    assert named is not None
    resolved = [
        item for item in named["resolutions"]
        if item["status"] == "resolved_named_entity_contract"
    ]
    assert len(resolved) == 1
    assert resolved[0]["target_cik"] == "0002222222"
    assert resolved[0]["target_document_url"] == target_exhibit

    cross_graph = packet["cross_cik_graph"]
    assert cross_graph is not None
    assert not any(
        item["status"] == "resolved_cross_cik" and item.get("target_cik") == "0002222222"
        for item in cross_graph["resolutions"]
    )
    assert cross_graph["named_entity_contract_graph"]["resolutions"] == named["resolutions"]

    source_packet = packet["debt_instrument_source_packet"]
    assert any(document["url"] == target_exhibit for document in source_packet["documents"])
    assert any(
        candidate["snapshot_template"].get("commitment") == 600_000_000
        for candidate in source_packet["candidates"]
    )

    summary = render_research_summary(bundle)
    assert "Named external-entity contract resolutions: 1 (1 resolved)" in summary

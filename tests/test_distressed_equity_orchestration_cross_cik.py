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

    def __init__(self, root, foreign, mapping):
        self.root = root
        self.foreign = foreign
        self.session = FakeSession(mapping)

    def _throttle(self):
        return None

    def submissions(self, cik):
        key = str(cik).zfill(10)
        return {
            "name": "Parent Co" if key == self.root.cik else "Borrower Co",
            "formerNames": [],
        }

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
        values = (self.root,) if key == self.root.cik else ((self.foreign,) if key == self.foreign.cik else ())
        allowed = set(forms)
        return tuple(
            item for item in values
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


def test_research_bundle_freezes_cross_cik_graph_and_foreign_debt_source():
    root = filing("1111111111", "0001111111-22-000001", date(2022, 11, 1), "10-Q", "parent.htm")
    foreign = filing("2222222222", "0002222222-20-000010", date(2020, 5, 15), "8-K", "borrower8k.htm")
    foreign_base = filing_index_url(foreign).rsplit("/", 1)[0]
    foreign_exhibit = foreign_base + "/ex10-1.htm"
    root_html = (
        '<html><body><p>$500 million of senior notes mature in 2027.</p>'
        '<a href="/Archives/edgar/data/2222222222/000222222220000010/ex10-1.htm">'
        'Borrower Credit Agreement</a></body></html>'
    )
    foreign_rows = (
        '<tr><td>2</td><td>Credit Agreement</td><td><a href="ex10-1.htm">ex10-1.htm</a></td>'
        '<td>EX-10.1</td><td>5000</td></tr>'
    )
    mapping = {
        root.archive_url: root_html,
        filing_index_url(root): index_html("parent.htm"),
        filing_index_url(foreign): index_html("borrower8k.htm", foreign_rows),
        foreign_exhibit: (
            "<html><body><p>Credit Agreement dated May 3, 2020.</p>"
            "<p>Aggregate commitments of $700 million under a revolving credit facility.</p></body></html>"
        ),
    }
    client = FakeSecClient(root, foreign, mapping)
    bundle = build_research_bundle(
        client,
        ticker="PARENT",
        analysis_date=date(2022, 12, 31),
        resolve_contract_identity_references=False,
    )
    packet = bundle.packet
    assert packet["legal_name_alias_graph"]["canonical_name_as_of"] == "Parent Co"
    graph = packet["cross_cik_graph"]
    assert graph is not None
    assert any(item["status"] == "resolved_cross_cik" for item in graph["resolutions"])
    assert any(item["target_cik"] == "2222222222" for item in graph["references"])
    assert any(
        candidate["snapshot_template"].get("commitment") == 700_000_000
        for candidate in packet["debt_instrument_source_packet"]["candidates"]
    )
    summary = render_research_summary(bundle)
    assert "Cross-CIK SEC resolutions" in summary
    assert "Explicit legal-entity graph" in summary
    assert "Canonical legal name at cutoff" in summary

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

    def __init__(self, root, foreign_entry, foreign_old, mapping):
        self.root = root
        self.foreign_entry = foreign_entry
        self.foreign_old = foreign_old
        self.session = FakeSession(mapping)

    def _throttle(self):
        return None

    def submissions(self, cik):
        key = str(cik).zfill(10)
        if key == self.root.cik:
            return {"name": "Parent Co", "formerNames": []}
        if key == self.foreign_entry.cik:
            return {
                "name": "Borrower New LLC",
                "formerNames": [
                    {"name": "Borrower Old LLC", "from": "2010-01-01", "to": "2020-06-01"},
                ],
            }
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
        elif key == self.foreign_entry.cik:
            values = (self.foreign_entry, self.foreign_old)
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


def test_workspace_resolves_locatorless_contract_inside_explicit_foreign_cik():
    root = filing("0001111111", "0001111111-22-000001", date(2022, 11, 1), "10-Q", "parent.htm")
    foreign_entry = filing("0002222222", "0002222222-22-000010", date(2022, 5, 15), "8-K", "entry8k.htm")
    foreign_old = filing("0002222222", "0002222222-19-000003", date(2019, 5, 10), "8-K", "old8k.htm")

    entry_base = filing_index_url(foreign_entry).rsplit("/", 1)[0]
    entry_exhibit = entry_base + "/ex10-entry.htm"
    old_base = filing_index_url(foreign_old).rsplit("/", 1)[0]
    old_exhibit = old_base + "/ex10-1.htm"

    root_html = (
        '<html><body><p>$500 million of senior notes mature in 2027.</p>'
        '<a href="/Archives/edgar/data/2222222/000222222222000010/ex10-entry.htm">'
        'foreign borrower facility discussion</a></body></html>'
    )
    entry_rows = (
        '<tr><td>2</td><td>Facility discussion</td><td><a href="ex10-entry.htm">ex10-entry.htm</a></td>'
        '<td>EX-10.2</td><td>5000</td></tr>'
    )
    old_rows = (
        '<tr><td>2</td><td>Credit Agreement</td><td><a href="ex10-1.htm">ex10-1.htm</a></td>'
        '<td>EX-10.1</td><td>6000</td></tr>'
    )
    mapping = {
        root.archive_url: root_html,
        filing_index_url(root): index_html("parent.htm"),
        filing_index_url(foreign_entry): index_html("entry8k.htm", entry_rows),
        entry_exhibit: (
            "<html><body>Borrower New LLC, as Borrower, is party to the "
            "Credit Agreement dated May 3, 2019.</body></html>"
        ),
        filing_index_url(foreign_old): index_html("old8k.htm", old_rows),
        old_exhibit: (
            "<html><body>Credit Agreement dated May 3, 2019 among "
            "Borrower Old LLC, as Borrower. Aggregate commitments of $900 million "
            "under a revolving credit facility.</body></html>"
        ),
    }
    client = FakeSecClient(root, foreign_entry, foreign_old, mapping)
    bundle = build_research_bundle(
        client,
        ticker="PARENT",
        analysis_date=date(2022, 12, 31),
        reference_depth=3,
    )

    packet = bundle.packet
    cross_graph = packet["cross_cik_graph"]
    assert cross_graph is not None
    assert len(cross_graph["foreign_contract_graphs"]) == 1
    foreign_graph = cross_graph["foreign_contract_graphs"][0]
    assert foreign_graph["target_cik"] == "0002222222"
    assert foreign_graph["cross_cik_hops"] == 1
    assert any(
        edge["status"] == "resolved_by_contract_identity"
        for edge in foreign_graph["graph"]["edges"]
    )
    assert any(
        document["url"] == old_exhibit
        for document in packet["debt_instrument_source_packet"]["documents"]
    )
    assert any(
        candidate["snapshot_template"].get("commitment") == 900_000_000
        for candidate in packet["debt_instrument_source_packet"]["candidates"]
    )
    summary = render_research_summary(bundle)
    assert "Foreign-CIK contract graphs: 1 (1 locator-less contracts resolved)" in summary

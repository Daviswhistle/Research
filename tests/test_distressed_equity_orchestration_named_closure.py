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

    def __init__(self, root, named_target, foreign_target, mapping):
        self.root = root
        self.named_target = named_target
        self.foreign_target = foreign_target
        self.session = FakeSession(mapping)

    def _throttle(self):
        return None

    def ticker_map(self):
        return {"TGT": (self.named_target.cik, "Target Finance LLC")}

    def submissions(self, cik):
        key = str(cik).zfill(10)
        names = {
            self.root.cik: "Parent Co",
            self.named_target.cik: "Target Finance LLC",
            self.foreign_target.cik: "Foreign Finance LLC",
        }
        if key not in names:
            raise AssertionError(f"unexpected submissions CIK: {cik}")
        return {"name": names[key], "formerNames": []}

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
        elif key == self.named_target.cik:
            values = (self.named_target,)
        elif key == self.foreign_target.cik:
            values = (self.foreign_target,)
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


def test_named_entity_restart_is_serialized_in_final_top_level_cross_graph():
    root = filing("0001111111", "0001111111-22-000001", date(2022, 6, 30), "10-Q", "parent.htm")
    named_target = filing("0002222222", "0002222222-19-000003", date(2019, 5, 10), "8-K", "target8k.htm")
    foreign_target = filing("0003333333", "0003333333-18-000004", date(2018, 8, 15), "8-K", "foreign8k.htm")

    named_base = filing_index_url(named_target).rsplit("/", 1)[0]
    named_exhibit = named_base + "/ex10-1.htm"
    foreign_base = filing_index_url(foreign_target).rsplit("/", 1)[0]
    foreign_exhibit = foreign_base + "/ex10-b.htm"
    explicit_foreign_url = (
        "https://www.sec.gov/Archives/edgar/data/3333333/"
        "000333333318000004/ex10-b.htm"
    )

    root_html = (
        "<html><body><p>Credit Agreement dated May 3, 2019 among "
        "Target Finance LLC, as Borrower.</p></body></html>"
    )
    named_rows = (
        '<tr><td>2</td><td>Credit Agreement</td><td><a href="ex10-1.htm">ex10-1.htm</a></td>'
        '<td>EX-10.1</td><td>6000</td></tr>'
    )
    foreign_rows = (
        '<tr><td>2</td><td>Foreign Facility</td><td><a href="ex10-b.htm">ex10-b.htm</a></td>'
        '<td>EX-10.2</td><td>6000</td></tr>'
    )
    mapping = {
        root.archive_url: root_html,
        filing_index_url(root): index_html("parent.htm"),
        filing_index_url(named_target): index_html("target8k.htm", named_rows),
        named_exhibit: (
            "<html><body>Credit Agreement dated May 3, 2019 among "
            "Target Finance LLC, as Borrower. Reference is made to the related "
            f"agreement filed as exhibit at {explicit_foreign_url}</body></html>"
        ),
        filing_index_url(foreign_target): index_html("foreign8k.htm", foreign_rows),
        foreign_exhibit: (
            "<html><body>Aggregate commitments of $700 million under a revolving "
            "credit facility.</body></html>"
        ),
    }
    client = FakeSecClient(root, named_target, foreign_target, mapping)
    bundle = build_research_bundle(
        client,
        ticker="PARENT",
        analysis_date=date(2022, 12, 31),
        reference_depth=3,
    )

    named = bundle.packet["named_entity_contract_graph"]
    assert any(
        item["status"] == "resolved_named_entity_contract"
        and item["target_cik"] == named_target.cik
        for item in named["resolutions"]
    )

    cross_graph = bundle.packet["cross_cik_graph"]
    resolved_foreign = [
        item
        for item in cross_graph["resolutions"]
        if item["status"] == "resolved_cross_cik"
        and item["target_cik"] == foreign_target.cik
    ]
    assert len(resolved_foreign) == 1
    assert resolved_foreign[0]["source_cik"] == named_target.cik
    assert resolved_foreign[0]["source_depth"] == 1
    assert resolved_foreign[0]["target_depth"] == 2
    assert any(
        document["url"] == foreign_exhibit
        for document in cross_graph["resolved_documents"]
    )
    assert any(
        document["url"] == foreign_exhibit
        for document in bundle.packet["debt_instrument_source_packet"]["documents"]
    )
    # The name-only hop itself remains outside explicit cross-CIK provenance.
    assert not any(
        item["target_cik"] == named_target.cik
        for item in cross_graph["resolutions"]
    )

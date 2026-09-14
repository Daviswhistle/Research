from datetime import date

from distressed_equity.market import PricePoint, SecurityIdentity
from distressed_equity.orchestration import build_research_bundle, render_research_summary
from distressed_equity.sec import SecFiling, SecPointInTimeSnapshot


class FakeResponse:
    text = """
    <html><body>
    <p>The revolving credit facility contains a maximum net leverage ratio of 5.0%.</p>
    <p>$500 million of senior notes mature in 2027.</p>
    </body></html>
    """

    def raise_for_status(self):
        return None


class FakeSession:
    def get(self, *args, **kwargs):
        return FakeResponse()


class FakeSecClient:
    user_agent = "Research test@example.com"
    session = FakeSession()

    def _throttle(self):
        return None

    def snapshot(self, **kwargs):
        cutoff = kwargs["analysis_date"]
        filing = SecFiling(
            cik="0000000001",
            accession_number="0000000001-22-000001",
            filing_date=date(2022, 11, 1),
            form="10-Q",
            primary_document="test.htm",
            report_date=date(2022, 9, 30),
        )
        return SecPointInTimeSnapshot(
            ticker="TEST",
            cik="0000000001",
            company_name="Test Co",
            analysis_date=cutoff,
            filings=(filing,),
            facts={},
            warnings=(),
        )


class FakeMarketProvider:
    name = "fake-market"
    bulk_safe = True

    def universe(self, as_of):
        return (
            SecurityIdentity(
                security_id="PERM-1",
                symbol="TEST",
                name="Test Co",
                start_date=date(2020, 1, 1),
                provider=self.name,
            ),
        )

    def price_on_or_before(self, security, as_of):
        return PricePoint(
            security_id=security.security_id,
            symbol=security.symbol,
            date=date(2022, 12, 30),
            close=2.0,
            adjusted=False,
            source="fake raw prices",
        )

    def adjusted_history(self, security, start, end):
        return (
            PricePoint(
                security_id=security.security_id,
                symbol=security.symbol,
                date=date(2021, 1, 1),
                close=10.0,
                adjusted=True,
                source="fake adjusted prices",
            ),
            PricePoint(
                security_id=security.security_id,
                symbol=security.symbol,
                date=date(2022, 12, 30),
                close=2.0,
                adjusted=True,
                source="fake adjusted prices",
            ),
        )


def test_bundle_connects_sec_debt_diff_market_and_task_templates():
    bundle = build_research_bundle(
        FakeSecClient(),
        ticker="TEST",
        analysis_date=date(2022, 12, 31),
        market_provider=FakeMarketProvider(),
    )
    packet = bundle.packet
    assert packet["screening_draft"]["screening_candidate_draft"]["capital_structure"]["current_price"] == 2.0
    assert packet["market_snapshot"]["adjusted_drawdown_from_peak"] == 0.8
    assert packet["capital_stack_packet"]["snippets"]
    assert packet["capital_stack_diff"]["changes"] == []
    names = [item["task"]["name"] for item in packet["agent_tasks"]]
    assert "capital_stack_extractor" in names
    assert "historical_market_reconstructor" in names
    assert all("result_template" in item for item in packet["agent_tasks"])
    assert packet["point_in_time_violations"] == []


def test_summary_makes_resume_path_explicit():
    bundle = build_research_bundle(
        FakeSecClient(),
        ticker="TEST",
        analysis_date=date(2022, 12, 31),
    )
    summary = render_research_summary(bundle)
    assert "distressed-equity-covenant" in summary
    assert "structured result templates" in summary

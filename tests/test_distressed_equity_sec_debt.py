from datetime import date

from distressed_equity.sec import SecClient, SecFiling
from distressed_equity.sec_debt import (
    build_capital_stack_agent_task,
    build_capital_stack_packet,
    extract_capital_stack_snippets,
    html_to_text,
)


HTML = """
<html>
<head><script>var fake = '$9 billion notes due 2099';</script></head>
<body>
<p>We maintain a $500 million revolving credit facility that matures in 2027.</p>
<p>The facility is secured by substantially all assets and bears interest at SOFR plus 3.25% per annum.</p>
<p>A springing maturity applies in 2026 if more than $100 million of the 2027 senior notes remains outstanding.</p>
<p>The agreement contains a minimum liquidity covenant of $50 million.</p>
</body>
</html>
"""


def filing(filing_date=date(2022, 11, 1)):
    return SecFiling(
        cik="0000000001",
        accession_number="0000000001-22-000001",
        filing_date=filing_date,
        form="10-Q",
        primary_document="test.htm",
        report_date=date(2022, 9, 30),
    )


def test_html_to_text_excludes_script_noise():
    text = html_to_text(HTML)
    assert "$9 billion" not in text
    assert "$500 million revolving credit facility" in text


def test_extracts_capital_stack_categories_and_candidates():
    snippets = extract_capital_stack_snippets(HTML, filing(), max_snippets=50)
    categories = {snippet.category for snippet in snippets}
    assert {"revolver", "maturity", "collateral_seniority", "interest", "springing_maturity", "covenant"}.issubset(categories)
    assert any(
        any(candidate.amount == 500_000_000 for candidate in snippet.amount_candidates)
        for snippet in snippets
    )
    assert any(2027 in snippet.year_candidates for snippet in snippets)
    assert any(3.25 in snippet.percentage_candidates for snippet in snippets)
    assert all(snippet.published_on == date(2022, 11, 1) for snippet in snippets)


class FakeResponse:
    text = HTML

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, headers=None, timeout=None, params=None):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        return FakeResponse()


def test_packet_ignores_future_filing_and_builds_agent_task():
    session = FakeSession()
    client = SecClient(user_agent="Research test@example.com", session=session)
    past = filing()
    future = filing(date(2023, 1, 5))
    packet = build_capital_stack_packet(
        client,
        ticker="TEST",
        company_name="Test Co",
        analysis_date=date(2022, 12, 31),
        filings=(future, past),
        filing_limit=3,
        max_snippets_per_filing=20,
    )
    assert packet.source_filings and packet.source_filings[0]["accession_number"] == past.accession_number
    assert len(session.calls) == 1
    task = build_capital_stack_agent_task(packet)
    assert task.name == "capital_stack_extractor"
    assert any("structured agent-result" in guardrail for guardrail in task.guardrails)
    assert any("springing" in output.lower() for output in task.required_output)

from datetime import date

from distressed_equity.capital_stack_diff import diff_capital_stack_packet
from distressed_equity.sec_debt import AmountCandidate, CapitalStackPacket, CapitalStackSnippet


def snippet(accession, filing_date, amount, year, percentage):
    return CapitalStackSnippet(
        snippet_id=f"{accession}:1",
        category="maturity",
        text=f"Senior notes amount {amount} mature in {year} at {percentage}%.",
        source_url="https://www.sec.gov/example",
        published_on=filing_date,
        form="10-Q",
        accession_number=accession,
        amount_candidates=(AmountCandidate(amount=amount, raw=f"${amount}"),),
        year_candidates=(year,),
        percentage_candidates=(percentage,),
    )


def test_diff_flags_candidate_changes_without_asserting_amendment():
    packet = CapitalStackPacket(
        ticker="TEST",
        company_name="Test Co",
        analysis_date=date(2022, 12, 31),
        snippets=(
            snippet("a", date(2022, 5, 1), 500.0, 2025, 5.0),
            snippet("b", date(2022, 11, 1), 600.0, 2027, 6.0),
        ),
        source_filings=(
            {
                "form": "10-Q",
                "filing_date": "2022-05-01",
                "accession_number": "a",
                "url": "https://www.sec.gov/a",
                "snippet_count": 1,
            },
            {
                "form": "10-Q",
                "filing_date": "2022-11-01",
                "accession_number": "b",
                "url": "https://www.sec.gov/b",
                "snippet_count": 1,
            },
        ),
        warnings=(),
    )
    report = diff_capital_stack_packet(packet)
    assert len(report.changes) == 1
    change = report.changes[0]
    assert "amount_signal_changed" in change.change_types
    assert "maturity_or_date_signal_changed" in change.change_types
    assert "rate_or_ratio_signal_changed" in change.change_types
    assert change.confidence == "candidate"
    assert any("not confirmed amendments" in warning for warning in report.warnings)


def test_diff_with_one_filing_has_no_comparisons():
    packet = CapitalStackPacket(
        ticker="TEST",
        company_name="Test Co",
        analysis_date=date(2022, 12, 31),
        snippets=(snippet("a", date(2022, 5, 1), 500.0, 2025, 5.0),),
        source_filings=(
            {
                "form": "10-Q",
                "filing_date": "2022-05-01",
                "accession_number": "a",
                "url": "https://www.sec.gov/a",
                "snippet_count": 1,
            },
        ),
        warnings=(),
    )
    report = diff_capital_stack_packet(packet)
    assert report.comparisons == ()
    assert report.changes == ()

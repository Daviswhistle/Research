from datetime import date

from distressed_equity.sec_instruments import FilingDocument, extract_source_candidates


def _document():
    return FilingDocument(
        sequence=2,
        description="Debt Schedule",
        document="ex41.htm",
        document_type="EX-4.1",
        size=50_000,
        url="https://www.sec.gov/Archives/example/ex41.htm",
        source_accession="0000000001-22-000001",
        filing_date=date(2022, 12, 1),
        filing_form="8-K",
    )


def test_one_instrument_transposed_html_table_is_not_skipped():
    html = """
    <table>
      <tr><th>Term</th><th>5.25% Senior Notes due 2028</th></tr>
      <tr><th>Principal amount ($ in millions)</th><td>500</td></tr>
      <tr><th>Maturity</th><td>June 15, 2028</td></tr>
      <tr><th>Coupon</th><td>5.25%</td></tr>
      <tr><th>CUSIP</th><td>123456789</td></tr>
    </table>
    """
    _, candidates = extract_source_candidates(html, _document(), max_candidates=20)
    columns = [candidate for candidate in candidates if candidate.cluster_status == "table_column"]
    assert len(columns) == 1
    row = columns[0].snapshot_template
    assert row["name"] == "5.25% Senior Notes due 2028"
    assert row["principal"] == 500_000_000
    assert row["maturity_date"] == "2028-06-15"
    assert row["coupon_pct"] == 5.25
    assert row["cusip"] == "123456789"

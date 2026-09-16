from datetime import date

from distressed_equity.sec_instruments import FilingDocument, extract_source_candidates


def document():
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


def table_candidates(html):
    spans, candidates = extract_source_candidates(html, document(), max_candidates=20)
    return spans, [
        candidate
        for candidate in candidates
        if candidate.cluster_status in {"table_row", "table_row_group", "table_column"}
    ]


def test_transposed_table_maps_one_instrument_per_column():
    html = """
    <html><body>
      <table>
        <tr>
          <th>Term</th>
          <th>5.25% Senior Secured Notes due 2028<sup>1</sup></th>
          <th>6.00% Senior Notes due 2030</th>
        </tr>
        <tr><th>Principal amount ($ in millions)</th><td>500<sup>1</sup></td><td>400</td></tr>
        <tr><th>Maturity</th><td>June 15, 2028</td><td>June 15, 2030</td></tr>
        <tr><th>Coupon</th><td>5.25%</td><td>6.00%</td></tr>
        <tr><th>CUSIP</th><td>123456789</td><td>987654321</td></tr>
        <tr><td colspan="3">(1) Principal amount is the face amount before unamortized discount.</td></tr>
      </table>
    </body></html>
    """
    spans, candidates = table_candidates(html)
    columns = [candidate for candidate in candidates if candidate.cluster_status == "table_column"]
    assert len(columns) == 2

    by_cusip = {candidate.snapshot_template["cusip"]: candidate for candidate in columns}
    first = by_cusip["123456789"]
    second = by_cusip["987654321"]
    assert first.snapshot_template["principal"] == 500_000_000
    assert first.snapshot_template["maturity_date"] == "2028-06-15"
    assert first.snapshot_template["coupon_pct"] == 5.25
    assert second.snapshot_template["principal"] == 400_000_000
    assert second.snapshot_template["maturity_date"] == "2030-06-15"
    assert second.snapshot_template["coupon_pct"] == 6.0
    assert "transposed_html_table" in first.cluster_basis
    assert any(":t1:fn6" in ref for ref in first.source_span_ids)
    assert any("linked footnote marker" in warning for warning in first.warnings)
    assert any("TABLE FOOTNOTE" in span.text for span in spans if span.span_id in first.source_span_ids)


def test_rowspan_continuation_rows_form_one_instrument_candidate():
    html = """
    <table>
      <tr>
        <th>Instrument</th><th>Principal ($ millions)</th><th>Interest rate</th><th>Maturity</th><th>CUSIP</th>
      </tr>
      <tr>
        <td rowspan="2">Term Loan B</td><td>800</td><td>SOFR + 3.25%</td><td></td><td rowspan="2">111111111</td>
      </tr>
      <tr><td></td><td></td><td>December 15, 2028</td></tr>
    </table>
    """
    _, candidates = table_candidates(html)
    groups = [candidate for candidate in candidates if candidate.cluster_status == "table_row_group"]
    assert len(groups) == 1
    candidate = groups[0]
    assert candidate.snapshot_template["name"] == "Term Loan B"
    assert candidate.snapshot_template["principal"] == 800_000_000
    assert candidate.snapshot_template["benchmark"] == "SOFR"
    assert candidate.snapshot_template["spread_bps"] == 325.0
    assert candidate.snapshot_template["maturity_date"] == "2028-12-15"
    assert candidate.snapshot_template["cusip"] == "111111111"
    assert len([ref for ref in candidate.source_span_ids if ":t1:r" in ref]) == 2


def test_blank_name_continuation_row_is_joined_conservatively():
    html = """
    <table>
      <tr><th>Instrument</th><th>Principal ($ millions)</th><th>Coupon</th><th>Maturity</th></tr>
      <tr><td>7.50% Senior Notes</td><td>300</td><td>7.50%</td><td></td></tr>
      <tr><td></td><td></td><td></td><td>September 1, 2029</td></tr>
    </table>
    """
    _, candidates = table_candidates(html)
    groups = [candidate for candidate in candidates if candidate.cluster_status == "table_row_group"]
    assert len(groups) == 1
    candidate = groups[0]
    assert candidate.snapshot_template["principal"] == 300_000_000
    assert candidate.snapshot_template["coupon_pct"] == 7.5
    assert candidate.snapshot_template["maturity_date"] == "2029-09-01"


def test_conflicting_values_across_continuation_rows_are_left_unresolved():
    html = """
    <table>
      <tr><th>Instrument</th><th>Principal ($ millions)</th><th>Maturity</th></tr>
      <tr><td rowspan="2">Term Loan B</td><td>500</td><td>2028</td></tr>
      <tr><td>400</td><td></td></tr>
    </table>
    """
    _, candidates = table_candidates(html)
    groups = [candidate for candidate in candidates if candidate.cluster_status == "table_row_group"]
    assert len(groups) == 1
    candidate = groups[0]
    assert candidate.snapshot_template["principal"] is None
    assert any("conflicting high-confidence principal" in warning for warning in candidate.warnings)
    assert candidate.snapshot_template["maturity_year"] == 2028


def test_superscript_footnote_marker_does_not_break_money_parsing_or_become_candidate():
    html = """
    <table>
      <tr><th>Instrument</th><th>Principal ($ millions)</th><th>Maturity</th></tr>
      <tr><td>5.25% Senior Notes due 2028<sup>a</sup></td><td>500<sup>a</sup></td><td>2028</td></tr>
      <tr><td colspan="3"><sup>a</sup> Amount excludes $12 million of unamortized discount.</td></tr>
    </table>
    """
    spans, candidates = table_candidates(html)
    rows = [candidate for candidate in candidates if candidate.cluster_status == "table_row"]
    assert len(rows) == 1
    candidate = rows[0]
    assert candidate.snapshot_template["principal"] == 500_000_000
    assert any("linked footnote marker" in warning for warning in candidate.warnings)
    assert len([span for span in spans if "TABLE FOOTNOTE" in span.text]) == 1
    assert not any(candidate.snapshot_template["name"].lower().startswith("amount excludes") for candidate in rows)


def test_body_th_scope_row_is_not_mistaken_for_an_extra_header_row():
    html = """
    <table>
      <tr><th>Instrument</th><th>Principal ($ millions)</th><th>Maturity</th></tr>
      <tr><th scope="row">5.25% Senior Notes due 2028</th><td>500</td><td>2028</td></tr>
      <tr><th scope="row">6.00% Senior Notes due 2030</th><td>400</td><td>2030</td></tr>
    </table>
    """
    _, candidates = table_candidates(html)
    rows = [candidate for candidate in candidates if candidate.cluster_status == "table_row"]
    assert len(rows) == 2
    principals = sorted(candidate.snapshot_template["principal"] for candidate in rows)
    assert principals == [400_000_000, 500_000_000]


def test_aggregate_and_footnote_rows_are_not_promoted_as_instruments():
    html = """
    <table>
      <tr><th>Instrument</th><th>Principal ($ millions)</th><th>Maturity</th></tr>
      <tr><td>5.25% Senior Notes due 2028</td><td>500</td><td>2028</td></tr>
      <tr><td>Total debt</td><td>500</td><td></td></tr>
      <tr><td colspan="3">(1) Includes $20 million classified as current.</td></tr>
    </table>
    """
    _, candidates = table_candidates(html)
    names = [candidate.snapshot_template["name"] for candidate in candidates]
    assert names == ["5.25% Senior Notes due 2028"]

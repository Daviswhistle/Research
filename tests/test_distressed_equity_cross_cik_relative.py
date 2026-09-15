from datetime import date

from distressed_equity.cross_cik import extract_cross_cik_references


def test_relative_sec_archives_href_proves_foreign_cik_without_name_lookup():
    raw = (
        '<a href="/Archives/edgar/data/2222222222/'
        '000222222220000010/ex10-1.htm">Credit Agreement</a>'
    )
    refs = extract_cross_cik_references(
        raw,
        source_node_id="filing:root:primary",
        source_cik="1111111111",
        source_accession="0001111111-22-000001",
        source_published_on=date(2022, 11, 1),
    )
    assert len(refs) == 1
    assert refs[0].target_cik == "2222222222"
    assert refs[0].cited_accession == "0002222222-20-000010"
    assert refs[0].cited_url.startswith("https://www.sec.gov/Archives/")

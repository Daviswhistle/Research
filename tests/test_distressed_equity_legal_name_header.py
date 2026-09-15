from datetime import date

from distressed_equity.legal_name_header import (
    complete_submission_text_url,
    extract_complete_submission_name_evidence,
)
from distressed_equity.sec import SecFiling


def filing(cik="0000123456", accession="0000123456-22-000010"):
    return SecFiling(
        cik=cik,
        accession_number=accession,
        filing_date=date(2022, 6, 30),
        form="10-Q",
        primary_document="q2.htm",
    )


def test_complete_submission_url_uses_cik_directory_and_accession_txt():
    item = filing()
    assert complete_submission_text_url(item) == (
        "https://www.sec.gov/Archives/edgar/data/123456/"
        "000012345622000010/0000123456-22-000010.txt"
    )


def test_header_parser_selects_only_target_cik_entity_block():
    raw = """
<SEC-HEADER>
FILER:
    COMPANY DATA:
        COMPANY CONFORMED NAME: New Parent Inc.
        CENTRAL INDEX KEY: 0000123456
    FORMER COMPANY:
        FORMER CONFORMED NAME: Old Parent Inc.
        DATE OF NAME CHANGE: 20210115
REPORTING-OWNER:
    OWNER DATA:
        COMPANY CONFORMED NAME: Other Person LLC
        CENTRAL INDEX KEY: 0000654321
    FORMER NAME:
        FORMER CONFORMED NAME: Wrong Old Name LLC
        DATE OF NAME CHANGE: 20190101
</SEC-HEADER>
<DOCUMENT>
<TYPE>10-Q
</DOCUMENT>
"""
    result = extract_complete_submission_name_evidence(raw, filing=filing())
    assert result.current_name == "New Parent Inc."
    assert [item.name for item in result.former_names] == ["Old Parent Inc."]
    assert result.former_names[0].change_on == date(2021, 1, 15)
    assert "Wrong Old Name LLC" not in {item.name for item in result.former_names}


def test_future_name_change_boundary_is_not_promoted():
    raw = """
<SEC-HEADER>
FILER:
    COMPANY DATA:
        COMPANY CONFORMED NAME: Example Inc.
        CENTRAL INDEX KEY: 0000123456
    FORMER COMPANY:
        FORMER CONFORMED NAME: Future Former Inc.
        DATE OF NAME CHANGE: 20270101
</SEC-HEADER>
"""
    result = extract_complete_submission_name_evidence(raw, filing=filing())
    assert result.former_names == ()
    assert any("future DATE OF NAME CHANGE ignored" in item for item in result.warnings)

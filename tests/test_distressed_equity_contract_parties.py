from datetime import date

from distressed_equity.contract_identity import extract_contract_identities, reverse_search_contract_identity
from distressed_equity.contract_parties import (
    contract_parties_compatible,
    extract_contract_parties,
    normalize_party_name,
)
from distressed_equity.sec import SecFiling
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


class FakeClient:
    user_agent = "Research test@example.com"

    def __init__(self, mapping):
        self.session = FakeSession(mapping)

    def _throttle(self):
        return None


def filing(accession, filed, primary):
    return SecFiling(
        cik="0000000001",
        accession_number=accession,
        filing_date=filed,
        form="8-K",
        primary_document=primary,
        report_date=filed,
    )


def index_html(primary, exhibit):
    return f"""
    <html><body><table>
      <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
      <tr><td>1</td><td>Current Report</td><td><a href="{primary}">{primary}</a></td><td>8-K</td><td>1000</td></tr>
      <tr><td>2</td><td>Credit Agreement</td><td><a href="{exhibit}">{exhibit}</a></td><td>EX-10.1</td><td>5000</td></tr>
    </table></body></html>
    """


def test_extracts_role_aware_borrower_and_guarantor_parties():
    parties = extract_contract_parties(
        "ABC Borrower, LLC, as Borrower, and Parent Holdings, Inc., as Parent Guarantor"
    )
    assert {(party.role, party.normalized_name) for party in parties} == {
        ("borrower", "abc borrower llc"),
        ("guarantor", "parent holdings inc"),
    }


def test_party_name_normalization_handles_common_legal_suffix_punctuation():
    assert normalize_party_name("ABC Holdings, Inc.") == normalize_party_name("ABC Holdings Incorporated")
    assert normalize_party_name("Borrower, L.L.C.") == normalize_party_name("Borrower LLC")


def test_contract_identity_keeps_same_day_facilities_separate_by_borrower():
    identities = extract_contract_identities(
        "ABC Borrower LLC, as Borrower, entered into a Credit Agreement dated May 3, 2019. "
        "XYZ Borrower LLC, as Borrower, entered into a Credit Agreement dated May 3, 2019."
    )
    assert len(identities) == 2
    fingerprints = [
        {(party.role, party.normalized_name) for party in identity.parties}
        for identity in identities
    ]
    assert {("borrower", "abc borrower llc")} in fingerprints
    assert {("borrower", "xyz borrower llc")} in fingerprints


def test_primary_obligor_is_a_hard_filter_for_locatorless_matching():
    source = extract_contract_identities(
        "ABC Borrower LLC, as Borrower, is party to the Credit Agreement dated May 3, 2019."
    )[0]
    same = extract_contract_identities(
        "CREDIT AGREEMENT dated as of May 3, 2019 among ABC Borrower LLC, as Borrower."
    )[0]
    different = extract_contract_identities(
        "CREDIT AGREEMENT dated as of May 3, 2019 among XYZ Borrower LLC, as Borrower."
    )[0]
    assert contract_parties_compatible(source.parties, same.parties) is True
    assert contract_parties_compatible(source.parties, different.parties) is False


def test_source_with_party_does_not_match_candidate_that_cannot_confirm_party():
    source = extract_contract_identities(
        "ABC Borrower LLC, as Borrower, is party to the Credit Agreement dated May 3, 2019."
    )[0]
    candidate = extract_contract_identities("Credit Agreement dated May 3, 2019.")[0]
    assert source.parties
    assert candidate.parties == ()
    assert contract_parties_compatible(source.parties, candidate.parties) is False


def test_reverse_search_disambiguates_same_title_and_date_by_borrower():
    old_a = filing("0000000001-19-000010", date(2019, 5, 6), "old-a.htm")
    old_b = filing("0000000001-19-000011", date(2019, 5, 7), "old-b.htm")
    base_a = filing_index_url(old_a).rsplit("/", 1)[0]
    base_b = filing_index_url(old_b).rsplit("/", 1)[0]
    exhibit_a = base_a + "/ex10-1a.htm"
    exhibit_b = base_b + "/ex10-1b.htm"
    mapping = {
        filing_index_url(old_a): index_html("old-a.htm", "ex10-1a.htm"),
        filing_index_url(old_b): index_html("old-b.htm", "ex10-1b.htm"),
        exhibit_a: (
            "<html><body>CREDIT AGREEMENT dated as of May 3, 2019 among "
            "ABC Borrower LLC, as Borrower.</body></html>"
        ),
        exhibit_b: (
            "<html><body>CREDIT AGREEMENT dated as of May 3, 2019 among "
            "XYZ Borrower LLC, as Borrower.</body></html>"
        ),
    }
    identity = extract_contract_identities(
        "ABC Borrower LLC, as Borrower, is party to the Credit Agreement dated May 3, 2019."
    )[0]
    result = reverse_search_contract_identity(
        FakeClient(mapping),
        identity=identity,
        filings=(old_a, old_b),
        source_published_on=date(2022, 11, 1),
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].source_accession == old_a.accession_number


def test_guarantor_can_disambiguate_when_no_borrower_is_named():
    source = extract_contract_identities(
        "Parent Holdings Inc., as Guarantor, is party to the Credit Agreement dated May 3, 2019."
    )[0]
    same = extract_contract_identities(
        "Credit Agreement dated May 3, 2019 among Parent Holdings, Inc., as Guarantor."
    )[0]
    other = extract_contract_identities(
        "Credit Agreement dated May 3, 2019 among Other Parent Inc., as Guarantor."
    )[0]
    assert contract_parties_compatible(source.parties, same.parties) is True
    assert contract_parties_compatible(source.parties, other.parties) is False


def test_cross_sentence_party_after_contract_title_is_not_used_as_identity_evidence():
    identity = extract_contract_identities(
        "Credit Agreement dated May 3, 2019. Unrelated Subsidiary LLC, as Borrower, entered another facility."
    )[0]
    assert identity.parties == ()

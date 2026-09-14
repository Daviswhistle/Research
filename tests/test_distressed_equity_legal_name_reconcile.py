from datetime import date

from distressed_equity.legal_name_alias import (
    build_legal_name_alias_graph,
    build_legal_name_alias_graph_from_submissions,
)
from distressed_equity.legal_name_header import (
    CompleteSubmissionFormerName,
    CompleteSubmissionNameEvidence,
    complete_submission_text_url,
)
from distressed_equity.legal_name_reconcile import reconcile_legal_name_sources
from distressed_equity.sec import SecFiling


def evidence(*, current, former=(), filed=date(2022, 6, 30), accession="0000123456-22-000010"):
    return CompleteSubmissionNameEvidence(
        cik="0000123456",
        accession_number=accession,
        filing_date=filed,
        source_url=f"https://www.sec.gov/Archives/example/{accession}.txt",
        current_name=current,
        normalized_current_name=current.lower().replace(".", ""),
        former_names=tuple(former),
        warnings=(),
    )


def test_historical_header_prevents_undated_current_name_lookahead():
    submissions = build_legal_name_alias_graph_from_submissions(
        {"name": "Future Name Inc.", "formerNames": []},
        cik="0000123456",
        analysis_date=date(2020, 12, 31),
    )
    header = evidence(current="Historical Name Inc.", filed=date(2020, 11, 1), accession="0000123456-20-000010")
    reconciled = reconcile_legal_name_sources(submissions, (header,))
    assert reconciled.canonical_name_as_of == "Historical Name Inc."
    assert reconciled.canonical_status == "conflict_header_preferred"
    assert "future name inc" not in reconciled.alias_names
    assert "historical name inc" in reconciled.alias_names


def test_header_only_former_name_fills_incomplete_submissions_history():
    submissions = build_legal_name_alias_graph_from_submissions(
        {"name": "New Name Inc.", "formerNames": []},
        cik="0000123456",
        analysis_date=date(2022, 12, 31),
    )
    old = CompleteSubmissionFormerName(
        name="Old Name Inc.",
        normalized_name="old name inc",
        change_on=date(2021, 1, 15),
    )
    reconciled = reconcile_legal_name_sources(
        submissions,
        (evidence(current="New Name Inc.", former=(old,)),),
    )
    assert reconciled.canonical_status == "confirmed_by_header_and_submissions"
    assert set(reconciled.alias_names) == {"old name inc", "new name inc"}
    comparison = next(item for item in reconciled.comparisons if item.normalized_name == "old name inc")
    assert comparison.status == "header_only"


def test_boundary_mismatch_is_explicit_not_silently_merged():
    submissions = build_legal_name_alias_graph_from_submissions(
        {
            "name": "New Name Inc.",
            "formerNames": [
                {"name": "Old Name Inc.", "from": "2010-01-01", "to": "2021-01-15"},
            ],
        },
        cik="0000123456",
        analysis_date=date(2022, 12, 31),
    )
    old = CompleteSubmissionFormerName(
        name="Old Name Inc.",
        normalized_name="old name inc",
        change_on=date(2021, 1, 16),
    )
    reconciled = reconcile_legal_name_sources(
        submissions,
        (evidence(current="New Name Inc.", former=(old,)),),
    )
    comparison = next(item for item in reconciled.comparisons if item.normalized_name == "old name inc")
    assert comparison.status == "boundary_conflict"
    assert comparison.submissions_boundaries == (date(2021, 1, 15),)
    assert comparison.header_boundaries == (date(2021, 1, 16),)
    assert any("legal-name boundary conflict" in item for item in reconciled.warnings)


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

    def __init__(self, filing, header_text):
        self.filing = filing
        self.session = FakeSession({complete_submission_text_url(filing): header_text})

    def _throttle(self):
        return None

    def submissions(self, cik):
        return {"name": "Future Name Inc.", "formerNames": []}

    def filings_as_of(self, cik, analysis_date, forms=()):
        return (self.filing,) if self.filing.filing_date <= analysis_date else ()


def test_public_builder_fetches_complete_header_and_returns_reconciled_graph():
    filing = SecFiling(
        cik="0000123456",
        accession_number="0000123456-20-000010",
        filing_date=date(2020, 11, 1),
        form="10-Q",
        primary_document="q3.htm",
    )
    header = """
<SEC-HEADER>
FILER:
    COMPANY DATA:
        COMPANY CONFORMED NAME: Historical Name Inc.
        CENTRAL INDEX KEY: 0000123456
</SEC-HEADER>
"""
    graph = build_legal_name_alias_graph(
        FakeClient(filing, header),
        cik="0000123456",
        analysis_date=date(2020, 12, 31),
    )
    assert graph.canonical_name_as_of == "Historical Name Inc."
    assert graph.canonical_status == "conflict_header_preferred"
    assert len(graph.header_evidence) == 1
    assert "future name inc" not in graph.alias_names

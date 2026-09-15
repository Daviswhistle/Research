from datetime import date

from distressed_equity.legal_name_alias import build_legal_name_alias_graph
from distressed_equity.legal_name_header import complete_submission_text_url
from distressed_equity.sec import SecFiling


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if url not in self.mapping:
            raise AssertionError(f"unexpected URL: {url}")
        return FakeResponse(self.mapping[url])


class FakeClient:
    user_agent = "Research test@example.com"

    def __init__(self, filings, submissions, mapping):
        self._filings = tuple(filings)
        self._submissions = submissions
        self.session = FakeSession(mapping)

    def _throttle(self):
        return None

    def submissions(self, cik):
        return self._submissions

    def filings_as_of(self, cik, analysis_date, forms=()):
        allowed = set(forms)
        return tuple(
            item
            for item in self._filings
            if item.filing_date <= analysis_date and (not allowed or item.form in allowed)
        )


def filing(year, sequence):
    cik = "0000123456"
    return SecFiling(
        cik=cik,
        accession_number=f"0000123456-{str(year)[2:]}-{sequence:06d}",
        filing_date=date(year, 6, 30),
        form="10-Q",
        primary_document=f"q{year}.htm",
    )


def header(current, *, former=None, changed=None):
    former_block = ""
    if former:
        former_block = f"""
    FORMER COMPANY:
        FORMER CONFORMED NAME: {former}
        DATE OF NAME CHANGE: {changed.strftime('%Y%m%d')}
"""
    return f"""
<SEC-HEADER>
FILER:
    COMPANY DATA:
        COMPANY CONFORMED NAME: {current}
        CENTRAL INDEX KEY: 0000123456
{former_block}</SEC-HEADER>
"""


def test_recent_headers_stop_when_known_submissions_history_is_fully_corrobated():
    filings = [filing(2022, 1), filing(2021, 2), filing(2020, 3), filing(2018, 4), filing(2015, 5)]
    recent_header = header(
        "New Name Inc.",
        former="Old Name Inc.",
        changed=date(2020, 1, 15),
    )
    mapping = {
        complete_submission_text_url(item): recent_header
        for item in filings[:3]
    }
    client = FakeClient(
        filings,
        {
            "name": "New Name Inc.",
            "formerNames": [
                {"name": "Old Name Inc.", "from": "2010-01-01", "to": "2020-01-15"},
            ],
        },
        mapping,
    )

    graph = build_legal_name_alias_graph(
        client,
        cik="0000123456",
        analysis_date=date(2022, 12, 31),
    )

    assert graph.header_scan_status == "recent_only_sufficient"
    assert graph.header_scan_filings_fetched == 3
    assert set(graph.alias_names) == {"old name inc", "new name inc"}
    assert set(client.session.calls) == set(mapping)


def test_deep_scan_recovers_old_current_name_missing_from_recent_headers():
    filings = [
        filing(2022, 1), filing(2021, 2), filing(2020, 3),
        filing(2018, 4), filing(2015, 5), filing(2010, 6),
    ]
    mapping = {}
    for item in filings:
        current = "Old Name Inc." if item.filing_date.year == 2010 else "New Name Inc."
        mapping[complete_submission_text_url(item)] = header(current)
    client = FakeClient(
        filings,
        {"name": "New Name Inc.", "formerNames": []},
        mapping,
    )

    graph = build_legal_name_alias_graph(
        client,
        cik="0000123456",
        analysis_date=date(2022, 12, 31),
        header_max_filings=6,
    )

    assert "old name inc" in graph.alias_names
    assert "new name inc" in graph.alias_names
    assert graph.header_scan_status == "deep_scan_complete"
    assert graph.header_scan_filings_fetched == 6
    assert graph.header_scan_oldest_filing_on == date(2010, 6, 30)


def test_required_alias_forces_midpoint_scan_even_when_oldest_and_latest_names_match():
    filings = [
        filing(2022, 1), filing(2021, 2), filing(2020, 3), filing(2018, 4),
        filing(2016, 5), filing(2013, 6), filing(2010, 7),
    ]
    mapping = {}
    for item in filings:
        current = "Temporary Name Inc." if item.filing_date.year == 2016 else "Current Name Inc."
        mapping[complete_submission_text_url(item)] = header(current)
    client = FakeClient(
        filings,
        {"name": "Current Name Inc.", "formerNames": []},
        mapping,
    )

    graph = build_legal_name_alias_graph(
        client,
        cik="0000123456",
        analysis_date=date(2022, 12, 31),
        required_aliases=("Temporary Name Inc.",),
        header_max_filings=7,
    )

    assert "temporary name inc" in graph.alias_names
    assert graph.header_scan_status == "target_alias_found_deep"
    # Recent three + oldest + the first temporal midpoint (2016).
    assert graph.header_scan_filings_fetched == 5

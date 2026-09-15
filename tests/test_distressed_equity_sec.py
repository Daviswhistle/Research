from datetime import date

import pytest

from distressed_equity.evidence import validate_point_in_time
from distressed_equity.sec import (
    SEC_DATA_BASE,
    SEC_WEB_BASE,
    SecClient,
    normalize_cik,
    select_point_in_time_fact,
    snapshot_to_evidence,
)


def test_normalize_cik():
    assert normalize_cik(1690820) == "0001690820"
    assert normalize_cik("0001690820") == "0001690820"
    with pytest.raises(ValueError):
        normalize_cik("not-a-cik")


def test_fact_selection_uses_most_recent_period_across_aliases():
    payload = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue",
                    "units": {
                        "USD": [
                            {
                                "start": "2021-01-01",
                                "end": "2021-12-31",
                                "val": 100,
                                "filed": "2022-02-15",
                                "form": "10-K",
                                "accn": "0000000000-22-000001",
                            }
                        ]
                    },
                },
                "Revenues": {
                    "label": "Revenues",
                    "units": {
                        "USD": [
                            {
                                "start": "2022-07-01",
                                "end": "2022-09-30",
                                "val": 40,
                                "filed": "2022-11-04",
                                "form": "10-Q",
                                "accn": "0000000000-22-000002",
                            }
                        ]
                    },
                },
            }
        }
    }
    fact = select_point_in_time_fact(payload, "revenue", date(2022, 12, 31))
    assert fact is not None
    assert fact.tag == "Revenues"
    assert fact.period_end == date(2022, 9, 30)


def test_fact_selection_rejects_future_filing():
    payload = {
        "facts": {
            "us-gaap": {
                "OperatingIncomeLoss": {
                    "label": "Operating income",
                    "units": {
                        "USD": [
                            {
                                "start": "2022-01-01",
                                "end": "2022-09-30",
                                "val": -50,
                                "filed": "2022-11-01",
                                "form": "10-Q",
                            },
                            {
                                "start": "2022-01-01",
                                "end": "2022-12-31",
                                "val": 20,
                                "filed": "2023-02-20",
                                "form": "10-K",
                            },
                        ]
                    },
                }
            }
        }
    }
    fact = select_point_in_time_fact(payload, "operating_income", date(2022, 12, 31))
    assert fact is not None
    assert fact.value == -50
    assert fact.filed == date(2022, 11, 1)


class FixtureSecClient(SecClient):
    def __init__(self, fixtures):
        super().__init__(user_agent="Research test research@example.com")
        self.fixtures = fixtures
        self.requested = []

    def _get_json(self, url):
        self.requested.append(url)
        return self.fixtures[url]


def test_snapshot_loads_old_submission_file_without_lookahead():
    ticker_url = f"{SEC_WEB_BASE}/files/company_tickers.json"
    submissions_url = f"{SEC_DATA_BASE}/submissions/CIK0001690820.json"
    history_url = f"{SEC_DATA_BASE}/submissions/CIK0001690820-submissions-001.json"
    facts_url = f"{SEC_DATA_BASE}/api/xbrl/companyfacts/CIK0001690820.json"
    fixtures = {
        ticker_url: {
            "0": {"cik_str": 1690820, "ticker": "TEST", "title": "Test Company"}
        },
        submissions_url: {
            "name": "Test Company",
            "filings": {
                "recent": {
                    "accessionNumber": ["0001690820-23-000001"],
                    "filingDate": ["2023-02-20"],
                    "reportDate": ["2022-12-31"],
                    "acceptanceDateTime": ["2023-02-20T16:00:00.000Z"],
                    "form": ["10-K"],
                    "primaryDocument": ["future.htm"],
                    "items": [""],
                },
                "files": [
                    {
                        "name": "CIK0001690820-submissions-001.json",
                        "filingFrom": "2020-01-01",
                        "filingTo": "2022-12-31",
                    }
                ],
            },
        },
        history_url: {
            "accessionNumber": ["0001690820-22-000003", "0001690820-22-000001"],
            "filingDate": ["2022-11-04", "2022-02-15"],
            "reportDate": ["2022-09-30", "2021-12-31"],
            "acceptanceDateTime": ["2022-11-04T16:00:00.000Z", "2022-02-15T16:00:00.000Z"],
            "form": ["10-Q", "10-K"],
            "primaryDocument": ["q3.htm", "annual.htm"],
            "items": ["", ""],
        },
        facts_url: {
            "facts": {
                "us-gaap": {
                    "CashAndCashEquivalentsAtCarryingValue": {
                        "label": "Cash",
                        "units": {
                            "USD": [
                                {
                                    "end": "2022-09-30",
                                    "val": 150,
                                    "filed": "2022-11-04",
                                    "form": "10-Q",
                                    "accn": "0001690820-22-000003",
                                },
                                {
                                    "end": "2022-12-31",
                                    "val": 250,
                                    "filed": "2023-02-20",
                                    "form": "10-K",
                                    "accn": "0001690820-23-000001",
                                },
                            ]
                        },
                    }
                }
            }
        },
    }
    client = FixtureSecClient(fixtures)
    snapshot = client.snapshot(ticker="TEST", analysis_date=date(2022, 12, 31), filing_limit=10)
    assert snapshot.cik == "0001690820"
    assert [filing.form for filing in snapshot.filings] == ["10-Q", "10-K"]
    assert snapshot.filings[0].archive_url.endswith("/000169082022000003/q3.htm")
    assert snapshot.facts["cash"].value == 150
    evidence = snapshot_to_evidence(snapshot)
    assert validate_point_in_time(evidence, date(2022, 12, 31)) == ()


def test_sec_client_requires_identified_user_agent():
    with pytest.raises(ValueError):
        SecClient(user_agent="")
    with pytest.raises(ValueError):
        SecClient(user_agent="Research research@example.com", min_interval_seconds=0.05)

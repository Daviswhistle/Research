from __future__ import annotations

from typing import Any

from transformation_scanner.dart_client import DartClient
from transformation_scanner.models import Disclosure


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)
        self.content = b""

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.headers: dict[str, str] = {}
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, *, params: dict[str, Any], timeout: float) -> _FakeResponse:
        del timeout
        self.calls.append((url, dict(params)))
        if not self._responses:
            raise AssertionError("예상보다 많은 HTTP 호출")
        return self._responses.pop(0)


def test_list_disclosures_paginates_and_sorts() -> None:
    session = _FakeSession(
        [
            _FakeResponse(
                {
                    "status": "000",
                    "page_no": 1,
                    "total_page": 2,
                    "list": [
                        {
                            "corp_cls": "K",
                            "corp_name": "B사",
                            "corp_code": "00000002",
                            "stock_code": "000002",
                            "report_nm": "전환사채권 발행결정",
                            "rcept_no": "20260102000002",
                            "flr_nm": "B사",
                            "rcept_dt": "20260102",
                        }
                    ],
                }
            ),
            _FakeResponse(
                {
                    "status": "000",
                    "page_no": 2,
                    "total_page": 2,
                    "list": [
                        {
                            "corp_cls": "K",
                            "corp_name": "A사",
                            "corp_code": "00000001",
                            "stock_code": "000001",
                            "report_nm": "타법인 주식 및 출자증권 양수결정",
                            "rcept_no": "20260101000001",
                            "flr_nm": "A사",
                            "rcept_dt": "20260101",
                        }
                    ],
                }
            ),
        ]
    )
    client = DartClient(api_key="x" * 40, session=session)

    disclosures = client.list_disclosures(
        start_date="20260101",
        end_date="20260102",
        corp_classes=("K",),
        public_types=("B",),
    )

    assert [item.corp_name for item in disclosures] == ["A사", "B사"]
    assert len(session.calls) == 2
    assert session.calls[1][1]["page_no"] == 2


def test_market_wide_search_is_split_inside_three_month_limit() -> None:
    session = _FakeSession(
        [
            _FakeResponse({"status": "013", "message": "조회된 데이터가 없습니다."}),
            _FakeResponse({"status": "013", "message": "조회된 데이터가 없습니다."}),
        ]
    )
    client = DartClient(api_key="x" * 40, session=session)

    disclosures = client.list_disclosures(
        start_date="20260101",
        end_date="20260401",
        corp_classes=("K",),
        public_types=("B",),
    )

    assert disclosures == []
    assert len(session.calls) == 2
    assert session.calls[0][1]["bgn_de"] == "20260101"
    assert session.calls[0][1]["end_de"] == "20260321"
    assert session.calls[1][1]["bgn_de"] == "20260322"
    assert session.calls[1][1]["end_de"] == "20260401"


def test_no_data_status_returns_empty_list() -> None:
    session = _FakeSession([_FakeResponse({"status": "013", "message": "조회된 데이터가 없습니다."})])
    client = DartClient(api_key="x" * 40, session=session)

    disclosures = client.list_disclosures(
        start_date="20260101",
        end_date="20260101",
        corp_classes=("K",),
        public_types=("B",),
    )

    assert disclosures == []


def test_structured_enrichment_matches_exact_receipt_number() -> None:
    session = _FakeSession(
        [
            _FakeResponse(
                {
                    "status": "000",
                    "list": [
                        {"rcept_no": "20260101000000", "inhdtl_inhprc": "10"},
                        {"rcept_no": "20260101000001", "inhdtl_inhprc": "20"},
                    ],
                }
            )
        ]
    )
    client = DartClient(api_key="x" * 40, session=session)
    disclosure = Disclosure(
        corp_cls="K",
        corp_name="A사",
        corp_code="00000001",
        stock_code="000001",
        report_nm="타법인 주식 및 출자증권 양수결정",
        rcept_no="20260101000001",
        flr_nm="A사",
        rcept_dt="20260101",
    )

    row = client.fetch_matching_structured_row(
        structured_kind="acquisition",
        disclosure=disclosure,
    )

    assert row is not None
    assert row["inhdtl_inhprc"] == "20"


def test_corrected_filing_can_match_by_wider_first_receipt_window() -> None:
    session = _FakeSession(
        [
            _FakeResponse({"status": "013", "message": "조회된 데이터가 없습니다."}),
            _FakeResponse(
                {
                    "status": "000",
                    "list": [
                        {"rcept_no": "20260115000001", "inhdtl_inhprc": "30"},
                    ],
                }
            ),
        ]
    )
    client = DartClient(api_key="x" * 40, session=session)
    disclosure = Disclosure(
        corp_cls="K",
        corp_name="A사",
        corp_code="00000001",
        stock_code="000001",
        report_nm="[기재정정]타법인 주식 및 출자증권 양수결정",
        rcept_no="20260115000001",
        flr_nm="A사",
        rcept_dt="20260415",
    )

    row = client.fetch_matching_structured_row(
        structured_kind="acquisition",
        disclosure=disclosure,
        fallback_lookback_days=400,
    )

    assert row is not None
    assert row["inhdtl_inhprc"] == "30"
    assert len(session.calls) == 2
    assert session.calls[1][1]["bgn_de"] == "20250311"

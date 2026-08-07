from __future__ import annotations

import io
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Iterator, Mapping
from xml.etree import ElementTree

import requests

from .models import Disclosure


DEFAULT_BASE_URL = "https://opendart.fss.or.kr/api"
STRUCTURED_ENDPOINTS = {
    "acquisition": "otcprStkInvscrInhDecsn.json",
    "cb": "cvbdIsDecsn.json",
    "bw": "bdwtIsDecsn.json",
    "equity_issuance": "piicDecsn.json",
}


class DartClientError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = bool(retryable)


@dataclass(frozen=True)
class DartResponseStatus:
    status: str
    message: str


def _parse_ymd(ymd: str) -> datetime:
    try:
        return datetime.strptime(str(ymd), "%Y%m%d")
    except ValueError as exc:
        raise DartClientError(f"잘못된 날짜 형식: {ymd}", retryable=False) from exc


def _ymd_minus_days(ymd: str, days: int) -> str:
    return (_parse_ymd(ymd) - timedelta(days=max(int(days), 0))).strftime("%Y%m%d")


def _date_windows(start_date: str, end_date: str, *, max_span_days: int = 80) -> Iterator[tuple[str, str]]:
    start = _parse_ymd(start_date)
    end = _parse_ymd(end_date)
    if start > end:
        raise DartClientError(
            f"검색 시작일이 종료일보다 늦습니다: {start_date}>{end_date}",
            retryable=False,
        )
    span = max(int(max_span_days), 1)
    cursor = start
    while cursor <= end:
        window_end = min(end, cursor + timedelta(days=span - 1))
        yield cursor.strftime("%Y%m%d"), window_end.strftime("%Y%m%d")
        cursor = window_end + timedelta(days=1)


class DartClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 20.0,
        max_attempts: int = 3,
        session: Any | None = None,
    ) -> None:
        self._api_key = str(api_key or "").strip()
        self._base_url = str(base_url).rstrip("/")
        self._timeout_seconds = max(float(timeout_seconds), 0.1)
        self._max_attempts = max(int(max_attempts), 1)
        self._session = session or requests.Session()
        if hasattr(self._session, "headers"):
            self._session.headers.update({"User-Agent": "Daviswhistle-Research-transformation-scanner/0.1"})

    def _require_api_key(self) -> None:
        if not self._api_key:
            raise DartClientError("DART_API_KEY가 비어 있습니다.", retryable=False)

    def _url(self, endpoint: str) -> str:
        return f"{self._base_url}/{str(endpoint).lstrip('/')}"

    def _get(self, endpoint: str, *, params: Mapping[str, Any]) -> Any:
        self._require_api_key()
        query = {"crtfc_key": self._api_key, **dict(params)}
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._session.get(
                    self._url(endpoint),
                    params=query,
                    timeout=self._timeout_seconds,
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self._max_attempts:
                    raise DartClientError(f"OpenDART 네트워크 예외: {exc}", retryable=True) from exc
                time.sleep(min(0.5 * (2 ** (attempt - 1)), 4.0))
                continue

            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code == 200:
                return response
            retryable = status_code in {429, 500, 502, 503, 504}
            body = str(getattr(response, "text", ""))[:500]
            if not retryable or attempt >= self._max_attempts:
                raise DartClientError(
                    f"OpenDART HTTP {status_code}: {body}",
                    retryable=retryable,
                )
            time.sleep(min(0.5 * (2 ** (attempt - 1)), 4.0))

        raise DartClientError(f"OpenDART 요청 실패: {last_error}", retryable=True)

    @staticmethod
    def _status_from_payload(payload: Mapping[str, Any]) -> DartResponseStatus:
        return DartResponseStatus(
            status=str(payload.get("status") or "").strip(),
            message=str(payload.get("message") or "").strip(),
        )

    def _get_json(
        self,
        endpoint: str,
        *,
        params: Mapping[str, Any],
        no_data_is_empty: bool = True,
    ) -> dict[str, Any]:
        response = self._get(endpoint, params=params)
        try:
            payload = response.json()
        except Exception as exc:
            raise DartClientError(f"OpenDART JSON 파싱 실패: {exc}", retryable=True) from exc
        if not isinstance(payload, dict):
            raise DartClientError("OpenDART 응답이 JSON object가 아닙니다.", retryable=True)

        status = self._status_from_payload(payload)
        if status.status in {"", "000"}:
            return payload
        if status.status == "013" and no_data_is_empty:
            return {"status": "013", "message": status.message, "list": []}
        retryable = status.status in {"020", "800", "900"}
        raise DartClientError(
            f"OpenDART 오류 {status.status}: {status.message}",
            retryable=retryable,
        )

    def list_disclosures(
        self,
        *,
        start_date: str,
        end_date: str,
        corp_classes: Iterable[str] = ("Y", "K"),
        public_types: Iterable[str] = ("B", "C", "E", "I"),
        page_count: int = 100,
    ) -> list[Disclosure]:
        by_receipt: dict[str, Disclosure] = {}
        bounded_page_count = max(1, min(int(page_count), 100))
        windows = tuple(_date_windows(start_date, end_date))
        for window_start, window_end in windows:
            for corp_cls in tuple(corp_classes):
                for public_type in tuple(public_types):
                    page_no = 1
                    while True:
                        payload = self._get_json(
                            "list.json",
                            params={
                                "bgn_de": window_start,
                                "end_de": window_end,
                                "last_reprt_at": "N",
                                "pblntf_ty": str(public_type),
                                "corp_cls": str(corp_cls),
                                "sort": "date",
                                "sort_mth": "asc",
                                "page_no": page_no,
                                "page_count": bounded_page_count,
                            },
                        )
                        rows = payload.get("list") or []
                        if not isinstance(rows, list):
                            raise DartClientError("OpenDART list 필드 형식이 이상합니다.", retryable=True)
                        for row in rows:
                            if not isinstance(row, Mapping):
                                continue
                            disclosure = Disclosure.from_api(row)
                            if disclosure.rcept_no:
                                by_receipt[disclosure.rcept_no] = disclosure

                        total_page = int(payload.get("total_page") or 1)
                        if page_no >= total_page:
                            break
                        page_no += 1

        return sorted(by_receipt.values(), key=lambda item: (item.rcept_dt, item.rcept_no))

    def fetch_structured_rows(
        self,
        *,
        structured_kind: str,
        corp_code: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        endpoint = STRUCTURED_ENDPOINTS.get(str(structured_kind))
        if endpoint is None:
            raise DartClientError(
                f"지원하지 않는 structured_kind: {structured_kind}",
                retryable=False,
            )
        payload = self._get_json(
            endpoint,
            params={
                "corp_code": str(corp_code),
                "bgn_de": str(start_date),
                "end_de": str(end_date),
            },
        )
        rows = payload.get("list") or []
        if not isinstance(rows, list):
            raise DartClientError(f"{structured_kind} list 형식이 이상합니다.", retryable=True)
        return [dict(row) for row in rows if isinstance(row, Mapping)]

    @staticmethod
    def _find_receipt(rows: Iterable[Mapping[str, Any]], rcept_no: str) -> dict[str, Any] | None:
        expected = str(rcept_no).strip()
        for row in rows:
            if str(row.get("rcept_no") or "").strip() == expected:
                return dict(row)
        return None

    def fetch_matching_structured_row(
        self,
        *,
        structured_kind: str,
        disclosure: Disclosure,
        fallback_lookback_days: int = 0,
    ) -> dict[str, Any] | None:
        rows = self.fetch_structured_rows(
            structured_kind=structured_kind,
            corp_code=disclosure.corp_code,
            start_date=disclosure.rcept_dt,
            end_date=disclosure.rcept_dt,
        )
        matched = self._find_receipt(rows, disclosure.rcept_no)
        if matched is not None or fallback_lookback_days <= 0:
            return matched

        fallback_rows = self.fetch_structured_rows(
            structured_kind=structured_kind,
            corp_code=disclosure.corp_code,
            start_date=_ymd_minus_days(disclosure.rcept_dt, fallback_lookback_days),
            end_date=disclosure.rcept_dt,
        )
        return self._find_receipt(fallback_rows, disclosure.rcept_no)

    @staticmethod
    def _xml_error_status(content: bytes) -> DartResponseStatus | None:
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError:
            return None
        status = str(root.findtext("status") or "").strip()
        if not status:
            return None
        return DartResponseStatus(status=status, message=str(root.findtext("message") or "").strip())

    def fetch_document_zip(self, *, rcept_no: str) -> bytes:
        response = self._get("document.xml", params={"rcept_no": str(rcept_no)})
        content = bytes(getattr(response, "content", b"") or b"")
        if zipfile.is_zipfile(io.BytesIO(content)):
            return content
        status = self._xml_error_status(content)
        if status is not None:
            retryable = status.status in {"020", "800", "900"}
            raise DartClientError(
                f"OpenDART 원문 오류 {status.status}: {status.message}",
                retryable=retryable,
            )
        raise DartClientError("OpenDART 원문 응답이 ZIP 파일이 아닙니다.", retryable=True)

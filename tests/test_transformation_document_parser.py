from __future__ import annotations

import io
import zipfile

from transformation_scanner.document_parser import (
    extract_business_purpose_phrases,
    extract_document_rows,
    extract_financial_snapshot,
)


def _document_zip(html: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("document.xml", html)
    return buffer.getvalue()


def test_extracts_business_purpose_and_financial_rows() -> None:
    content = _document_zip(
        """
        <html><body><table>
          <tr><th>구분</th><th>내용</th></tr>
          <tr><td>사업목적 추가</td><td>인공지능 데이터센터 장비 제조</td></tr>
          <tr><td>매출액</td><td>51,000,000,000원</td></tr>
          <tr><td>영업이익</td><td>20,000,000,000원</td></tr>
        </table></body></html>
        """
    )

    rows = extract_document_rows(content)
    phrases = extract_business_purpose_phrases(rows)
    snapshot = extract_financial_snapshot(rows)

    assert any("인공지능 데이터센터" in phrase for phrase in phrases)
    assert snapshot["revenue"] == 51_000_000_000
    assert snapshot["operating_profit"] == 20_000_000_000

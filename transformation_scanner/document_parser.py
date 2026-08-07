from __future__ import annotations

import io
import re
import zipfile
from html.parser import HTMLParser
from typing import Iterable


_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


class _TableRowParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None
        self._cell_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        lowered = tag.lower()
        if lowered == "tr":
            if self._row:
                self.rows.append(self._row)
            self._row = []
        elif lowered in {"td", "th"}:
            if self._row is None:
                self._row = []
            self._cell_parts = []
            self._cell_depth = 1
        elif self._cell_parts is not None:
            self._cell_depth += 1

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"td", "th"} and self._cell_parts is not None:
            text = _WHITESPACE_RE.sub(" ", " ".join(self._cell_parts)).strip()
            if self._row is None:
                self._row = []
            self._row.append(text)
            self._cell_parts = None
            self._cell_depth = 0
        elif lowered == "tr":
            if self._row:
                self.rows.append(self._row)
            self._row = None
        elif self._cell_parts is not None and self._cell_depth > 0:
            self._cell_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            text = str(data).strip()
            if text:
                self._cell_parts.append(text)

    def close(self) -> None:
        super().close()
        if self._row:
            self.rows.append(self._row)
        self._row = None


def _decode_document(content: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp949", "euc-kr"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def extract_document_rows(document_zip: bytes) -> list[list[str]]:
    rows: list[list[str]] = []
    with zipfile.ZipFile(io.BytesIO(document_zip)) as archive:
        for name in archive.namelist():
            lowered = name.lower()
            if not lowered.endswith((".xml", ".html", ".htm", ".xhtml", ".txt")):
                continue
            text = _decode_document(archive.read(name))
            parser = _TableRowParser()
            try:
                parser.feed(text)
                parser.close()
            except Exception:
                parser = _TableRowParser()
            rows.extend(row for row in parser.rows if any(cell.strip() for cell in row))

            if not parser.rows:
                stripped = _WHITESPACE_RE.sub(" ", _TAG_RE.sub(" ", text)).strip()
                if stripped:
                    rows.append([stripped])
    return rows


def _compact(value: str) -> str:
    return re.sub(r"[\sㆍ·・\-_/()]", "", str(value or ""))


def extract_business_purpose_phrases(rows: Iterable[Iterable[str]]) -> list[str]:
    materialized = [list(row) for row in rows]
    phrases: list[str] = []
    markers = (
        "사업목적",
        "사업다각화",
        "신규사업",
        "신성장동력",
        "정관변경",
    )
    for index, row in enumerate(materialized):
        joined = " | ".join(cell.strip() for cell in row if cell.strip())
        compact = _compact(joined)
        if not any(_compact(marker) in compact for marker in markers):
            continue
        for candidate_row in materialized[index : index + 3]:
            phrase = " | ".join(cell.strip() for cell in candidate_row if cell.strip())
            if phrase and phrase not in phrases:
                phrases.append(phrase)
    return phrases[:20]


def parse_number(value: object) -> float | None:
    text = str(value or "").strip()
    if not text or text in {"-", "해당사항없음", "없음", "N/A", "n/a"}:
        return None
    match = _NUMBER_RE.search(text.replace("원", "").replace("주", "").replace("%", ""))
    if match is None:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def extract_financial_snapshot(rows: Iterable[Iterable[str]]) -> dict[str, float]:
    aliases = {
        "assets": ("자산총계", "총자산"),
        "liabilities": ("부채총계", "총부채"),
        "equity": ("자본총계", "자기자본"),
        "revenue": ("매출액", "영업수익"),
        "operating_profit": ("영업이익", "영업손실"),
        "net_income": ("당기순이익", "당기순손실"),
    }
    result: dict[str, float] = {}
    for row in rows:
        cells = [str(cell).strip() for cell in row]
        for metric, labels in aliases.items():
            if metric in result:
                continue
            for cell_index, cell in enumerate(cells):
                compact = _compact(cell)
                if not any(_compact(label) in compact for label in labels):
                    continue
                for candidate in cells[cell_index + 1 :]:
                    number = parse_number(candidate)
                    if number is not None:
                        result[metric] = number
                        break
                break
    return result

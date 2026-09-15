from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from typing import Any, Iterable


_VISUAL_EXTENSIONS = {
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".tif": "image",
    ".tiff": "image",
    ".bmp": "image",
    ".webp": "image",
}
_BLOCK_TAGS = {"p", "div", "li", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6"}


@dataclass(frozen=True)
class _RawCell:
    text: str
    is_header: bool
    rowspan: int = 1
    colspan: int = 1


@dataclass(frozen=True)
class _ExpandedCell:
    text: str
    is_header: bool


class _DebtHtmlParser(HTMLParser):
    """Collect top-level HTML tables while preserving non-table prose separately."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[_RawCell]]] = []
        self.outside_parts: list[str] = []
        self.image_count = 0
        self._table_depth = 0
        self._current_table: list[list[_RawCell]] | None = None
        self._current_row: list[_RawCell] | None = None
        self._in_cell = False
        self._cell_is_header = False
        self._cell_rowspan = 1
        self._cell_colspan = 1
        self._cell_parts: list[str] = []

    @property
    def outside_text(self) -> str:
        text = "".join(self.outside_parts)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text)
        return text.strip()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "img":
            self.image_count += 1
        if tag == "table":
            if self._table_depth == 0:
                self._current_table = []
            self._table_depth += 1
            return
        if self._table_depth == 0:
            if tag in _BLOCK_TAGS:
                self.outside_parts.append("\n")
            return
        if self._table_depth != 1:
            return
        if tag == "tr":
            self._current_row = []
        elif tag in {"td", "th"} and self._current_row is not None:
            self._in_cell = True
            self._cell_is_header = tag == "th"
            self._cell_parts = []
            attr_map = {key.lower(): value for key, value in attrs if value is not None}
            self._cell_rowspan = _safe_span(attr_map.get("rowspan"))
            self._cell_colspan = _safe_span(attr_map.get("colspan"))
        elif tag == "br" and self._in_cell:
            self._cell_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "table":
            if self._table_depth == 1 and self._current_table:
                self.tables.append(self._current_table)
                self._current_table = None
            if self._table_depth > 0:
                self._table_depth -= 1
            return
        if self._table_depth == 0:
            if tag in _BLOCK_TAGS:
                self.outside_parts.append("\n")
            return
        if self._table_depth != 1:
            return
        if tag in {"td", "th"} and self._in_cell and self._current_row is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell_parts)).strip()
            self._current_row.append(
                _RawCell(
                    text=text,
                    is_header=self._cell_is_header,
                    rowspan=self._cell_rowspan,
                    colspan=self._cell_colspan,
                )
            )
            self._in_cell = False
            self._cell_parts = []
        elif tag == "tr" and self._current_row is not None:
            if self._current_row and self._current_table is not None:
                self._current_table.append(self._current_row)
            self._current_row = None

    def handle_data(self, data: str) -> None:
        if self._table_depth == 0:
            self.outside_parts.append(data)
        elif self._in_cell:
            self._cell_parts.append(data)


def _safe_span(value: str | None) -> int:
    try:
        parsed = int(value or "1")
    except ValueError:
        return 1
    return max(1, min(parsed, 20))


def _expand_table(rows: list[list[_RawCell]]) -> tuple[tuple[_ExpandedCell, ...], ...]:
    pending: dict[int, tuple[int, _ExpandedCell]] = {}
    expanded: list[dict[int, _ExpandedCell]] = []
    max_width = 0
    for raw_row in rows:
        row: dict[int, _ExpandedCell] = {}
        for column, (remaining, cell) in list(pending.items()):
            row[column] = cell
            if remaining <= 1:
                pending.pop(column, None)
            else:
                pending[column] = (remaining - 1, cell)

        column = 0
        for raw_cell in raw_row:
            while column in row:
                column += 1
            for _ in range(raw_cell.colspan):
                while column in row:
                    column += 1
                cell = _ExpandedCell(raw_cell.text, raw_cell.is_header)
                row[column] = cell
                if raw_cell.rowspan > 1:
                    pending[column] = (raw_cell.rowspan - 1, cell)
                column += 1
        if row:
            max_width = max(max_width, max(row) + 1)
        expanded.append(row)

    result: list[tuple[_ExpandedCell, ...]] = []
    for row in expanded:
        result.append(
            tuple(row.get(index, _ExpandedCell("", False)) for index in range(max_width))
        )
    return tuple(result)


def _normalize_header(value: str) -> str:
    value = re.sub(r"[^a-z0-9%$]+", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


def _header_field(header: str) -> str | None:
    text = _normalize_header(header)
    if not text:
        return None
    if "cusip" in text:
        return "cusip"
    if "isin" in text:
        return "isin"
    if "maturity" in text or text in {"due", "due date"}:
        return "maturity"
    if "coupon" in text:
        return "coupon"
    if "spread" in text or "margin" in text:
        return "spread"
    if "benchmark" in text or "reference rate" in text:
        return "benchmark"
    if "commitment" in text or "facility size" in text or "capacity" in text:
        return "commitment"
    if "drawn" in text or "borrowed" in text:
        return "drawn"
    if "available" in text or "availability" in text:
        return "available"
    if "principal" in text or "face amount" in text:
        return "principal"
    if "outstanding" in text and "share" not in text:
        return "outstanding"
    if "seniority" in text or "ranking" in text:
        return "seniority"
    if "secured" in text or "security" in text or "collateral" in text:
        return "secured"
    if "currency" in text:
        return "currency"
    if any(
        token in text
        for token in (
            "instrument",
            "debt instrument",
            "debt",
            "security name",
            "security",
            "notes",
            "note",
            "facility",
            "loan",
            "description",
        )
    ):
        return "name"
    if text in {"interest rate", "rate"}:
        return "rate"
    return None


def _header_row_count(grid: tuple[tuple[_ExpandedCell, ...], ...]) -> int:
    count = 0
    for row in grid:
        if any(cell.is_header for cell in row):
            count += 1
            continue
        break
    if count:
        return count
    if grid:
        score = sum(_header_field(cell.text) is not None for cell in grid[0])
        if score >= 2:
            return 1
    return 0


def _column_headers(
    grid: tuple[tuple[_ExpandedCell, ...], ...],
    header_rows: int,
) -> tuple[str, ...]:
    if not grid:
        return ()
    width = max(len(row) for row in grid)
    output: list[str] = []
    for column in range(width):
        parts: list[str] = []
        for row in grid[:header_rows]:
            if column >= len(row):
                continue
            text = row[column].text.strip()
            if text and text not in parts:
                parts.append(text)
        output.append(" / ".join(parts))
    return tuple(output)


def _money_multiplier(header: str) -> float:
    text = _normalize_header(header)
    if "billion" in text or " billions" in f" {text}":
        return 1_000_000_000.0
    if "million" in text or " millions" in f" {text}":
        return 1_000_000.0
    if "thousand" in text or " thousands" in f" {text}":
        return 1_000.0
    return 1.0


def _table_money(module: Any, cell: str, header: str) -> float | None:
    text = cell.strip()
    if not text or text in {"-", "—", "–", "n/a", "N/A"}:
        return None
    if module._MONEY_RE.search(text):
        try:
            return float(module._money_value(text))
        except ValueError:
            return None
    negative = text.startswith("(") and text.endswith(")")
    cleaned = re.sub(r"[^0-9.()-]", "", text)
    cleaned = cleaned.strip("()")
    if not cleaned or cleaned.count(".") > 1:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if negative:
        value *= -1
    return value * _money_multiplier(header)


def _table_percent(cell: str) -> float | None:
    match = re.search(r"(-?[0-9]+(?:\.[0-9]+)?)\s*%", cell)
    if not match:
        return None
    return float(match.group(1))


def _table_maturity(cell: str) -> tuple[str | None, int | None]:
    text = re.sub(r"\s+", " ", cell).strip()
    month_names = (
        "January|February|March|April|May|June|July|August|September|October|November|December"
    )
    match = re.search(
        rf"(?P<month>{month_names})\s+(?P<day>[0-9]{{1,2}}),\s*(?P<year>20[0-9]{{2}})",
        text,
        re.IGNORECASE,
    )
    if match:
        months = {
            name.lower(): index
            for index, name in enumerate(
                (
                    "January", "February", "March", "April", "May", "June",
                    "July", "August", "September", "October", "November", "December",
                ),
                1,
            )
        }
        value = f"{int(match.group('year')):04d}-{months[match.group('month').lower()]:02d}-{int(match.group('day')):02d}"
        return value, int(match.group("year"))
    iso = re.search(r"\b(20[0-9]{2})-([01][0-9])-([0-3][0-9])\b", text)
    if iso:
        return iso.group(0), int(iso.group(1))
    slash = re.search(r"\b([01]?[0-9])/([0-3]?[0-9])/(20[0-9]{2})\b", text)
    if slash:
        return f"{int(slash.group(3)):04d}-{int(slash.group(1)):02d}-{int(slash.group(2)):02d}", int(slash.group(3))
    year = re.search(r"\b(20[0-9]{2})\b", text)
    return (None, int(year.group(1))) if year else (None, None)


def _clean_identifier(cell: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", cell.upper())


def _row_text(headers: tuple[str, ...], values: tuple[str, ...]) -> str:
    parts = []
    for index, value in enumerate(values):
        if not value.strip():
            continue
        header = headers[index] if index < len(headers) else f"column_{index + 1}"
        parts.append(f"{header or f'column_{index + 1}'}={value.strip()}")
    return "TABLE ROW | " + " | ".join(parts)


def _table_row_proposals(
    module: Any,
    *,
    span_id: str,
    headers: tuple[str, ...],
    values: tuple[str, ...],
    document: Any,
) -> tuple[Any, ...]:
    proposals: list[Any] = []
    identity_added = False

    for index, cell in enumerate(values):
        text = cell.strip()
        if not text:
            continue
        header = headers[index] if index < len(headers) else ""
        field = _header_field(header)
        basis = f"same HTML table row column: {header or index + 1}"

        if field == "name":
            if _normalize_header(text) not in {"total", "subtotal", "aggregate", "other"}:
                proposals.append(module.InstrumentFieldProposal("name", text, "high", span_id, basis))
                instrument_type = module._infer_instrument_type(text, text)
                if instrument_type:
                    proposals.append(
                        module.InstrumentFieldProposal(
                            "instrument_type",
                            instrument_type,
                            "high" if module._NOTES_TITLE_RE.search(text) or module._FACILITY_RE.search(text) else "medium",
                            span_id,
                            basis,
                        )
                    )
                identity_added = True
            continue

        note_match = module._NOTES_TITLE_RE.search(text)
        facility_match = module._FACILITY_RE.search(text)
        if not identity_added and (note_match or facility_match):
            name = re.sub(r"\s+", " ", (note_match.group(0) if note_match else facility_match.group("label"))).strip()
            proposals.append(module.InstrumentFieldProposal("name", name, "high", span_id, basis))
            instrument_type = "notes" if note_match else module._infer_instrument_type(name, text)
            if instrument_type:
                proposals.append(module.InstrumentFieldProposal("instrument_type", instrument_type, "high", span_id, basis))
            identity_added = True

        if field in {"principal", "outstanding", "commitment", "drawn", "available"}:
            amount = _table_money(module, text, header)
            if amount is not None:
                target = "principal" if field == "outstanding" else field
                confidence = "medium" if field == "outstanding" else "high"
                proposals.append(module.InstrumentFieldProposal(target, amount, confidence, span_id, basis))
        elif field == "maturity":
            maturity_date, maturity_year = _table_maturity(text)
            if maturity_date:
                proposals.append(module.InstrumentFieldProposal("maturity_date", maturity_date, "high", span_id, basis))
            if maturity_year:
                proposals.append(module.InstrumentFieldProposal("maturity_year", maturity_year, "high", span_id, basis))
        elif field in {"coupon", "rate"}:
            if "sofr" in text.lower() or "libor" in text.lower() or "base rate" in text.lower():
                benchmark = "SOFR" if "sofr" in text.lower() else ("LIBOR" if "libor" in text.lower() else "base_rate")
                proposals.append(module.InstrumentFieldProposal("benchmark", benchmark, "high", span_id, basis))
                spread = module._SOFR_SPREAD_RE.search(text) or module._BPS_SPREAD_RE.search(text)
                if spread:
                    if "basis" in text.lower():
                        spread_bps = float(spread.group("spread"))
                    else:
                        spread_bps = float(spread.group("spread")) * 100.0
                    proposals.append(module.InstrumentFieldProposal("spread_bps", spread_bps, "high", span_id, basis))
            else:
                coupon = _table_percent(text)
                if coupon is not None:
                    proposals.append(
                        module.InstrumentFieldProposal(
                            "coupon_pct",
                            coupon,
                            "high" if field == "coupon" else "medium",
                            span_id,
                            basis,
                        )
                    )
        elif field == "spread":
            bps = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:bps|basis points?)", text, re.IGNORECASE)
            pct = _table_percent(text)
            if bps:
                proposals.append(module.InstrumentFieldProposal("spread_bps", float(bps.group(1)), "high", span_id, basis))
            elif pct is not None:
                proposals.append(module.InstrumentFieldProposal("spread_bps", pct * 100.0, "high", span_id, basis))
        elif field == "benchmark":
            lower = text.lower()
            benchmark = "SOFR" if "sofr" in lower else ("LIBOR" if "libor" in lower else ("base_rate" if "base" in lower else None))
            if benchmark:
                proposals.append(module.InstrumentFieldProposal("benchmark", benchmark, "high", span_id, basis))
        elif field == "cusip":
            value = _clean_identifier(text)
            if len(value) == 9:
                proposals.append(module.InstrumentFieldProposal("cusip", value, "high", span_id, basis))
        elif field == "isin":
            value = _clean_identifier(text)
            if len(value) == 12 and value[:2].isalpha():
                proposals.append(module.InstrumentFieldProposal("isin", value, "high", span_id, basis))
        elif field == "seniority":
            lower = text.lower()
            if "senior" in lower:
                proposals.append(module.InstrumentFieldProposal("seniority", "senior", "high", span_id, basis))
            elif "subordinated" in lower or "junior" in lower:
                proposals.append(module.InstrumentFieldProposal("seniority", "subordinated", "high", span_id, basis))
        elif field == "secured":
            lower = text.lower()
            if "unsecured" in lower or lower in {"no", "n"}:
                proposals.append(module.InstrumentFieldProposal("secured", False, "high", span_id, basis))
            elif "secured" in lower or lower in {"yes", "y"}:
                proposals.append(module.InstrumentFieldProposal("secured", True, "high", span_id, basis))
        elif field == "currency":
            value = text.upper().strip()
            if re.fullmatch(r"[A-Z]{3}", value):
                proposals.append(module.InstrumentFieldProposal("currency", value, "high", span_id, basis))

    meaningful = [item for item in proposals if item.field not in {"name", "instrument_type"}]
    if not identity_added or not meaningful:
        return ()
    return tuple(proposals)


def _extract_table_candidates(
    module: Any,
    parser: _DebtHtmlParser,
    document: Any,
    *,
    max_candidates: int,
) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    spans: list[Any] = []
    candidates: list[Any] = []
    for table_index, raw_table in enumerate(parser.tables, 1):
        grid = _expand_table(raw_table)
        header_rows = _header_row_count(grid)
        if not grid or header_rows == 0:
            continue
        headers = _column_headers(grid, header_rows)
        recognized = {_header_field(header) for header in headers}
        recognized.discard(None)
        if not recognized.intersection({"name", "principal", "outstanding", "commitment", "maturity", "coupon", "rate", "cusip", "isin"}):
            continue

        for row_index, row in enumerate(grid[header_rows:], header_rows + 1):
            values = tuple(cell.text for cell in row)
            if not any(value.strip() for value in values):
                continue
            span_id = f"{document.source_accession}:{document.sequence or 0}:t{table_index}:r{row_index}"
            proposals = _table_row_proposals(
                module,
                span_id=span_id,
                headers=headers,
                values=values,
                document=document,
            )
            if not proposals:
                continue
            span = module.InstrumentSourceSpan(
                span_id=span_id,
                source_accession=document.source_accession,
                published_on=document.filing_date,
                document_url=document.url,
                document_type=document.document_type,
                description=document.description,
                text=_row_text(headers, values),
            )
            template, template_warnings = module._template_for_cluster(document, proposals, (span_id,))
            warnings = [
                "table-row candidate preserves same-row column association; verify table headers and units before ledger ingestion",
                *template_warnings,
            ]
            spans.append(span)
            candidates.append(
                module.DebtInstrumentSourceCandidate(
                    candidate_id=f"{document.source_accession}:{document.sequence or 0}:t{table_index}:r{row_index}",
                    source_accession=document.source_accession,
                    as_of_date=document.filing_date,
                    document_url=document.url,
                    document_type=document.document_type,
                    document_description=document.description,
                    proposals=tuple(proposals),
                    source_span_ids=(span_id,),
                    snapshot_template=template,
                    warnings=tuple(dict.fromkeys(warnings)),
                    cluster_status="table_row",
                    cluster_basis=("same_html_table_row", "header_mapped_columns"),
                )
            )
            if len(candidates) >= max_candidates:
                return tuple(spans), tuple(candidates)
    return tuple(spans), tuple(candidates)


def _visual_media_type(document: Any) -> str | None:
    name = str(getattr(document, "document", "") or "").lower()
    for suffix, media_type in _VISUAL_EXTENSIONS.items():
        if name.endswith(suffix):
            return media_type
    if str(getattr(document, "document_type", "") or "").upper().startswith("GRAPHIC"):
        return "image"
    return None


def _image_heavy_html(parser: _DebtHtmlParser) -> bool:
    visible = len(re.sub(r"\s+", "", parser.outside_text))
    table_text = sum(
        len(re.sub(r"\s+", "", cell.text))
        for table in parser.tables
        for row in table
        for cell in row
    )
    return parser.image_count > 0 and visible + table_text < 160


def _visual_warning(document: Any, media_type: str, reason: str) -> str:
    return (
        "VISUAL_EXTRACTION_REQUIRED"
        f"|accession={document.source_accession}"
        f"|document={document.document}"
        f"|document_type={document.document_type}"
        f"|media_type={media_type}"
        f"|url={document.url}"
        f"|reason={reason}"
    )


def install_structured_debt_extraction(module: Any) -> None:
    """Install table-aware extraction before orchestration imports bind helpers.

    This is kept in a separate module so table parsing / media deferral logic stays
    isolated from the already-large SEC navigation module. Installation is idempotent.
    """

    if getattr(module, "_structured_debt_extraction_installed", False):
        return

    original_extract = module.extract_source_candidates

    def structured_extract_source_candidates(
        document_text: str,
        document: Any,
        *,
        max_candidates: int = 20,
    ) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
        if max_candidates <= 0:
            return (), ()
        if _visual_media_type(document):
            return (), ()

        parser = _DebtHtmlParser()
        try:
            parser.feed(document_text)
            parser.close()
        except Exception:
            return original_extract(document_text, document, max_candidates=max_candidates)

        table_spans, table_candidates = _extract_table_candidates(
            module,
            parser,
            document,
            max_candidates=max_candidates,
        )
        remaining = max(0, max_candidates - len(table_candidates))
        if remaining == 0:
            return table_spans, table_candidates

        prose_source = parser.outside_text if parser.tables else document_text
        prose_spans, prose_candidates = original_extract(
            prose_source,
            document,
            max_candidates=remaining,
        )
        return tuple(table_spans) + tuple(prose_spans), tuple(table_candidates) + tuple(prose_candidates)

    def structured_build_sec_instrument_packet(
        client: Any,
        *,
        ticker: str | None,
        company_name: str,
        analysis_date: Any,
        filings: Iterable[Any],
        filing_limit: int = 6,
        max_exhibits_per_filing: int = 8,
        max_candidates_per_exhibit: int = 20,
    ) -> Any:
        documents: list[Any] = []
        spans: list[Any] = []
        candidates: list[Any] = []
        warnings: list[str] = [
            "SEC exhibit field proposals are deterministic extraction candidates, not confirmed debt schedule rows.",
            "Only source-verified snapshots should be passed into the stable debt-instrument ledger.",
        ]
        selected_filings = [
            filing
            for filing in filings
            if filing.filing_date <= analysis_date
            and filing.form in {"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A"}
        ][: max(filing_limit, 0)]

        for filing in selected_filings:
            try:
                index_html = module._get_text(client, module.filing_index_url(filing))
                filing_documents = module.parse_filing_documents(index_html, filing)
                selected_documents = module.select_debt_documents(
                    filing_documents,
                    limit=max_exhibits_per_filing,
                )
                documents.extend(selected_documents)
            except Exception as exc:
                warnings.append(f"failed to enumerate exhibits for {filing.accession_number}: {exc}")
                continue

            for document in selected_documents:
                media_type = _visual_media_type(document)
                if media_type:
                    warnings.append(
                        _visual_warning(
                            document,
                            media_type,
                            "binary_visual_exhibit_skipped_to_avoid_unstructured_text_association",
                        )
                    )
                    continue
                try:
                    document_html = module._get_text(client, document.url)
                    parser = _DebtHtmlParser()
                    parser.feed(document_html)
                    parser.close()
                    if _image_heavy_html(parser):
                        warnings.append(
                            _visual_warning(
                                document,
                                "image_heavy_html",
                                "visible_text_too_sparse_for_safe_automatic_debt_extraction",
                            )
                        )
                        continue
                    extracted_spans, extracted_candidates = structured_extract_source_candidates(
                        document_html,
                        document,
                        max_candidates=max_candidates_per_exhibit,
                    )
                    spans.extend(extracted_spans)
                    candidates.extend(extracted_candidates)
                except Exception as exc:
                    warnings.append(f"failed to extract debt fields from {document.document}: {exc}")

        return module.SecInstrumentPacket(
            ticker=ticker,
            company_name=company_name,
            analysis_date=analysis_date,
            documents=tuple(documents),
            spans=tuple(spans),
            candidates=tuple(candidates),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    structured_extract_source_candidates.__name__ = "extract_source_candidates"
    structured_build_sec_instrument_packet.__name__ = "build_sec_instrument_packet"
    module.extract_source_candidates = structured_extract_source_candidates
    module.build_sec_instrument_packet = structured_build_sec_instrument_packet
    module._structured_debt_extraction_installed = True

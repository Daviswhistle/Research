from __future__ import annotations

from dataclasses import dataclass, replace
from html import unescape
import re
from typing import Any, Iterable

from .structured_debt_extraction import (
    _DebtHtmlParser,
    _header_field,
    _normalize_header,
    _row_text,
    _table_row_proposals,
    _visual_media_type,
)


_MATERIAL_FIELDS = {
    "principal",
    "outstanding",
    "commitment",
    "drawn",
    "available",
}
_TERM_FIELDS = {
    "maturity",
    "coupon",
    "rate",
    "spread",
    "benchmark",
    "cusip",
    "isin",
    "seniority",
    "secured",
    "currency",
}
_TRANSPOSED_FIELD_SET = _MATERIAL_FIELDS | _TERM_FIELDS
_AGGREGATE_RE = re.compile(r"^(?:total|subtotal|aggregate)(?:\b|\s)", re.IGNORECASE)
_MARKER_TOKEN = r"(?:\[[0-9A-Za-z]{1,2}\]|\([0-9A-Za-z]{1,2}\)|\*{1,3}|†|‡)"
_MARKER_RE = re.compile(_MARKER_TOKEN)
_TRAILING_MARKERS_RE = re.compile(rf"(?:\s*{_MARKER_TOKEN})+\s*$")
_DEFINITION_RE = re.compile(
    rf"^\s*(?P<marker>{_MARKER_TOKEN})\s*[.:;-]?\s*(?P<body>.+?)\s*$",
    re.IGNORECASE,
)
_BARE_TABLE_DEFINITION_RE = re.compile(
    r"^\s*(?P<marker>[0-9]{1,2})[.)]\s+(?P<body>.+?)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _GridCell:
    text: str
    is_header: bool
    origin_row: int
    origin_cell: int
    from_rowspan: bool = False
    from_colspan: bool = False


@dataclass(frozen=True)
class _FootnoteDefinition:
    marker: str
    text: str
    span_id: str
    source: str


@dataclass(frozen=True)
class _TableExtraction:
    spans: tuple[Any, ...]
    candidates: tuple[Any, ...]


def _annotate_superscripts(document_text: str) -> str:
    """Make simple HTML superscript footnote markers visible to the table parser."""

    def repl(match: re.Match[str]) -> str:
        inner = re.sub(r"<[^>]+>", "", match.group(1))
        inner = re.sub(r"\s+", " ", unescape(inner)).strip()
        if re.fullmatch(r"[0-9A-Za-z]{1,2}|\*{1,3}|†|‡", inner):
            return f" [{inner}] "
        return f" {inner} "

    return re.sub(r"<sup\b[^>]*>(.*?)</sup>", repl, document_text, flags=re.IGNORECASE | re.DOTALL)


def _canonical_marker(value: str) -> str:
    token = value.strip()
    if token.startswith("[") and token.endswith("]"):
        token = token[1:-1]
    elif token.startswith("(") and token.endswith(")"):
        token = token[1:-1]
    return token.strip().lower()


def _extract_markers(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_canonical_marker(match.group(0)) for match in _MARKER_RE.finditer(value)))


def _strip_trailing_markers(value: str) -> str:
    return _TRAILING_MARKERS_RE.sub("", value).strip()


def _parse_definition(value: str, *, allow_bare_number: bool = False) -> tuple[str, str] | None:
    clean = re.sub(r"\s+", " ", value).strip()
    match = _DEFINITION_RE.match(clean)
    if match:
        body = match.group("body").strip()
        if len(body) >= 4:
            return _canonical_marker(match.group("marker")), body
    if allow_bare_number:
        match = _BARE_TABLE_DEFINITION_RE.match(clean)
        if match:
            body = match.group("body").strip()
            if len(body) >= 4:
                return match.group("marker").lower(), body
    return None


def _expand_with_provenance(rows: list[list[Any]]) -> tuple[tuple[_GridCell, ...], ...]:
    pending: dict[int, tuple[int, _GridCell]] = {}
    expanded: list[dict[int, _GridCell]] = []
    max_width = 0
    for row_index, raw_row in enumerate(rows):
        row: dict[int, _GridCell] = {}
        for column, (remaining, base) in list(pending.items()):
            row[column] = replace(base, from_rowspan=True)
            if remaining <= 1:
                pending.pop(column, None)
            else:
                pending[column] = (remaining - 1, base)

        column = 0
        for raw_cell_index, raw_cell in enumerate(raw_row):
            while column in row:
                column += 1
            for _ in range(raw_cell.colspan):
                while column in row:
                    column += 1
                cell = _GridCell(
                    text=raw_cell.text,
                    is_header=raw_cell.is_header,
                    origin_row=row_index,
                    origin_cell=raw_cell_index,
                    from_colspan=raw_cell.colspan > 1,
                )
                row[column] = cell
                if raw_cell.rowspan > 1:
                    pending[column] = (raw_cell.rowspan - 1, cell)
                column += 1
        if row:
            max_width = max(max_width, max(row) + 1)
        expanded.append(row)

    return tuple(
        tuple(
            row.get(index, _GridCell("", False, row_index, -1))
            for index in range(max_width)
        )
        for row_index, row in enumerate(expanded)
    )


def _source_span(module: Any, document: Any, span_id: str, text: str) -> Any:
    return module.InstrumentSourceSpan(
        span_id=span_id,
        source_accession=document.source_accession,
        published_on=document.filing_date,
        document_url=document.url,
        document_type=document.document_type,
        description=document.description,
        text=text,
    )


def _row_unique_texts(row: tuple[_GridCell, ...]) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[tuple[int, int]] = set()
    for cell in row:
        key = (cell.origin_row, cell.origin_cell)
        if cell.origin_cell < 0 or key in seen:
            continue
        seen.add(key)
        text = cell.text.strip()
        if text:
            output.append(text)
    return tuple(output)


def _row_is_caption(row: tuple[_GridCell, ...]) -> bool:
    texts = _row_unique_texts(row)
    if not texts:
        return False
    if len(texts) != 1:
        return False
    return _header_field(texts[0]) is None


def _semantic_fields(row: tuple[_GridCell, ...]) -> tuple[str, ...]:
    return tuple(field for cell in row if (field := _header_field(cell.text)) is not None)


def _standard_header_row_count(grid: tuple[tuple[_GridCell, ...], ...]) -> int:
    if not grid:
        return 0
    semantic_start: int | None = None
    for index, row in enumerate(grid[:4]):
        fields = set(_semantic_fields(row))
        if len(fields) >= 2:
            semantic_start = index
            break
        if not _row_is_caption(row):
            break
    if semantic_start is None:
        return 0

    count = semantic_start + 1
    for row in grid[count:]:
        nonempty = [cell for cell in row if cell.text.strip()]
        fields = set(_semantic_fields(row))
        all_header = bool(nonempty) and all(cell.is_header for cell in nonempty)
        if len(fields) >= 2 or (all_header and bool(fields)):
            count += 1
            continue
        break
    return count


def _column_headers(grid: tuple[tuple[_GridCell, ...], ...], header_rows: int) -> tuple[str, ...]:
    if not grid:
        return ()
    output: list[str] = []
    for column in range(len(grid[0])):
        parts: list[str] = []
        for row in grid[:header_rows]:
            text = row[column].text.strip()
            if text and text not in parts:
                parts.append(text)
        output.append(" / ".join(parts))
    return tuple(output)


def _aggregate_label(value: str) -> bool:
    text = _normalize_header(_strip_trailing_markers(value))
    return bool(text and _AGGREGATE_RE.match(text))


def _row_looks_aggregate(row: tuple[_GridCell, ...]) -> bool:
    texts = _row_unique_texts(row)
    return bool(texts and any(_aggregate_label(text) for text in texts[:2]))


def _row_has_terms(row: tuple[_GridCell, ...], headers: tuple[str, ...]) -> bool:
    for index, cell in enumerate(row):
        if not cell.text.strip():
            continue
        field = _header_field(headers[index] if index < len(headers) else "")
        if field and field != "name":
            return True
    return False


def _identity_score(module: Any, value: str) -> int:
    text = _strip_trailing_markers(re.sub(r"\s+", " ", value).strip())
    if not text or _aggregate_label(text):
        return 0
    if module._NOTES_TITLE_RE.search(text):
        return 100
    if module._FACILITY_RE.search(text):
        return 95
    normalized = _normalize_header(text)
    if re.search(r"\b(?:notes?|loans?|facility|revolver|term loan)\b", normalized):
        return 70 if re.search(r"\d", normalized) else 55
    return 45 if _header_field(text) == "name" and normalized not in {"instrument", "debt", "security", "notes", "facility", "loan"} else 0


def _instrument_header_for_column(
    module: Any,
    grid: tuple[tuple[_GridCell, ...], ...],
    column: int,
    first_field_row: int,
) -> tuple[str, tuple[str, ...]] | None:
    candidates: list[tuple[int, int, str]] = []
    markers: list[str] = []
    for row_index in range(first_field_row):
        if column >= len(grid[row_index]):
            continue
        text = grid[row_index][column].text.strip()
        if not text:
            continue
        markers.extend(_extract_markers(text))
        score = _identity_score(module, text)
        if score:
            candidates.append((score, row_index, text))
    if not candidates:
        return None
    _, _, text = max(candidates, key=lambda item: (item[0], item[1]))
    return _strip_trailing_markers(text), tuple(dict.fromkeys(markers))


def _transposed_spec(
    module: Any,
    grid: tuple[tuple[_GridCell, ...], ...],
) -> tuple[int, tuple[tuple[int, str], ...], tuple[tuple[int, str, tuple[str, ...]], ...]] | None:
    if not grid or len(grid[0]) < 3:
        return None
    field_rows: list[tuple[int, str]] = []
    for row_index, row in enumerate(grid):
        if not row:
            continue
        field = _header_field(row[0].text)
        if field in _TRANSPOSED_FIELD_SET:
            field_rows.append((row_index, field))
    distinct_fields = {field for _, field in field_rows}
    if len(distinct_fields) < 2:
        return None
    if not (distinct_fields & _MATERIAL_FIELDS and distinct_fields & _TERM_FIELDS) and len(distinct_fields) < 3:
        return None
    first_field_row = min(index for index, _ in field_rows)
    if first_field_row == 0:
        return None

    instrument_columns: list[tuple[int, str, tuple[str, ...]]] = []
    for column in range(1, len(grid[0])):
        identity = _instrument_header_for_column(module, grid, column, first_field_row)
        if identity:
            instrument_columns.append((column, identity[0], identity[1]))
    names = [_normalize_header(item[1]) for item in instrument_columns]
    if len(instrument_columns) < 1 or len(set(names)) != len(names):
        return None
    return first_field_row, tuple(field_rows), tuple(instrument_columns)


def _definition_from_raw_row(raw_row: list[Any]) -> tuple[str, str] | None:
    texts = [cell.text.strip() for cell in raw_row if cell.text.strip()]
    if not texts:
        return None
    combined = " ".join(texts)
    definition = _parse_definition(combined, allow_bare_number=True)
    if not definition:
        return None
    first_only_marker = bool(re.fullmatch(rf"\s*{_MARKER_TOKEN}\s*", texts[0]))
    if len(raw_row) == 1 or any(cell.colspan > 1 for cell in raw_row) or first_only_marker:
        return definition
    return None


def _table_footnotes(
    raw_table: list[list[Any]],
    document: Any,
    table_index: int,
) -> tuple[dict[str, list[_FootnoteDefinition]], set[int]]:
    definitions: dict[str, list[_FootnoteDefinition]] = {}
    footnote_rows: set[int] = set()
    for row_index, raw_row in enumerate(raw_table):
        parsed = _definition_from_raw_row(raw_row)
        if not parsed:
            continue
        marker, body = parsed
        footnote_rows.add(row_index)
        definition = _FootnoteDefinition(
            marker=marker,
            text=f"TABLE FOOTNOTE | marker={marker} | {body}",
            span_id=f"{document.source_accession}:{document.sequence or 0}:t{table_index}:fn{row_index + 1}",
            source="table",
        )
        definitions.setdefault(marker, []).append(definition)
    return definitions, footnote_rows


def _outside_footnotes(parser: _DebtHtmlParser, document: Any) -> dict[str, list[_FootnoteDefinition]]:
    definitions: dict[str, list[_FootnoteDefinition]] = {}
    ordinal = 0
    for line in parser.outside_text.splitlines():
        parsed = _parse_definition(line, allow_bare_number=False)
        if not parsed:
            continue
        ordinal += 1
        marker, body = parsed
        definition = _FootnoteDefinition(
            marker=marker,
            text=f"DOCUMENT FOOTNOTE | marker={marker} | {body}",
            span_id=f"{document.source_accession}:{document.sequence or 0}:fnx{ordinal}",
            source="outside_table",
        )
        definitions.setdefault(marker, []).append(definition)
    return definitions


def _merge_definitions(
    table_defs: dict[str, list[_FootnoteDefinition]],
    outside_defs: dict[str, list[_FootnoteDefinition]],
) -> dict[str, list[_FootnoteDefinition]]:
    output = {key: list(value) for key, value in table_defs.items()}
    for key, rows in outside_defs.items():
        output.setdefault(key, []).extend(rows)
    return output


def _linked_footnotes(
    markers: Iterable[str],
    definitions: dict[str, list[_FootnoteDefinition]],
) -> tuple[tuple[_FootnoteDefinition, ...], tuple[str, ...]]:
    linked: list[_FootnoteDefinition] = []
    warnings: list[str] = []
    for marker in tuple(dict.fromkeys(markers)):
        matches = definitions.get(marker, [])
        unique = {item.span_id: item for item in matches}
        if len(unique) == 1:
            definition = next(iter(unique.values()))
            linked.append(definition)
            warnings.append(
                f"linked footnote marker {marker!r}; reviewer must consider the linked footnote before ledger ingestion"
            )
        elif not unique:
            warnings.append(f"footnote marker {marker!r} has no uniquely resolved definition in deterministic table context")
        else:
            warnings.append(f"footnote marker {marker!r} has multiple possible definitions; left unresolved")
    return tuple(linked), tuple(warnings)


def _candidate_from_proposals(
    module: Any,
    document: Any,
    *,
    candidate_id: str,
    proposals: tuple[Any, ...],
    evidence_spans: tuple[Any, ...],
    warnings: Iterable[str],
    cluster_status: str,
    cluster_basis: tuple[str, ...],
) -> Any | None:
    if not proposals or not evidence_spans:
        return None
    source_span_ids = tuple(dict.fromkeys(span.span_id for span in evidence_spans))
    template, template_warnings = module._template_for_cluster(document, proposals, source_span_ids)
    if not template.get("name"):
        return None
    return module.DebtInstrumentSourceCandidate(
        candidate_id=candidate_id,
        source_accession=document.source_accession,
        as_of_date=document.filing_date,
        document_url=document.url,
        document_type=document.document_type,
        document_description=document.description,
        proposals=proposals,
        source_span_ids=source_span_ids,
        snapshot_template=template,
        warnings=tuple(dict.fromkeys([*warnings, *template_warnings])),
        cluster_status=cluster_status,
        cluster_basis=cluster_basis,
    )


def _standard_groups(
    grid: tuple[tuple[_GridCell, ...], ...],
    headers: tuple[str, ...],
    header_rows: int,
    footnote_rows: set[int],
) -> tuple[tuple[int, ...], ...]:
    name_columns = [index for index, header in enumerate(headers) if _header_field(header) == "name"]
    if len(name_columns) != 1:
        return tuple(
            (row_index,)
            for row_index in range(header_rows, len(grid))
            if row_index not in footnote_rows and not _row_looks_aggregate(grid[row_index])
        )

    name_column = name_columns[0]
    groups: list[tuple[int, ...]] = []
    current: list[int] = []
    current_identity: str | None = None
    for row_index in range(header_rows, len(grid)):
        if row_index in footnote_rows:
            continue
        row = grid[row_index]
        if _row_looks_aggregate(row):
            if current:
                groups.append(tuple(current))
                current = []
                current_identity = None
            continue
        name_cell = row[name_column]
        name_text = _strip_trailing_markers(name_cell.text.strip())
        if name_text:
            normalized = _normalize_header(name_text)
            if name_cell.from_rowspan and current and normalized == current_identity:
                current.append(row_index)
                continue
            if current:
                groups.append(tuple(current))
            current = [row_index]
            current_identity = normalized
            continue
        if current and _row_has_terms(row, headers):
            current.append(row_index)
        elif current:
            groups.append(tuple(current))
            current = []
            current_identity = None
    if current:
        groups.append(tuple(current))
    return tuple(groups)


def _standard_candidate(
    module: Any,
    document: Any,
    *,
    table_index: int,
    grid: tuple[tuple[_GridCell, ...], ...],
    headers: tuple[str, ...],
    group: tuple[int, ...],
    definitions: dict[str, list[_FootnoteDefinition]],
) -> tuple[tuple[Any, ...], Any | None]:
    name_columns = [index for index, header in enumerate(headers) if _header_field(header) == "name"]
    identity = ""
    if len(name_columns) == 1:
        for row_index in group:
            value = _strip_trailing_markers(grid[row_index][name_columns[0]].text.strip())
            if value:
                identity = value
                break

    spans: list[Any] = []
    proposals: list[Any] = []
    markers: list[str] = []
    for row_index in group:
        raw_values = tuple(cell.text for cell in grid[row_index])
        markers.extend(marker for value in raw_values for marker in _extract_markers(value))
        sanitized = [_strip_trailing_markers(value) for value in raw_values]
        if identity and len(name_columns) == 1 and not sanitized[name_columns[0]].strip():
            sanitized[name_columns[0]] = identity
        span_id = f"{document.source_accession}:{document.sequence or 0}:t{table_index}:r{row_index + 1}"
        row_proposals = _table_row_proposals(
            module,
            span_id=span_id,
            headers=headers,
            values=tuple(sanitized),
            document=document,
        )
        proposals.extend(row_proposals)
        spans.append(
            _source_span(
                module,
                document,
                span_id,
                _row_text(headers, raw_values),
            )
        )

    if not proposals:
        return tuple(spans), None
    proposals_tuple = module._unique_proposals(proposals)
    linked, footnote_warnings = _linked_footnotes(markers, definitions)
    linked_spans = [
        _source_span(module, document, item.span_id, item.text)
        for item in linked
    ]
    status = "table_row" if len(group) == 1 else "table_row_group"
    if len(group) == 1:
        candidate_id = f"{document.source_accession}:{document.sequence or 0}:t{table_index}:r{group[0] + 1}"
        basis = ("same_html_table_row", "header_mapped_columns")
        base_warnings = [
            "table-row candidate preserves same-row column association; verify table headers and units before ledger ingestion"
        ]
    else:
        candidate_id = (
            f"{document.source_accession}:{document.sequence or 0}:t{table_index}:"
            f"g{group[0] + 1}-{group[-1] + 1}"
        )
        basis = ("contiguous_html_table_rows", "shared_or_inherited_instrument_identity", "header_mapped_columns")
        base_warnings = [
            "multi-row candidate combines only contiguous rows sharing a blank or rowspan-inherited instrument identity; conflicting high-confidence fields remain unresolved"
        ]
    candidate = _candidate_from_proposals(
        module,
        document,
        candidate_id=candidate_id,
        proposals=proposals_tuple,
        evidence_spans=tuple([*spans, *linked_spans]),
        warnings=(*base_warnings, *footnote_warnings),
        cluster_status=status,
        cluster_basis=basis,
    )
    return tuple([*spans, *linked_spans]), candidate


def _transposed_candidates(
    module: Any,
    document: Any,
    *,
    table_index: int,
    grid: tuple[tuple[_GridCell, ...], ...],
    field_rows: tuple[tuple[int, str], ...],
    instrument_columns: tuple[tuple[int, str, tuple[str, ...]], ...],
    definitions: dict[str, list[_FootnoteDefinition]],
) -> _TableExtraction:
    span_by_id: dict[str, Any] = {}
    candidates: list[Any] = []
    for column, identity, identity_markers in instrument_columns:
        header_span_id = f"{document.source_accession}:{document.sequence or 0}:t{table_index}:c{column + 1}:h"
        header_span = _source_span(
            module,
            document,
            header_span_id,
            f"TRANSPOSED TABLE COLUMN HEADER | column={column + 1} | instrument={identity}",
        )
        span_by_id[header_span_id] = header_span
        name_proposals: list[Any] = [
            module.InstrumentFieldProposal(
                "name",
                identity,
                "high",
                header_span_id,
                "transposed HTML table instrument column header",
            )
        ]
        instrument_type = module._infer_instrument_type(identity, identity)
        if instrument_type:
            name_proposals.append(
                module.InstrumentFieldProposal(
                    "instrument_type",
                    instrument_type,
                    "high" if module._NOTES_TITLE_RE.search(identity) or module._FACILITY_RE.search(identity) else "medium",
                    header_span_id,
                    "transposed HTML table instrument column header",
                )
            )

        proposals: list[Any] = list(name_proposals)
        evidence_spans: list[Any] = [header_span]
        markers: list[str] = list(identity_markers)
        for row_index, _ in field_rows:
            label = grid[row_index][0].text.strip()
            value = grid[row_index][column].text.strip() if column < len(grid[row_index]) else ""
            if not value:
                continue
            markers.extend(_extract_markers(value))
            span_id = f"{document.source_accession}:{document.sequence or 0}:t{table_index}:c{column + 1}:r{row_index + 1}"
            field_span = _source_span(
                module,
                document,
                span_id,
                f"TRANSPOSED TABLE FIELD | instrument={identity} | {label}={value}",
            )
            span_by_id[span_id] = field_span
            evidence_spans.append(field_span)
            parsed = _table_row_proposals(
                module,
                span_id=span_id,
                headers=("Instrument", label),
                values=(identity, _strip_trailing_markers(value)),
                document=document,
            )
            proposals.extend(item for item in parsed if item.field not in {"name", "instrument_type"})

        meaningful = [item for item in proposals if item.field not in {"name", "instrument_type"}]
        if not meaningful:
            continue
        linked, footnote_warnings = _linked_footnotes(markers, definitions)
        for item in linked:
            if item.span_id not in span_by_id:
                span_by_id[item.span_id] = _source_span(module, document, item.span_id, item.text)
            evidence_spans.append(span_by_id[item.span_id])
        candidate = _candidate_from_proposals(
            module,
            document,
            candidate_id=f"{document.source_accession}:{document.sequence or 0}:t{table_index}:c{column + 1}",
            proposals=module._unique_proposals(proposals),
            evidence_spans=tuple(evidence_spans),
            warnings=(
                "transposed table candidate maps first-column field labels across one instrument column; verify orientation, units, and linked footnotes before ledger ingestion",
                *footnote_warnings,
            ),
            cluster_status="table_column",
            cluster_basis=("transposed_html_table", "field_labels_in_first_column", "same_html_table_column"),
        )
        if candidate:
            candidates.append(candidate)
    return _TableExtraction(tuple(span_by_id.values()), tuple(candidates))


def _extract_complex_tables(
    module: Any,
    parser: _DebtHtmlParser,
    document: Any,
    *,
    max_candidates: int,
) -> _TableExtraction:
    span_by_id: dict[str, Any] = {}
    candidates: list[Any] = []
    outside_defs = _outside_footnotes(parser, document)

    for table_index, raw_table in enumerate(parser.tables, 1):
        if len(candidates) >= max_candidates:
            break
        grid = _expand_with_provenance(raw_table)
        if not grid:
            continue
        table_defs, footnote_rows = _table_footnotes(raw_table, document, table_index)
        definitions = _merge_definitions(table_defs, outside_defs)
        transposed = _transposed_spec(module, grid)
        if transposed:
            _, field_rows, instrument_columns = transposed
            result = _transposed_candidates(
                module,
                document,
                table_index=table_index,
                grid=grid,
                field_rows=field_rows,
                instrument_columns=instrument_columns,
                definitions=definitions,
            )
            for span in result.spans:
                span_by_id[span.span_id] = span
            for candidate in result.candidates:
                if len(candidates) >= max_candidates:
                    break
                candidates.append(candidate)
            continue

        header_rows = _standard_header_row_count(grid)
        if header_rows == 0:
            continue
        headers = _column_headers(grid, header_rows)
        recognized = {_header_field(header) for header in headers}
        recognized.discard(None)
        if not recognized.intersection({"name", "principal", "outstanding", "commitment", "maturity", "coupon", "rate", "cusip", "isin"}):
            continue
        groups = _standard_groups(grid, headers, header_rows, footnote_rows)
        for group in groups:
            if len(candidates) >= max_candidates:
                break
            spans, candidate = _standard_candidate(
                module,
                document,
                table_index=table_index,
                grid=grid,
                headers=headers,
                group=group,
                definitions=definitions,
            )
            for span in spans:
                span_by_id[span.span_id] = span
            if candidate:
                candidates.append(candidate)

    return _TableExtraction(tuple(span_by_id.values()), tuple(candidates))


def install_complex_table_extraction(module: Any) -> None:
    """Replace row-only HTML table extraction with orientation-aware conservative parsing."""

    if getattr(module, "_complex_table_debt_extraction_installed", False):
        return
    original_extract = module.extract_source_candidates

    def complex_extract_source_candidates(
        document_text: str,
        document: Any,
        *,
        max_candidates: int = 20,
    ) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
        if max_candidates <= 0:
            return (), ()
        if _visual_media_type(document):
            return original_extract(document_text, document, max_candidates=max_candidates)

        annotated = _annotate_superscripts(document_text)
        parser = _DebtHtmlParser()
        try:
            parser.feed(annotated)
            parser.close()
        except Exception:
            return original_extract(document_text, document, max_candidates=max_candidates)
        if not parser.tables:
            return original_extract(document_text, document, max_candidates=max_candidates)

        table_result = _extract_complex_tables(
            module,
            parser,
            document,
            max_candidates=max_candidates,
        )
        if not table_result.candidates:
            return original_extract(document_text, document, max_candidates=max_candidates)

        remaining = max(0, max_candidates - len(table_result.candidates))
        if remaining == 0:
            return table_result.spans, table_result.candidates
        prose_spans, prose_candidates = original_extract(
            parser.outside_text,
            document,
            max_candidates=remaining,
        )
        span_by_id = {span.span_id: span for span in (*table_result.spans, *prose_spans)}
        return tuple(span_by_id.values()), tuple(table_result.candidates) + tuple(prose_candidates)

    complex_extract_source_candidates.__name__ = "extract_source_candidates"
    module.extract_source_candidates = complex_extract_source_candidates
    module._complex_table_debt_extraction_installed = True

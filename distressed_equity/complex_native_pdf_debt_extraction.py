from __future__ import annotations

import re
from typing import Any, Iterable

from .complex_table_debt_extraction import (
    _MATERIAL_FIELDS,
    _TERM_FIELDS,
    _aggregate_label,
    _extract_markers,
    _identity_score,
    _linked_footnotes,
    _parse_definition,
    _source_span,
    _strip_trailing_markers,
    _FootnoteDefinition,
)
from .structured_debt_extraction import _header_field, _normalize_header, _table_row_proposals


_TRANSPOSED_FIELDS = _MATERIAL_FIELDS | _TERM_FIELDS


def _page_footnotes(native: Any, rows: tuple[Any, ...], document: Any) -> dict[int, dict[str, list[_FootnoteDefinition]]]:
    output: dict[int, dict[str, list[_FootnoteDefinition]]] = {}
    for row_index, row in enumerate(rows, 1):
        parsed = _parse_definition(row.text, allow_bare_number=True)
        if not parsed:
            continue
        marker, body = parsed
        definition = _FootnoteDefinition(
            marker=marker,
            text=f"PDF PAGE FOOTNOTE | page={row.page_number} | marker={marker} | {body}",
            span_id=f"{document.source_accession}:{document.sequence or 0}:p{row.page_number}:fn{row_index}",
            source="native_pdf",
        )
        output.setdefault(row.page_number, {}).setdefault(marker, []).append(definition)
    return output


def _column_value(row: Any, xs: tuple[float, ...], column: int) -> str:
    if not xs:
        return ""
    boundaries = [(xs[index] + xs[index + 1]) / 2.0 for index in range(len(xs) - 1)]
    values: list[list[str]] = [[] for _ in xs]
    for fragment in row.fragments:
        target = 0
        while target < len(boundaries) and fragment.x >= boundaries[target]:
            target += 1
        values[target].append(fragment.text)
    return " ".join(values[column]).strip()


def _field_row(row: Any) -> tuple[str, str] | None:
    if not row.fragments:
        return None
    left = min(row.fragments, key=lambda item: item.x)
    field = _header_field(left.text)
    if field not in _TRANSPOSED_FIELDS:
        return None
    return left.text, field


def _transposed_header(native: Any, rows: list[Any], first_field_index: int, module: Any) -> tuple[Any, tuple[tuple[float, str], ...]] | None:
    for header_index in range(first_field_index - 1, max(-1, first_field_index - 4), -1):
        row = rows[header_index]
        identities: list[tuple[float, str]] = []
        for fragment in row.fragments:
            text = _strip_trailing_markers(fragment.text)
            if _header_field(text) is not None:
                continue
            if _identity_score(module, text) >= 55:
                identities.append((fragment.x, text))
        identities.sort(key=lambda item: item[0])
        if identities:
            normalized = [_normalize_header(text) for _, text in identities]
            if len(normalized) == len(set(normalized)):
                return row, tuple(identities)
    return None


def _transposed_page(
    native: Any,
    module: Any,
    page_rows: list[Any],
    document: Any,
    definitions: dict[str, list[_FootnoteDefinition]],
    *,
    max_candidates: int,
) -> tuple[tuple[Any, ...], tuple[Any, ...]] | None:
    field_rows = [(index, *_field_row(row)) for index, row in enumerate(page_rows) if _field_row(row)]
    if not field_rows:
        return None
    distinct = {field for _, _, field in field_rows}
    if len(distinct) < 2:
        return None
    if not (distinct & _MATERIAL_FIELDS and distinct & _TERM_FIELDS) and len(distinct) < 3:
        return None
    first_field_index = min(index for index, _, _ in field_rows)
    if first_field_index <= 0:
        return None
    header = _transposed_header(native, page_rows, first_field_index, module)
    if not header:
        return None
    header_row, identities = header
    instrument_xs = tuple(x for x, _ in identities)
    if not instrument_xs:
        return None

    span_by_id: dict[str, Any] = {}
    candidates: list[Any] = []
    for instrument_index, (instrument_x, identity) in enumerate(identities):
        if len(candidates) >= max_candidates:
            break
        header_span_id = (
            f"{document.source_accession}:{document.sequence or 0}:"
            f"p{header_row.page_number}:c{instrument_index + 1}:h"
        )
        header_span = _source_span(
            module,
            document,
            header_span_id,
            f"PDF TRANSPOSED COLUMN HEADER | page={header_row.page_number} | x={instrument_x:.2f} | instrument={identity}",
        )
        span_by_id[header_span_id] = header_span
        proposals: list[Any] = [
            module.InstrumentFieldProposal(
                "name", identity, "high", header_span_id,
                "transposed native PDF instrument column header",
            )
        ]
        instrument_type = module._infer_instrument_type(identity, identity)
        if instrument_type:
            proposals.append(
                module.InstrumentFieldProposal(
                    "instrument_type",
                    instrument_type,
                    "high" if module._NOTES_TITLE_RE.search(identity) or module._FACILITY_RE.search(identity) else "medium",
                    header_span_id,
                    "transposed native PDF instrument column header",
                )
            )
        markers: list[str] = list(_extract_markers(header_row.text))
        evidence: list[Any] = [header_span]
        for row_index, label, _field in field_rows:
            row = page_rows[row_index]
            # Include the semantic label as a left-side anchor, then the instrument x anchors.
            label_x = min(fragment.x for fragment in row.fragments)
            xs = (label_x, *instrument_xs)
            value = _column_value(row, xs, instrument_index + 1)
            if not value:
                continue
            markers.extend(_extract_markers(value))
            span_id = (
                f"{document.source_accession}:{document.sequence or 0}:"
                f"p{row.page_number}:c{instrument_index + 1}:r{row_index + 1}"
            )
            span = _source_span(
                module,
                document,
                span_id,
                f"PDF TRANSPOSED FIELD | page={row.page_number} | instrument={identity} | {label}={value}",
            )
            span_by_id[span_id] = span
            evidence.append(span)
            parsed = _table_row_proposals(
                module,
                span_id=span_id,
                headers=("Instrument", label),
                values=(identity, _strip_trailing_markers(value)),
                document=document,
            )
            proposals.extend(item for item in parsed if item.field not in {"name", "instrument_type"})

        proposals = list(module._unique_proposals(proposals))
        if not any(item.field not in {"name", "instrument_type"} for item in proposals):
            continue
        linked, footnote_warnings = _linked_footnotes(markers, definitions)
        for item in linked:
            if item.span_id not in span_by_id:
                span_by_id[item.span_id] = _source_span(module, document, item.span_id, item.text)
            evidence.append(span_by_id[item.span_id])
        source_span_ids = tuple(dict.fromkeys(span.span_id for span in evidence))
        template, template_warnings = module._template_for_cluster(document, tuple(proposals), source_span_ids)
        candidates.append(
            module.DebtInstrumentSourceCandidate(
                candidate_id=(
                    f"{document.source_accession}:{document.sequence or 0}:"
                    f"p{header_row.page_number}:c{instrument_index + 1}"
                ),
                source_accession=document.source_accession,
                as_of_date=document.filing_date,
                document_url=document.url,
                document_type=document.document_type,
                document_description=document.description,
                proposals=tuple(proposals),
                source_span_ids=source_span_ids,
                snapshot_template=template,
                warnings=tuple(dict.fromkeys([
                    "transposed native-PDF candidate uses explicit x-axis instrument columns and first-column field labels; verify orientation and units before ledger ingestion",
                    *footnote_warnings,
                    *template_warnings,
                ])),
                cluster_status="pdf_layout_column",
                cluster_basis=(
                    "transposed_native_pdf_table",
                    "coordinate_mapped_instrument_columns",
                    "first_column_field_labels",
                    "pypdf_text_matrix",
                ),
            )
        )
    if not candidates:
        return None
    return tuple(span_by_id.values()), tuple(candidates)


def _row_group_candidate(
    native: Any,
    module: Any,
    document: Any,
    *,
    page_number: int,
    headers: tuple[str, ...],
    group: list[tuple[int, Any, tuple[str, ...]]],
    definitions: dict[str, list[_FootnoteDefinition]],
) -> tuple[tuple[Any, ...], Any | None]:
    name_columns = [index for index, header in enumerate(headers) if _header_field(header) == "name"]
    if len(name_columns) != 1:
        return (), None
    name_column = name_columns[0]
    identity = ""
    for _, _row, values in group:
        if values[name_column].strip():
            identity = _strip_trailing_markers(values[name_column])
            break
    if not identity or _aggregate_label(identity):
        return (), None

    spans: list[Any] = []
    proposals: list[Any] = []
    markers: list[str] = []
    for row_index, row, raw_values in group:
        markers.extend(marker for value in raw_values for marker in _extract_markers(value))
        values = [_strip_trailing_markers(value) for value in raw_values]
        if not values[name_column].strip():
            values[name_column] = identity
        span_id = f"{document.source_accession}:{document.sequence or 0}:p{page_number}:r{row_index + 1}"
        row_proposals = _table_row_proposals(
            module,
            span_id=span_id,
            headers=headers,
            values=tuple(values),
            document=document,
        )
        # Preserve native-PDF provenance wording from the original path.
        row_proposals = native._convert_pdf_proposals(module, row_proposals)
        proposals.extend(row_proposals)
        spans.append(
            _source_span(
                module,
                document,
                span_id,
                native._pdf_row_text(page_number, row.y, headers, raw_values),
            )
        )
    if not proposals:
        return tuple(spans), None
    proposals_tuple = module._unique_proposals(proposals)
    linked, footnote_warnings = _linked_footnotes(markers, definitions)
    linked_spans = [_source_span(module, document, item.span_id, item.text) for item in linked]
    source_span_ids = tuple(dict.fromkeys(span.span_id for span in [*spans, *linked_spans]))
    template, template_warnings = module._template_for_cluster(document, proposals_tuple, source_span_ids)
    multi = len(group) > 1
    candidate = module.DebtInstrumentSourceCandidate(
        candidate_id=(
            f"{document.source_accession}:{document.sequence or 0}:p{page_number}:"
            + (f"g{group[0][0] + 1}-{group[-1][0] + 1}" if multi else f"r{group[0][0] + 1}")
        ),
        source_accession=document.source_accession,
        as_of_date=document.filing_date,
        document_url=document.url,
        document_type=document.document_type,
        document_description=document.description,
        proposals=proposals_tuple,
        source_span_ids=source_span_ids,
        snapshot_template=template,
        warnings=tuple(dict.fromkeys([
            (
                "native-PDF multi-row candidate combines only contiguous coordinate rows with one explicit instrument identity; conflicting high-confidence fields remain unresolved"
                if multi
                else "native-PDF row candidate preserves coordinate-based same-row/column association; verify layout before ledger ingestion"
            ),
            *footnote_warnings,
            *template_warnings,
        ])),
        cluster_status="pdf_layout_row_group" if multi else "pdf_layout_row",
        cluster_basis=(
            ("contiguous_native_pdf_layout_rows", "shared_instrument_identity", "coordinate_mapped_columns", "pypdf_text_matrix")
            if multi
            else ("same_native_pdf_layout_row", "coordinate_mapped_columns", "pypdf_text_matrix")
        ),
    )
    return tuple([*spans, *linked_spans]), candidate


def _row_oriented_page(
    native: Any,
    module: Any,
    page_rows: list[Any],
    document: Any,
    definitions: dict[str, list[_FootnoteDefinition]],
    *,
    max_candidates: int,
) -> tuple[tuple[Any, ...], tuple[Any, ...]] | None:
    spans_by_id: dict[str, Any] = {}
    candidates: list[Any] = []
    header_index = 0
    found_header = False
    while header_index < len(page_rows) and len(candidates) < max_candidates:
        anchors = native._header_anchors(page_rows[header_index])
        if not anchors:
            header_index += 1
            continue
        found_header = True
        headers = tuple(item[1] for item in anchors)
        fields = tuple(item[2] for item in anchors)
        name_columns = [index for index, field in enumerate(fields) if field == "name"]
        if len(name_columns) != 1:
            header_index += 1
            continue
        name_column = name_columns[0]
        groups: list[list[tuple[int, Any, tuple[str, ...]]]] = []
        current: list[tuple[int, Any, tuple[str, ...]]] = []
        data_index = header_index + 1
        while data_index < len(page_rows):
            row = page_rows[data_index]
            if native._header_anchors(row):
                break
            if _parse_definition(row.text, allow_bare_number=True):
                if current:
                    groups.append(current)
                    current = []
                data_index += 1
                continue
            values = native._column_values(row, anchors)
            if not values or sum(bool(value.strip()) for value in values) < 1:
                if current:
                    groups.append(current)
                    current = []
                data_index += 1
                continue
            identity = _strip_trailing_markers(values[name_column]) if name_column < len(values) else ""
            if identity and _aggregate_label(identity):
                if current:
                    groups.append(current)
                    current = []
                data_index += 1
                continue
            if identity:
                if current:
                    groups.append(current)
                current = [(data_index, row, values)]
            elif current and any(
                values[index].strip()
                for index, field in enumerate(fields)
                if index < len(values) and field != "name"
            ):
                current.append((data_index, row, values))
            elif current:
                groups.append(current)
                current = []
            data_index += 1
        if current:
            groups.append(current)
        for group in groups:
            if len(candidates) >= max_candidates:
                break
            group_spans, candidate = _row_group_candidate(
                native,
                module,
                document,
                page_number=page_rows[header_index].page_number,
                headers=headers,
                group=group,
                definitions=definitions,
            )
            for span in group_spans:
                spans_by_id[span.span_id] = span
            if candidate:
                candidates.append(candidate)
        header_index = max(header_index + 1, data_index)
    if not found_header:
        return None
    return tuple(spans_by_id.values()), tuple(candidates)


def _complex_extract(native: Any, module: Any, pdf_bytes: bytes, document: Any, *, max_candidates: int) -> Any:
    fragments = native.extract_pdf_text_fragments(pdf_bytes)
    character_count = sum(len(re.sub(r"\s+", "", item.text)) for item in fragments)
    page_count = max((item.page_number for item in fragments), default=0)
    if len(fragments) < native._MIN_NATIVE_FRAGMENTS or character_count < native._MIN_NATIVE_TEXT_CHARS:
        return native.NativePdfExtractionResult(
            native_text_detected=False,
            spans=(),
            candidates=(),
            page_count=page_count,
            fragment_count=len(fragments),
            extracted_character_count=character_count,
            warnings=("native PDF text layer was absent or too sparse for safe automatic extraction",),
        )

    rows = native.layout_rows(fragments)
    definitions_by_page = _page_footnotes(native, rows, document)
    rows_by_page: dict[int, list[Any]] = {}
    for row in rows:
        rows_by_page.setdefault(row.page_number, []).append(row)

    spans: list[Any] = []
    candidates: list[Any] = []
    table_pages: set[int] = set()
    for page_number in sorted(rows_by_page):
        if len(candidates) >= max_candidates:
            break
        page_rows = rows_by_page[page_number]
        definitions = definitions_by_page.get(page_number, {})
        transposed = _transposed_page(
            native,
            module,
            page_rows,
            document,
            definitions,
            max_candidates=max_candidates - len(candidates),
        )
        if transposed:
            page_spans, page_candidates = transposed
            spans.extend(page_spans)
            candidates.extend(page_candidates)
            table_pages.add(page_number)
            continue
        row_oriented = _row_oriented_page(
            native,
            module,
            page_rows,
            document,
            definitions,
            max_candidates=max_candidates - len(candidates),
        )
        if row_oriented:
            page_spans, page_candidates = row_oriented
            spans.extend(page_spans)
            candidates.extend(page_candidates)
            table_pages.add(page_number)

    remaining = max(0, max_candidates - len(candidates))
    if remaining:
        prose_spans, prose_candidates = native._prose_page_candidates(
            module,
            rows,
            document,
            excluded_pages=table_pages,
            max_candidates=remaining,
        )
        spans.extend(prose_spans)
        candidates.extend(prose_candidates)

    span_by_id = {span.span_id: span for span in spans}
    return native.NativePdfExtractionResult(
        native_text_detected=True,
        spans=tuple(span_by_id.values()),
        candidates=tuple(candidates),
        page_count=page_count,
        fragment_count=len(fragments),
        extracted_character_count=character_count,
        warnings=(),
    )


def install_complex_native_pdf_extraction(native: Any) -> None:
    """Upgrade native-PDF deterministic tables without introducing OCR."""

    if getattr(native, "_complex_native_pdf_debt_extraction_installed", False):
        return
    original = native.extract_native_pdf_debt_candidates

    def complex_native_pdf_debt_candidates(
        module: Any,
        pdf_bytes: bytes,
        document: Any,
        *,
        max_candidates: int = 20,
    ) -> Any:
        if max_candidates <= 0:
            return original(module, pdf_bytes, document, max_candidates=max_candidates)
        try:
            return _complex_extract(
                native,
                module,
                pdf_bytes,
                document,
                max_candidates=max_candidates,
            )
        except Exception:
            # Complex layout detection is conservative. Any parser defect or unsupported
            # shape falls back to the previously tested native-row/prose extractor.
            return original(module, pdf_bytes, document, max_candidates=max_candidates)

    complex_native_pdf_debt_candidates.__name__ = "extract_native_pdf_debt_candidates"
    native.extract_native_pdf_debt_candidates = complex_native_pdf_debt_candidates
    native._complex_native_pdf_debt_extraction_installed = True

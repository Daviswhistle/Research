from __future__ import annotations

from dataclasses import replace
import re
from typing import Any

from .complex_table_debt_extraction import _identity_score, _strip_trailing_markers
from .structured_debt_extraction import _header_field, _normalize_header


def install_complex_native_pdf_hardening(complex_module: Any) -> None:
    """Tighten complex native-PDF detection without weakening visual safeguards.

    Edge cases handled here:
    - instrument titles such as "Senior Secured Notes" win over generic header tokens;
    - sparse but explicit coordinate tables can be parsed without enabling sparse prose;
    - multiple transposed tables on one page are segmented rather than cross-linked;
    - transposed header footnotes are scoped to the instrument column that cites them.
    """

    if getattr(complex_module, "_complex_native_pdf_hardening_installed", False):
        return

    def hardened_transposed_header(
        native: Any,
        rows: list[Any],
        first_field_index: int,
        module: Any,
    ) -> tuple[Any, tuple[tuple[float, str], ...]] | None:
        for header_index in range(first_field_index - 1, max(-1, first_field_index - 4), -1):
            row = rows[header_index]
            identities: list[tuple[float, str]] = []
            for fragment in row.fragments:
                text = _strip_trailing_markers(fragment.text)
                score = _identity_score(module, text)
                # Instrument identity is more specific than a generic semantic
                # token inside the title (e.g. "secured" or "notes").
                if score >= 55:
                    identities.append((fragment.x, text))
                    continue
                if _header_field(text) is not None:
                    continue
            identities.sort(key=lambda item: item[0])
            if identities:
                normalized = [_normalize_header(text) for _, text in identities]
                if len(normalized) == len(set(normalized)):
                    return row, tuple(identities)
        return None

    complex_module._transposed_header = hardened_transposed_header

    original_transposed_page = complex_module._transposed_page

    def _header_for_segment(native: Any, module: Any, rows: list[Any]):
        field_indices = [
            index for index, row in enumerate(rows)
            if complex_module._field_row(row)
        ]
        if not field_indices:
            return None
        first_field = min(field_indices)
        if first_field <= 0:
            return None
        return complex_module._transposed_header(native, rows, first_field, module)

    def _scope_candidate_footnotes(
        candidate: Any,
        spans: tuple[Any, ...],
        header_row: Any,
        definitions: dict[str, list[Any]],
    ) -> Any:
        span_map = {span.span_id: span for span in spans}
        footnote_marker_by_id = {
            definition.span_id: marker
            for marker, rows in definitions.items()
            for definition in rows
        }
        name = next(
            (str(item.value) for item in candidate.proposals if item.field == "name"),
            "",
        )
        allowed_markers: set[str] = set()
        normalized_name = _normalize_header(_strip_trailing_markers(name))
        for fragment in header_row.fragments:
            text = fragment.text.strip()
            if _normalize_header(_strip_trailing_markers(text)) == normalized_name:
                allowed_markers.update(complex_module._extract_markers(text))

        for ref in candidate.source_span_ids:
            if ref in footnote_marker_by_id:
                continue
            span = span_map.get(ref)
            if span is not None:
                allowed_markers.update(complex_module._extract_markers(span.text))

        kept_refs = tuple(
            ref
            for ref in candidate.source_span_ids
            if ref not in footnote_marker_by_id
            or footnote_marker_by_id[ref] in allowed_markers
        )
        if kept_refs == candidate.source_span_ids:
            return candidate
        template = dict(candidate.snapshot_template)
        template["source_refs"] = list(kept_refs)
        return replace(
            candidate,
            source_span_ids=kept_refs,
            snapshot_template=template,
        )

    def _remap_segment_ids(spans: tuple[Any, ...], candidates: tuple[Any, ...], segment_number: int):
        id_map = {
            span.span_id: f"{span.span_id}:table{segment_number}"
            for span in spans
        }
        remapped_spans = tuple(
            replace(span, span_id=id_map[span.span_id])
            for span in spans
        )
        remapped_candidates: list[Any] = []
        for candidate in candidates:
            proposals = tuple(
                replace(
                    proposal,
                    source_span_id=id_map.get(proposal.source_span_id, proposal.source_span_id),
                )
                for proposal in candidate.proposals
            )
            refs = tuple(id_map.get(ref, ref) for ref in candidate.source_span_ids)
            template = dict(candidate.snapshot_template)
            template["source_refs"] = [id_map.get(ref, ref) for ref in template.get("source_refs", [])]
            remapped_candidates.append(
                replace(
                    candidate,
                    candidate_id=f"{candidate.candidate_id}:table{segment_number}",
                    proposals=proposals,
                    source_span_ids=refs,
                    snapshot_template=template,
                )
            )
        return remapped_spans, tuple(remapped_candidates)

    def hardened_transposed_page(
        native: Any,
        module: Any,
        page_rows: list[Any],
        document: Any,
        definitions: dict[str, list[Any]],
        *,
        max_candidates: int,
    ):
        if not page_rows or max_candidates <= 0:
            return None

        header_indices: list[int] = []
        for field_index, row in enumerate(page_rows):
            if not complex_module._field_row(row) or field_index <= 0:
                continue
            header = complex_module._transposed_header(native, page_rows, field_index, module)
            if not header:
                continue
            header_row, _ = header
            header_index = next(
                (index for index, candidate in enumerate(page_rows) if candidate is header_row),
                None,
            )
            if header_index is not None and header_index not in header_indices:
                header_indices.append(header_index)
        header_indices.sort()
        if not header_indices:
            return None

        # One table preserves the established IDs; still scope its footnotes.
        if len(header_indices) == 1:
            result = original_transposed_page(
                native,
                module,
                page_rows,
                document,
                definitions,
                max_candidates=max_candidates,
            )
            if not result:
                return None
            spans, candidates = result
            header = _header_for_segment(native, module, page_rows)
            if not header:
                return result
            header_row, _ = header
            candidates = tuple(
                _scope_candidate_footnotes(candidate, spans, header_row, definitions)
                for candidate in candidates
            )
            return spans, candidates

        all_spans: dict[str, Any] = {}
        all_candidates: list[Any] = []
        for segment_number, start in enumerate(header_indices, 1):
            if len(all_candidates) >= max_candidates:
                break
            end = header_indices[segment_number] if segment_number < len(header_indices) else len(page_rows)
            segment_rows = page_rows[start:end]
            result = original_transposed_page(
                native,
                module,
                segment_rows,
                document,
                definitions,
                max_candidates=max_candidates - len(all_candidates),
            )
            if not result:
                continue
            spans, candidates = result
            header = _header_for_segment(native, module, segment_rows)
            if not header:
                continue
            header_row, _ = header
            candidates = tuple(
                _scope_candidate_footnotes(candidate, spans, header_row, definitions)
                for candidate in candidates
            )
            spans, candidates = _remap_segment_ids(spans, candidates, segment_number)
            for span in spans:
                all_spans[span.span_id] = span
            all_candidates.extend(candidates)

        if not all_candidates:
            return None
        return tuple(all_spans.values()), tuple(all_candidates)

    complex_module._transposed_page = hardened_transposed_page

    def hardened_complex_extract(
        native: Any,
        module: Any,
        pdf_bytes: bytes,
        document: Any,
        *,
        max_candidates: int,
    ) -> Any:
        fragments = native.extract_pdf_text_fragments(pdf_bytes)
        character_count = sum(len(re.sub(r"\s+", "", item.text)) for item in fragments)
        page_count = max((item.page_number for item in fragments), default=0)
        rows = native.layout_rows(fragments)
        sparse = (
            len(fragments) < native._MIN_NATIVE_FRAGMENTS
            or character_count < native._MIN_NATIVE_TEXT_CHARS
        )

        definitions_by_page = complex_module._page_footnotes(native, rows, document)
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
            transposed = complex_module._transposed_page(
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
            row_oriented = complex_module._row_oriented_page(
                native,
                module,
                page_rows,
                document,
                definitions,
                max_candidates=max_candidates - len(candidates),
            )
            if row_oriented and row_oriented[1]:
                page_spans, page_candidates = row_oriented
                spans.extend(page_spans)
                candidates.extend(page_candidates)
                table_pages.add(page_number)

        if sparse and not candidates:
            return native.NativePdfExtractionResult(
                native_text_detected=False,
                spans=(),
                candidates=(),
                page_count=page_count,
                fragment_count=len(fragments),
                extracted_character_count=character_count,
                warnings=(
                    "native PDF text layer was absent or too sparse for safe automatic extraction",
                ),
            )

        warnings: tuple[str, ...] = ()
        if sparse and candidates:
            warnings = (
                "native text was sparse, but explicit coordinate-table geometry supported deterministic table extraction; prose extraction remained disabled",
            )
        else:
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
            warnings=warnings,
        )

    complex_module._complex_extract = hardened_complex_extract
    complex_module._complex_native_pdf_hardening_installed = True

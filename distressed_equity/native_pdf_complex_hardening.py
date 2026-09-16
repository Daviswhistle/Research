from __future__ import annotations

import re
from typing import Any

from .complex_table_debt_extraction import _identity_score, _strip_trailing_markers
from .structured_debt_extraction import _header_field, _normalize_header


def install_complex_native_pdf_hardening(complex_module: Any) -> None:
    """Tighten complex native-PDF detection without weakening visual safeguards.

    Two edge cases need special treatment:
    - a real instrument title such as "Senior Secured Notes" must win over the
      generic `secured` header classifier when it appears in a transposed header;
    - a small but explicit coordinate table can be safely parsed even when it is
      below the prose-oriented native-text density threshold.

    Sparse documents are *not* sent through prose extraction. They are accepted
    only when the deterministic table parser itself produces candidates.
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

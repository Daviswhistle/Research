from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import re
from typing import Any, Iterable

from pypdf import PdfReader

from .structured_debt_extraction import _header_field, _row_text, _table_row_proposals


_MAX_PDF_BYTES = 25_000_000
_MIN_NATIVE_TEXT_CHARS = 80
_MIN_NATIVE_FRAGMENTS = 6


@dataclass(frozen=True)
class PdfTextFragment:
    page_number: int
    x: float
    y: float
    font_size: float
    text: str


@dataclass(frozen=True)
class PdfLayoutRow:
    page_number: int
    y: float
    fragments: tuple[PdfTextFragment, ...]

    @property
    def text(self) -> str:
        return " ".join(item.text for item in self.fragments if item.text).strip()


@dataclass(frozen=True)
class NativePdfExtractionResult:
    native_text_detected: bool
    spans: tuple[Any, ...]
    candidates: tuple[Any, ...]
    page_count: int
    fragment_count: int
    extracted_character_count: int
    warnings: tuple[str, ...] = ()


def _pdf_bytes(client: Any, url: str, *, max_bytes: int = _MAX_PDF_BYTES) -> bytes:
    client._throttle()
    response = client.session.get(
        url,
        headers={
            "User-Agent": client.user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
        },
        timeout=30,
    )
    response.raise_for_status()
    content = bytes(response.content)
    if len(content) > max_bytes:
        raise ValueError(f"PDF exceeds native extraction byte limit: {len(content)} > {max_bytes}")
    if not content.startswith(b"%PDF"):
        raise ValueError("response did not contain a PDF header")
    return content


def _apply_matrix(x: float, y: float, matrix: list[float] | tuple[float, ...]) -> tuple[float, float]:
    if len(matrix) < 6:
        return x, y
    return (
        x * float(matrix[0]) + y * float(matrix[2]) + float(matrix[4]),
        x * float(matrix[1]) + y * float(matrix[3]) + float(matrix[5]),
    )


def extract_pdf_text_fragments(pdf_bytes: bytes) -> tuple[PdfTextFragment, ...]:
    """Extract native PDF text while preserving page and text-matrix position.

    This intentionally does not OCR. A PDF without a usable text layer returns no
    meaningful fragments and remains on the visual-extraction path.
    """

    reader = PdfReader(BytesIO(pdf_bytes), strict=False)
    output: list[PdfTextFragment] = []
    for page_number, page in enumerate(reader.pages, 1):
        page_fragments: list[PdfTextFragment] = []

        def visitor_text(text: str, cm: list[float], tm: list[float], _font: Any, font_size: float) -> None:
            clean = re.sub(r"\s+", " ", text or "").strip()
            if not clean:
                return
            tx = float(tm[4]) if len(tm) >= 6 else 0.0
            ty = float(tm[5]) if len(tm) >= 6 else 0.0
            x, y = _apply_matrix(tx, ty, cm)
            if not (-10_000 <= x <= 10_000 and -10_000 <= y <= 10_000):
                return
            page_fragments.append(
                PdfTextFragment(
                    page_number=page_number,
                    x=round(x, 3),
                    y=round(y, 3),
                    font_size=max(0.0, float(font_size or 0.0)),
                    text=clean,
                )
            )

        page.extract_text(visitor_text=visitor_text)
        # Exact duplicate text-show operations occur in some PDFs because of
        # clipping/layering. Preserve first occurrence only at the same location.
        seen: set[tuple[float, float, str]] = set()
        for item in sorted(page_fragments, key=lambda value: (-value.y, value.x, value.text)):
            key = (round(item.x, 1), round(item.y, 1), item.text)
            if key in seen:
                continue
            seen.add(key)
            output.append(item)
    return tuple(output)


def layout_rows(
    fragments: Iterable[PdfTextFragment],
    *,
    minimum_y_tolerance: float = 2.5,
) -> tuple[PdfLayoutRow, ...]:
    by_page: dict[int, list[PdfTextFragment]] = {}
    for item in fragments:
        by_page.setdefault(item.page_number, []).append(item)

    output: list[PdfLayoutRow] = []
    for page_number in sorted(by_page):
        rows: list[list[PdfTextFragment]] = []
        row_y: list[float] = []
        for item in sorted(by_page[page_number], key=lambda value: (-value.y, value.x)):
            tolerance = max(minimum_y_tolerance, min(6.0, item.font_size * 0.35))
            target: int | None = None
            best_distance = float("inf")
            for index, y in enumerate(row_y):
                distance = abs(item.y - y)
                if distance <= tolerance and distance < best_distance:
                    target = index
                    best_distance = distance
            if target is None:
                rows.append([item])
                row_y.append(item.y)
            else:
                rows[target].append(item)
                row_y[target] = sum(value.y for value in rows[target]) / len(rows[target])

        ordered = sorted(zip(row_y, rows), key=lambda pair: -pair[0])
        for y, row in ordered:
            output.append(
                PdfLayoutRow(
                    page_number=page_number,
                    y=round(y, 3),
                    fragments=tuple(sorted(row, key=lambda value: value.x)),
                )
            )
    return tuple(output)


def _header_anchors(row: PdfLayoutRow) -> tuple[tuple[float, str, str], ...]:
    anchors: list[tuple[float, str, str]] = []
    for fragment in row.fragments:
        field = _header_field(fragment.text)
        if field:
            anchors.append((fragment.x, fragment.text, field))
    anchors.sort(key=lambda item: item[0])
    fields = {item[2] for item in anchors}
    meaningful = fields.intersection(
        {"name", "principal", "outstanding", "commitment", "drawn", "available", "maturity", "coupon", "rate", "cusip", "isin"}
    )
    if len(anchors) < 2 or len(meaningful) < 2:
        return ()
    if "name" not in fields and not fields.intersection({"cusip", "isin"}):
        return ()
    return tuple(anchors)


def _column_values(row: PdfLayoutRow, anchors: tuple[tuple[float, str, str], ...]) -> tuple[str, ...]:
    if not anchors:
        return ()
    boundaries = [
        (anchors[index][0] + anchors[index + 1][0]) / 2.0
        for index in range(len(anchors) - 1)
    ]
    cells: list[list[str]] = [[] for _ in anchors]
    for fragment in row.fragments:
        column = 0
        while column < len(boundaries) and fragment.x >= boundaries[column]:
            column += 1
        cells[column].append(fragment.text)
    return tuple(" ".join(parts).strip() for parts in cells)


def _pdf_row_text(page_number: int, y: float, headers: tuple[str, ...], values: tuple[str, ...]) -> str:
    return f"PDF LAYOUT ROW | page={page_number} | y={y:.2f} | " + _row_text(headers, values)


def _convert_pdf_proposals(module: Any, proposals: Iterable[Any]) -> tuple[Any, ...]:
    output: list[Any] = []
    for item in proposals:
        basis = str(item.basis).replace("same HTML table row column", "same native PDF layout row column")
        output.append(
            module.InstrumentFieldProposal(
                item.field,
                item.value,
                item.confidence,
                item.source_span_id,
                basis,
            )
        )
    return tuple(output)


def _layout_table_candidates(
    module: Any,
    rows: tuple[PdfLayoutRow, ...],
    document: Any,
    *,
    max_candidates: int,
) -> tuple[tuple[Any, ...], tuple[Any, ...], set[int]]:
    spans: list[Any] = []
    candidates: list[Any] = []
    table_pages: set[int] = set()
    rows_by_page: dict[int, list[PdfLayoutRow]] = {}
    for row in rows:
        rows_by_page.setdefault(row.page_number, []).append(row)

    for page_number in sorted(rows_by_page):
        page_rows = rows_by_page[page_number]
        header_index = 0
        while header_index < len(page_rows):
            anchors = _header_anchors(page_rows[header_index])
            if not anchors:
                header_index += 1
                continue
            table_pages.add(page_number)
            headers = tuple(item[1] for item in anchors)
            data_index = header_index + 1
            while data_index < len(page_rows):
                row = page_rows[data_index]
                if _header_anchors(row):
                    break
                values = _column_values(row, anchors)
                if not values or sum(bool(value.strip()) for value in values) < 2:
                    data_index += 1
                    continue
                span_id = f"{document.source_accession}:{document.sequence or 0}:p{page_number}:r{data_index + 1}"
                proposals = _table_row_proposals(
                    module,
                    span_id=span_id,
                    headers=headers,
                    values=values,
                    document=document,
                )
                proposals = _convert_pdf_proposals(module, proposals)
                if not proposals:
                    data_index += 1
                    continue
                span = module.InstrumentSourceSpan(
                    span_id=span_id,
                    source_accession=document.source_accession,
                    published_on=document.filing_date,
                    document_url=document.url,
                    document_type=document.document_type,
                    description=document.description,
                    text=_pdf_row_text(page_number, row.y, headers, values),
                )
                template, template_warnings = module._template_for_cluster(document, proposals, (span_id,))
                warnings = tuple(
                    dict.fromkeys(
                        [
                            "native-PDF row candidate preserves coordinate-based same-row/column association; verify layout before ledger ingestion",
                            *template_warnings,
                        ]
                    )
                )
                spans.append(span)
                candidates.append(
                    module.DebtInstrumentSourceCandidate(
                        candidate_id=f"{document.source_accession}:{document.sequence or 0}:p{page_number}:r{data_index + 1}",
                        source_accession=document.source_accession,
                        as_of_date=document.filing_date,
                        document_url=document.url,
                        document_type=document.document_type,
                        document_description=document.description,
                        proposals=proposals,
                        source_span_ids=(span_id,),
                        snapshot_template=template,
                        warnings=warnings,
                        cluster_status="pdf_layout_row",
                        cluster_basis=("same_native_pdf_layout_row", "coordinate_mapped_columns", "pypdf_text_matrix"),
                    )
                )
                if len(candidates) >= max_candidates:
                    return tuple(spans), tuple(candidates), table_pages
                data_index += 1
            header_index = max(header_index + 1, data_index)
    return tuple(spans), tuple(candidates), table_pages


def _prose_page_candidates(
    module: Any,
    rows: tuple[PdfLayoutRow, ...],
    document: Any,
    *,
    excluded_pages: set[int],
    max_candidates: int,
) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    spans: list[Any] = []
    candidates: list[Any] = []
    rows_by_page: dict[int, list[PdfLayoutRow]] = {}
    for row in rows:
        rows_by_page.setdefault(row.page_number, []).append(row)

    for page_number in sorted(rows_by_page):
        if page_number in excluded_pages:
            continue
        page_text = "\n".join(row.text for row in rows_by_page[page_number] if row.text)
        fragments: list[Any] = []
        for block_index, block in enumerate(module._candidate_blocks(page_text), 1):
            span_id = f"{document.source_accession}:{document.sequence or 0}:p{page_number}:s{block_index}"
            proposals = module._proposals_for_block(span_id, block, document)
            if not proposals:
                continue
            span = module.InstrumentSourceSpan(
                span_id=span_id,
                source_accession=document.source_accession,
                published_on=document.filing_date,
                document_url=document.url,
                document_type=document.document_type,
                description=document.description,
                text=f"PDF NATIVE TEXT | page={page_number} | {block}",
            )
            fragments.append(module._SpanFragment(span=span, proposals=proposals, order=len(fragments)))

        clustered = module._cluster_fragments(document, tuple(fragments))
        for cluster, basis in clustered:
            source_span_ids = tuple(item.span.span_id for item in cluster)
            proposals = module._unique_proposals(
                proposal
                for item in cluster
                for proposal in item.proposals
            )
            template, template_warnings = module._template_for_cluster(document, proposals, source_span_ids)
            for item in cluster:
                spans.append(item.span)
            warnings = ["native-PDF prose candidate preserves page-level provenance; verify PDF text order before ledger ingestion"]
            if len(cluster) > 1:
                warnings.append("clustered multiple native-PDF prose spans on the same page")
            warnings.extend(template_warnings)
            candidates.append(
                module.DebtInstrumentSourceCandidate(
                    candidate_id=f"{document.source_accession}:{document.sequence or 0}:p{page_number}:c{len(candidates) + 1}",
                    source_accession=document.source_accession,
                    as_of_date=document.filing_date,
                    document_url=document.url,
                    document_type=document.document_type,
                    document_description=document.description,
                    proposals=proposals,
                    source_span_ids=source_span_ids,
                    snapshot_template=template,
                    warnings=tuple(dict.fromkeys(warnings)),
                    cluster_status="pdf_native_prose_linked" if len(cluster) > 1 else "pdf_native_prose",
                    cluster_basis=tuple(sorted(set(basis) | {"same_pdf_page", "native_text_matrix_order"})),
                )
            )
            if len(candidates) >= max_candidates:
                return tuple(spans), tuple(candidates)
    return tuple(spans), tuple(candidates)


def extract_native_pdf_debt_candidates(
    module: Any,
    pdf_bytes: bytes,
    document: Any,
    *,
    max_candidates: int = 20,
) -> NativePdfExtractionResult:
    fragments = extract_pdf_text_fragments(pdf_bytes)
    character_count = sum(len(re.sub(r"\s+", "", item.text)) for item in fragments)
    page_count = max((item.page_number for item in fragments), default=0)
    if len(fragments) < _MIN_NATIVE_FRAGMENTS or character_count < _MIN_NATIVE_TEXT_CHARS:
        return NativePdfExtractionResult(
            native_text_detected=False,
            spans=(),
            candidates=(),
            page_count=page_count,
            fragment_count=len(fragments),
            extracted_character_count=character_count,
            warnings=("native PDF text layer was absent or too sparse for safe automatic extraction",),
        )

    rows = layout_rows(fragments)
    table_spans, table_candidates, table_pages = _layout_table_candidates(
        module,
        rows,
        document,
        max_candidates=max_candidates,
    )
    remaining = max(0, max_candidates - len(table_candidates))
    prose_spans: tuple[Any, ...] = ()
    prose_candidates: tuple[Any, ...] = ()
    if remaining:
        prose_spans, prose_candidates = _prose_page_candidates(
            module,
            rows,
            document,
            excluded_pages=table_pages,
            max_candidates=remaining,
        )

    return NativePdfExtractionResult(
        native_text_detected=True,
        spans=tuple(table_spans) + tuple(prose_spans),
        candidates=tuple(table_candidates) + tuple(prose_candidates),
        page_count=page_count,
        fragment_count=len(fragments),
        extracted_character_count=character_count,
        warnings=(),
    )


def _is_pdf(document: Any) -> bool:
    return str(getattr(document, "document", "") or "").lower().endswith(".pdf")


def _visual_warning_for_document(warning: str, document: Any) -> bool:
    return (
        warning.startswith("VISUAL_EXTRACTION_REQUIRED|")
        and f"|document={document.document}|" in warning
    )


def _pdf_status_warning(document: Any, status: str, **values: Any) -> str:
    fields = [
        status,
        f"accession={document.source_accession}",
        f"document={document.document}",
        f"document_type={document.document_type}",
        f"url={document.url}",
    ]
    fields.extend(f"{key}={value}" for key, value in values.items())
    return "|".join(fields)


def install_native_pdf_debt_extraction(module: Any) -> None:
    """Supplement the structured SEC packet builder with native-text PDF parsing."""

    if getattr(module, "_native_pdf_debt_extraction_installed", False):
        return
    original_build = module.build_sec_instrument_packet

    def native_pdf_build_sec_instrument_packet(
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
        packet = original_build(
            client,
            ticker=ticker,
            company_name=company_name,
            analysis_date=analysis_date,
            filings=filings,
            filing_limit=filing_limit,
            max_exhibits_per_filing=max_exhibits_per_filing,
            max_candidates_per_exhibit=max_candidates_per_exhibit,
        )
        spans = list(packet.spans)
        candidates = list(packet.candidates)
        warnings = list(packet.warnings)

        for document in packet.documents:
            if not _is_pdf(document):
                continue
            try:
                content = _pdf_bytes(client, document.url)
                result = extract_native_pdf_debt_candidates(
                    module,
                    content,
                    document,
                    max_candidates=max_candidates_per_exhibit,
                )
            except Exception as exc:
                warnings.append(
                    _pdf_status_warning(
                        document,
                        "PDF_NATIVE_TEXT_EXTRACTION_FAILED",
                        reason=str(exc).replace("|", "/"),
                    )
                )
                continue

            if not result.native_text_detected:
                warnings.append(
                    _pdf_status_warning(
                        document,
                        "PDF_NATIVE_TEXT_UNAVAILABLE",
                        page_count=result.page_count,
                        fragment_count=result.fragment_count,
                        character_count=result.extracted_character_count,
                    )
                )
                continue

            warnings = [
                item
                for item in warnings
                if not _visual_warning_for_document(item, document)
            ]
            spans.extend(result.spans)
            candidates.extend(result.candidates)
            warnings.append(
                _pdf_status_warning(
                    document,
                    "PDF_NATIVE_TEXT_EXTRACTED",
                    page_count=result.page_count,
                    fragment_count=result.fragment_count,
                    character_count=result.extracted_character_count,
                    candidate_count=len(result.candidates),
                )
            )

        return module.SecInstrumentPacket(
            ticker=packet.ticker,
            company_name=packet.company_name,
            analysis_date=packet.analysis_date,
            documents=packet.documents,
            spans=tuple(spans),
            candidates=tuple(candidates),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    native_pdf_build_sec_instrument_packet.__name__ = "build_sec_instrument_packet"
    module.build_sec_instrument_packet = native_pdf_build_sec_instrument_packet
    module._native_pdf_debt_extraction_installed = True

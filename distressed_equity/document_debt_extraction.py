from __future__ import annotations

import re
from typing import Any

from . import sec_instruments
from .complex_table_debt_extraction import install_complex_table_extraction
from .native_pdf_debt_extraction import (
    _MIN_NATIVE_FRAGMENTS,
    _MIN_NATIVE_TEXT_CHARS,
    _pdf_bytes,
    _pdf_status_warning,
    extract_native_pdf_debt_candidates,
    extract_pdf_text_fragments,
    layout_rows,
)
from .structured_debt_extraction import (
    _DebtHtmlParser,
    _image_heavy_html,
    _visual_media_type,
    _visual_warning,
)


class VisualExtractionRequiredError(RuntimeError):
    """Raised when a visual document cannot be represented safely as native text."""


class NativePdfTextPayload(str):
    """String-compatible native PDF text carrying the original PDF bytes.

    Source/reference resolvers can scan this value as ordinary text while debt
    extraction can recover the exact bytes and reconstruct coordinate-based rows.
    """

    pdf_bytes: bytes
    source_url: str

    def __new__(cls, value: str, *, pdf_bytes: bytes, source_url: str):
        obj = str.__new__(cls, value)
        obj.pdf_bytes = pdf_bytes
        obj.source_url = source_url
        return obj


def _native_payload(client: Any, url: str) -> NativePdfTextPayload:
    content = _pdf_bytes(client, url)
    fragments = extract_pdf_text_fragments(content)
    character_count = sum(len(re.sub(r"\s+", "", item.text)) for item in fragments)
    if len(fragments) < _MIN_NATIVE_FRAGMENTS or character_count < _MIN_NATIVE_TEXT_CHARS:
        raise VisualExtractionRequiredError(
            "VISUAL_EXTRACTION_REQUIRED"
            f"|media_type=pdf|url={url}"
            f"|reason=native_text_layer_unavailable_or_too_sparse"
            f"|fragment_count={len(fragments)}|character_count={character_count}"
        )
    rows = layout_rows(fragments)
    text = "\n".join(row.text for row in rows if row.text)
    return NativePdfTextPayload(text, pdf_bytes=content, source_url=url)


def extract_document_source_candidates(
    client: Any,
    document: Any,
    *,
    max_candidates: int = 20,
    text_cache: dict[str, str] | None = None,
) -> tuple[tuple[Any, ...], tuple[Any, ...], tuple[str, ...]]:
    """Extract debt candidates without losing media-type safety.

    Returns `(spans, candidates, warnings)` and is safe to use for documents found
    after the initial SEC packet (source graph, contract identity, named entity,
    foreign closure). PDF bytes are never decoded through `response.text`.
    """

    media_type = _visual_media_type(document)
    if media_type == "pdf":
        try:
            content = _pdf_bytes(client, document.url)
            result = extract_native_pdf_debt_candidates(
                sec_instruments,
                content,
                document,
                max_candidates=max_candidates,
            )
        except Exception as exc:
            return (), (), (
                _visual_warning(
                    document,
                    "pdf",
                    "native_text_probe_failed_and_visual_extraction_is_required",
                ),
                _pdf_status_warning(
                    document,
                    "PDF_NATIVE_TEXT_EXTRACTION_FAILED",
                    reason=str(exc).replace("|", "/"),
                ),
            )
        if not result.native_text_detected:
            return (), (), (
                _visual_warning(
                    document,
                    "pdf",
                    "native_text_layer_unavailable_or_too_sparse",
                ),
                _pdf_status_warning(
                    document,
                    "PDF_NATIVE_TEXT_UNAVAILABLE",
                    page_count=result.page_count,
                    fragment_count=result.fragment_count,
                    character_count=result.extracted_character_count,
                ),
            )
        return result.spans, result.candidates, (
            _pdf_status_warning(
                document,
                "PDF_NATIVE_TEXT_EXTRACTED",
                page_count=result.page_count,
                fragment_count=result.fragment_count,
                character_count=result.extracted_character_count,
                candidate_count=len(result.candidates),
            ),
        )

    if media_type:
        return (), (), (
            _visual_warning(
                document,
                media_type,
                "binary_visual_exhibit_skipped_to_avoid_unstructured_text_association",
            ),
        )

    document_text: str | None = None
    if text_cache is not None:
        document_text = text_cache.get(document.url)
    if document_text is None:
        document_text = sec_instruments._get_text(client, document.url)
        if text_cache is not None:
            text_cache[document.url] = document_text

    parser = _DebtHtmlParser()
    try:
        parser.feed(document_text)
        parser.close()
    except Exception:
        parser = None
    if parser is not None and _image_heavy_html(parser):
        return (), (), (
            _visual_warning(
                document,
                "image_heavy_html",
                "visible_text_too_sparse_for_safe_automatic_debt_extraction",
            ),
        )

    spans, candidates = sec_instruments.extract_source_candidates(
        document_text,
        document,
        max_candidates=max_candidates,
    )
    return tuple(spans), tuple(candidates), ()


def install_document_aware_extraction(module: Any) -> None:
    """Patch SEC helpers before downstream modules bind them.

    This makes source-graph, locator-less-contract and named-entity paths PDF-safe
    without requiring each caller to special-case binary documents. HTML behavior
    remains delegated to the installed structured + complex-table extractors.
    """

    if getattr(module, "_document_aware_extraction_installed", False):
        return

    # Structured extraction is installed immediately before this hook. Extend that
    # HTML path with orientation-aware / multi-row / footnote-aware table parsing,
    # then capture the resulting callable below so every downstream document path
    # sees the same behavior.
    install_complex_table_extraction(module)

    original_get_text = module._get_text
    original_extract = module.extract_source_candidates

    def document_aware_get_text(
        client: Any,
        url: str,
        *,
        accept: str = "text/html,text/plain;q=0.9,*/*;q=0.1",
    ) -> str:
        if str(url).lower().split("?", 1)[0].endswith(".pdf"):
            return _native_payload(client, url)
        return original_get_text(client, url, accept=accept)

    def document_aware_extract_source_candidates(
        document_text: str,
        document: Any,
        *,
        max_candidates: int = 20,
    ) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
        media_type = _visual_media_type(document)
        if media_type == "pdf":
            if not isinstance(document_text, NativePdfTextPayload):
                raise VisualExtractionRequiredError(
                    _visual_warning(
                        document,
                        "pdf",
                        "PDF extraction requires binary native-text payload; unstructured text input refused",
                    )
                )
            result = extract_native_pdf_debt_candidates(
                module,
                document_text.pdf_bytes,
                document,
                max_candidates=max_candidates,
            )
            if not result.native_text_detected:
                raise VisualExtractionRequiredError(
                    _visual_warning(
                        document,
                        "pdf",
                        "native_text_layer_unavailable_or_too_sparse",
                    )
                )
            return result.spans, result.candidates
        if media_type:
            raise VisualExtractionRequiredError(
                _visual_warning(
                    document,
                    media_type,
                    "binary_visual_exhibit_skipped_to_avoid_unstructured_text_association",
                )
            )
        return original_extract(
            document_text,
            document,
            max_candidates=max_candidates,
        )

    document_aware_get_text.__name__ = "_get_text"
    document_aware_extract_source_candidates.__name__ = "extract_source_candidates"
    module._get_text = document_aware_get_text
    module.extract_source_candidates = document_aware_extract_source_candidates
    module._document_aware_extraction_installed = True

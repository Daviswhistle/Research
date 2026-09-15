from __future__ import annotations

from typing import Any

from . import sec_instruments
from .native_pdf_debt_extraction import (
    _pdf_bytes,
    _pdf_status_warning,
    extract_native_pdf_debt_candidates,
)
from .structured_debt_extraction import (
    _DebtHtmlParser,
    _image_heavy_html,
    _visual_media_type,
    _visual_warning,
)


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

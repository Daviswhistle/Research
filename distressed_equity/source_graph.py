from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import re
from typing import Any, Iterable
from urllib.parse import urlparse

from .sec import SecClient, SecFiling
from .sec_debt import html_to_text
from .sec_instruments import (
    FilingDocument,
    SecInstrumentPacket,
    _get_text,
    extract_source_candidates,
    filing_index_url,
    parse_filing_documents,
)

REFERENCE_FORMS = (
    "8-K", "8-K/A", "10-K", "10-K/A", "10-Q", "10-Q/A",
    "S-1", "S-1/A", "S-3", "S-3/A", "S-4", "S-4/A",
)

_REFERENCE_TERMS = (
    "incorporated by reference",
    "incorporated herein by reference",
    "previously filed",
    "filed as exhibit",
    "filed as an exhibit",
    "reference is made to",
)
_ACCESSION_RE = re.compile(r"\b(\d{10}-\d{2}-\d{6})\b")
_ARCHIVE_ACCESSION_RE = re.compile(r"/Archives/edgar/data/\d+/(\d{18})/", re.IGNORECASE)
_EXHIBIT_RE = re.compile(r"\bExhibit\s+(?P<exhibit>\d{1,3}(?:\.[0-9A-Za-z-]+)?)\b", re.IGNORECASE)
_FORM_RE = re.compile(
    r"\bForm\s+(?P<form>8-K(?:/A)?|10-K(?:/A)?|10-Q(?:/A)?|S-1(?:/A)?|S-3(?:/A)?|S-4(?:/A)?)\b",
    re.IGNORECASE,
)
_FILED_DATE_RE = re.compile(
    r"\bfiled(?:\s+with\s+the\s+(?:SEC|Commission))?(?:\s+on)?\s+"
    r"(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"(?P<day>\d{1,2}),\s+(?P<year>20\d{2})\b",
    re.IGNORECASE,
)
_NUMERIC_DATE_RE = re.compile(
    r"\bfiled(?:\s+on)?\s+(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>20\d{2})\b",
    re.IGNORECASE,
)
_SEC_URL_RE = re.compile(r"https?://(?:www\.)?sec\.gov/Archives/edgar/data/[^\s\"'<>]+", re.IGNORECASE)

_MONTHS = {
    name.lower(): number
    for number, name in enumerate(
        (
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ),
        1,
    )
}


@dataclass(frozen=True)
class SourceReference:
    reference_id: str
    source_node_id: str
    source_accession: str
    source_published_on: date
    reference_text: str
    cited_accession: str | None = None
    cited_form: str | None = None
    cited_filing_date: date | None = None
    cited_exhibit: str | None = None
    cited_url: str | None = None
    confidence: str = "candidate"


@dataclass(frozen=True)
class SourceGraphNode:
    node_id: str
    node_type: str
    accession_number: str
    filing_date: date
    form: str
    url: str
    document_type: str | None = None
    description: str | None = None
    depth: int = 0


@dataclass(frozen=True)
class SourceGraphEdge:
    edge_id: str
    from_node_id: str
    reference_id: str
    to_node_id: str | None
    status: str
    reason: str
    target_accession: str | None = None
    target_exhibit: str | None = None


@dataclass(frozen=True)
class SourceDocumentGraph:
    cik: str
    analysis_date: date
    nodes: tuple[SourceGraphNode, ...]
    references: tuple[SourceReference, ...]
    edges: tuple[SourceGraphEdge, ...]
    resolved_documents: tuple[FilingDocument, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ExpandedInstrumentPacket:
    packet: SecInstrumentPacket
    graph: SourceDocumentGraph


def _accession_from_archive_url(url: str | None) -> str | None:
    if not url:
        return None
    match = _ARCHIVE_ACCESSION_RE.search(url)
    if not match:
        return None
    digits = match.group(1)
    return f"{digits[:10]}-{digits[10:12]}-{digits[12:]}"


def _filename_from_url(url: str | None) -> str | None:
    if not url:
        return None
    path = urlparse(url).path
    name = path.rsplit("/", 1)[-1]
    return name or None


def _parse_reference_date(text: str) -> date | None:
    match = _FILED_DATE_RE.search(text)
    if match:
        return date(
            int(match.group("year")),
            _MONTHS[match.group("month").lower()],
            int(match.group("day")),
        )
    match = _NUMERIC_DATE_RE.search(text)
    if match:
        try:
            return date(int(match.group("year")), int(match.group("month")), int(match.group("day")))
        except ValueError:
            return None
    return None


def _normalize_form(value: str | None) -> str | None:
    return value.upper() if value else None


def _normalize_exhibit(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.upper().replace("EX-", "").replace("EXHIBIT", "").strip()
    return cleaned or None


def _reference_blocks(text: str) -> Iterable[str]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    seen: set[str] = set()
    for idx, line in enumerate(lines):
        window = " ".join(lines[max(0, idx - 1): min(len(lines), idx + 3)])
        lower = window.lower()
        has_trigger = any(term in lower for term in _REFERENCE_TERMS)
        has_locator = bool(_ACCESSION_RE.search(window) or _SEC_URL_RE.search(window))
        has_exhibit_filing_pair = bool(_EXHIBIT_RE.search(window) and _FORM_RE.search(window))
        if not (has_trigger or has_locator or has_exhibit_filing_pair):
            continue
        normalized = re.sub(r"\s+", " ", window).strip()
        if len(normalized) < 35 or normalized in seen:
            continue
        seen.add(normalized)
        yield normalized[:3000]


def extract_source_references(
    text: str,
    *,
    source_node_id: str,
    source_accession: str,
    source_published_on: date,
) -> tuple[SourceReference, ...]:
    plain = html_to_text(text)
    references: list[SourceReference] = []
    for block in _reference_blocks(plain):
        accession_match = _ACCESSION_RE.search(block)
        url_match = _SEC_URL_RE.search(block)
        cited_url = url_match.group(0).rstrip(".,);") if url_match else None
        cited_accession = accession_match.group(1) if accession_match else _accession_from_archive_url(cited_url)
        form_match = _FORM_RE.search(block)
        exhibit_match = _EXHIBIT_RE.search(block)
        cited_date = _parse_reference_date(block)

        confidence = "medium"
        if cited_accession:
            confidence = "high"
        elif cited_date and form_match and exhibit_match:
            confidence = "high"
        elif form_match and exhibit_match:
            confidence = "medium"
        else:
            confidence = "low"

        references.append(
            SourceReference(
                reference_id=f"{source_node_id}:r{len(references) + 1}",
                source_node_id=source_node_id,
                source_accession=source_accession,
                source_published_on=source_published_on,
                reference_text=block,
                cited_accession=cited_accession,
                cited_form=_normalize_form(form_match.group("form")) if form_match else None,
                cited_filing_date=cited_date,
                cited_exhibit=_normalize_exhibit(exhibit_match.group("exhibit")) if exhibit_match else None,
                cited_url=cited_url,
                confidence=confidence,
            )
        )
    return tuple(references)


def _filing_node(filing: SecFiling, *, depth: int) -> SourceGraphNode | None:
    if not filing.archive_url:
        return None
    return SourceGraphNode(
        node_id=f"filing:{filing.accession_number}:primary",
        node_type="filing_primary",
        accession_number=filing.accession_number,
        filing_date=filing.filing_date,
        form=filing.form,
        url=filing.archive_url,
        document_type=filing.form,
        description="primary filing document",
        depth=depth,
    )


def _document_node(document: FilingDocument, *, depth: int) -> SourceGraphNode:
    return SourceGraphNode(
        node_id=f"exhibit:{document.source_accession}:{document.sequence or 0}:{document.document}",
        node_type="exhibit",
        accession_number=document.source_accession,
        filing_date=document.filing_date,
        form=document.filing_form,
        url=document.url,
        document_type=document.document_type,
        description=document.description,
        depth=depth,
    )


def _match_target_filing(reference: SourceReference, filings: tuple[SecFiling, ...]) -> tuple[SecFiling | None, str]:
    if reference.cited_accession:
        matches = [item for item in filings if item.accession_number == reference.cited_accession]
        if len(matches) == 1:
            return matches[0], "exact accession"
        if not matches:
            return None, "cited accession not found before cutoff"
        return None, "multiple filings matched cited accession"

    candidates = list(filings)
    if reference.cited_filing_date is not None:
        candidates = [item for item in candidates if item.filing_date == reference.cited_filing_date]
    if reference.cited_form is not None:
        candidates = [item for item in candidates if item.form.upper() == reference.cited_form]
    if len(candidates) == 1 and (reference.cited_filing_date is not None or reference.cited_form is not None):
        return candidates[0], "form/date match"
    if not candidates:
        return None, "no filing matched cited form/date"
    return None, "reference is ambiguous without exact accession"


def _match_target_document(reference: SourceReference, documents: tuple[FilingDocument, ...]) -> tuple[FilingDocument | None, str]:
    cited_filename = _filename_from_url(reference.cited_url)
    if cited_filename:
        exact_file = [item for item in documents if item.document == cited_filename]
        if len(exact_file) == 1:
            return exact_file[0], "exact document filename"

    exhibit = _normalize_exhibit(reference.cited_exhibit)
    if exhibit:
        target_type = f"EX-{exhibit}"
        exact = [item for item in documents if item.document_type.upper() == target_type]
        if len(exact) == 1:
            return exact[0], "exact exhibit type"
        prefixed = [item for item in documents if item.document_type.upper().startswith(target_type)]
        if len(prefixed) == 1:
            return prefixed[0], "exhibit type prefix"
        if not exact and not prefixed:
            return None, f"cited exhibit {exhibit} not found in target filing"
        return None, f"cited exhibit {exhibit} matched multiple documents"
    return None, "reference resolves filing but not a specific exhibit"


def resolve_source_document_graph(
    client: SecClient,
    *,
    cik: str,
    analysis_date: date,
    seed_filings: Iterable[SecFiling],
    seed_documents: Iterable[FilingDocument] = (),
    max_depth: int = 3,
    max_nodes: int = 80,
) -> SourceDocumentGraph:
    if max_depth < 0:
        raise ValueError("max_depth cannot be negative")
    if max_nodes <= 0:
        raise ValueError("max_nodes must be positive")

    all_filings = client.filings_as_of(cik, analysis_date, forms=REFERENCE_FORMS)
    all_filings = tuple(item for item in all_filings if item.filing_date <= analysis_date)
    nodes: dict[str, SourceGraphNode] = {}
    references: list[SourceReference] = []
    edges: list[SourceGraphEdge] = []
    resolved_documents: dict[str, FilingDocument] = {}
    warnings: list[str] = []
    queue: list[SourceGraphNode] = []

    for filing in seed_filings:
        if filing.filing_date > analysis_date:
            warnings.append(f"seed filing after cutoff ignored: {filing.accession_number}")
            continue
        node = _filing_node(filing, depth=0)
        if node and node.node_id not in nodes:
            nodes[node.node_id] = node
            queue.append(node)
    for document in seed_documents:
        if document.filing_date > analysis_date:
            continue
        node = _document_node(document, depth=0)
        if node.node_id not in nodes:
            nodes[node.node_id] = node
            queue.append(node)

    index_cache: dict[str, tuple[FilingDocument, ...]] = {}
    text_cache: dict[str, str] = {}
    seen_reference_keys: set[tuple[str, str]] = set()

    while queue and len(nodes) < max_nodes:
        source = queue.pop(0)
        if source.depth >= max_depth:
            continue
        try:
            source_text = text_cache.get(source.url)
            if source_text is None:
                source_text = _get_text(client, source.url)
                text_cache[source.url] = source_text
        except Exception as exc:
            warnings.append(f"failed to fetch graph source {source.url}: {exc}")
            continue

        extracted = extract_source_references(
            source_text,
            source_node_id=source.node_id,
            source_accession=source.accession_number,
            source_published_on=source.filing_date,
        )
        for reference in extracted:
            ref_key = (reference.source_node_id, reference.reference_text)
            if ref_key in seen_reference_keys:
                continue
            seen_reference_keys.add(ref_key)
            references.append(reference)
            edge_id = f"edge:{len(edges) + 1}"

            if reference.cited_filing_date and reference.cited_filing_date > analysis_date:
                edges.append(SourceGraphEdge(edge_id, source.node_id, reference.reference_id, None, "blocked_cutoff", "cited filing date is after analysis cutoff", reference.cited_accession, reference.cited_exhibit))
                continue
            target_filing, filing_reason = _match_target_filing(reference, all_filings)
            if target_filing is None:
                edges.append(SourceGraphEdge(edge_id, source.node_id, reference.reference_id, None, "unresolved", filing_reason, reference.cited_accession, reference.cited_exhibit))
                continue
            if target_filing.filing_date > analysis_date:
                edges.append(SourceGraphEdge(edge_id, source.node_id, reference.reference_id, None, "blocked_cutoff", "resolved filing is after analysis cutoff", target_filing.accession_number, reference.cited_exhibit))
                continue
            if target_filing.filing_date > source.filing_date:
                edges.append(SourceGraphEdge(edge_id, source.node_id, reference.reference_id, None, "blocked_temporal", "target filing post-dates the source document", target_filing.accession_number, reference.cited_exhibit))
                continue

            filing_node = _filing_node(target_filing, depth=source.depth + 1)
            if filing_node and filing_node.node_id not in nodes and len(nodes) < max_nodes:
                nodes[filing_node.node_id] = filing_node
                queue.append(filing_node)

            docs = index_cache.get(target_filing.accession_number)
            if docs is None:
                try:
                    index_html = _get_text(client, filing_index_url(target_filing))
                    docs = parse_filing_documents(index_html, target_filing)
                    index_cache[target_filing.accession_number] = docs
                except Exception as exc:
                    edges.append(SourceGraphEdge(edge_id, source.node_id, reference.reference_id, filing_node.node_id if filing_node else None, "filing_resolved_document_unavailable", f"target filing resolved but index fetch failed: {exc}", target_filing.accession_number, reference.cited_exhibit))
                    continue

            target_document, document_reason = _match_target_document(reference, docs)
            if target_document is None:
                edges.append(SourceGraphEdge(edge_id, source.node_id, reference.reference_id, filing_node.node_id if filing_node else None, "filing_resolved", document_reason, target_filing.accession_number, reference.cited_exhibit))
                continue

            target_node = _document_node(target_document, depth=source.depth + 1)
            resolved_documents[target_node.node_id] = target_document
            if target_node.node_id not in nodes and len(nodes) < max_nodes:
                nodes[target_node.node_id] = target_node
                queue.append(target_node)
            edges.append(SourceGraphEdge(edge_id, source.node_id, reference.reference_id, target_node.node_id, "resolved", f"{filing_reason}; {document_reason}", target_filing.accession_number, reference.cited_exhibit))

    if queue:
        warnings.append(f"source graph stopped at max_nodes={max_nodes}")
    return SourceDocumentGraph(
        cik=str(cik),
        analysis_date=analysis_date,
        nodes=tuple(nodes.values()),
        references=tuple(references),
        edges=tuple(edges),
        resolved_documents=tuple(resolved_documents.values()),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def expand_instrument_packet_with_references(
    client: SecClient,
    *,
    packet: SecInstrumentPacket,
    cik: str,
    seed_filings: Iterable[SecFiling],
    max_depth: int = 3,
    max_nodes: int = 80,
    max_candidates_per_exhibit: int = 20,
) -> ExpandedInstrumentPacket:
    graph = resolve_source_document_graph(
        client,
        cik=cik,
        analysis_date=packet.analysis_date,
        seed_filings=seed_filings,
        seed_documents=packet.documents,
        max_depth=max_depth,
        max_nodes=max_nodes,
    )
    existing_urls = {item.url for item in packet.documents}
    documents = list(packet.documents)
    spans = list(packet.spans)
    candidates = list(packet.candidates)
    warnings = list(packet.warnings) + list(graph.warnings)

    for document in graph.resolved_documents:
        if document.url in existing_urls:
            continue
        existing_urls.add(document.url)
        documents.append(document)
        try:
            document_text = _get_text(client, document.url)
            new_spans, new_candidates = extract_source_candidates(
                document_text,
                document,
                max_candidates=max_candidates_per_exhibit,
            )
            spans.extend(new_spans)
            candidates.extend(new_candidates)
        except Exception as exc:
            warnings.append(f"failed to extract resolved reference document {document.document}: {exc}")

    expanded = SecInstrumentPacket(
        ticker=packet.ticker,
        company_name=packet.company_name,
        analysis_date=packet.analysis_date,
        documents=tuple(documents),
        spans=tuple(spans),
        candidates=tuple(candidates),
        warnings=tuple(dict.fromkeys(warnings)),
    )
    return ExpandedInstrumentPacket(packet=expanded, graph=graph)


def source_document_graph_to_dict(graph: SourceDocumentGraph) -> dict[str, Any]:
    def convert(value: Any) -> Any:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, tuple):
            return [convert(item) for item in value]
        if isinstance(value, list):
            return [convert(item) for item in value]
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        if hasattr(value, "__dataclass_fields__"):
            return convert(asdict(value))
        return value

    return convert(graph)

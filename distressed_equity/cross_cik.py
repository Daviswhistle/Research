from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import re
from typing import Any, Iterable

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
from .source_graph import (
    REFERENCE_FORMS,
    ExpandedInstrumentPacket,
    SourceGraphNode,
    SourceReference,
    _match_target_document,
    _match_target_filing,
    extract_source_references,
    resolve_source_document_graph,
)

_ARCHIVE_URL_RE = re.compile(
    r"https?://(?:www\.)?sec\.gov/Archives/edgar/data/"
    r"(?P<cik>\d{1,10})/(?P<accession>\d{18})/(?P<document>[^\s\"'<>?#]+)",
    re.IGNORECASE,
)
_EXPLICIT_CIK_RE = re.compile(
    r"\bCIK(?:\s+(?:No\.?|Number))?\s*[:#]?\s*(?P<cik>\d{6,10})\b",
    re.IGNORECASE,
)
_ROLE_RE = re.compile(
    r"\b(?:as\s+(?:the\s+)?)?(?P<role>co[- ]borrower|borrower|issuer|parent\s+guarantor|guarantor)\b",
    re.IGNORECASE,
)
_ENTITY_WITH_CIK_RE = re.compile(
    r"(?P<name>[A-Z][A-Za-z0-9&'’.,()\-/ ]{1,120}?)\s*"
    r"(?:\(|,\s*)?CIK(?:\s+(?:No\.?|Number))?\s*[:#]?\s*(?P<cik>\d{6,10})\)?",
    re.IGNORECASE,
)
_SUBSIDIARY_OF_SOURCE_RE = re.compile(
    r"(?P<name>[A-Z][A-Za-z0-9&'’.,()\-/ ]{1,120}?)\s*"
    r"(?:\(|,\s*)?CIK(?:\s+(?:No\.?|Number))?\s*[:#]?\s*(?P<cik>\d{6,10})\)?"
    r"[^.;]{0,120}?\b(?:a|an)\s+(?P<wholly>wholly[- ]owned\s+)?subsidiary\s+of\s+"
    r"(?:the\s+Company|the\s+Registrant|the\s+Issuer)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CrossCikReference:
    reference_id: str
    source_node_id: str
    source_cik: str
    source_accession: str
    source_published_on: date
    target_cik: str
    reference_text: str
    cited_accession: str | None = None
    cited_form: str | None = None
    cited_filing_date: date | None = None
    cited_exhibit: str | None = None
    cited_url: str | None = None
    confidence: str = "high"


@dataclass(frozen=True)
class CrossCikResolution:
    resolution_id: str
    reference_id: str
    source_node_id: str
    source_cik: str
    target_cik: str
    status: str
    reason: str
    target_accession: str | None = None
    target_document_url: str | None = None


@dataclass(frozen=True)
class LegalEntityNode:
    entity_id: str
    cik: str
    name: str | None = None


@dataclass(frozen=True)
class LegalEntityRole:
    role_id: str
    entity_id: str
    role: str
    context_node_id: str
    source_accession: str
    published_on: date
    evidence_text: str


@dataclass(frozen=True)
class LegalEntityRelation:
    relation_id: str
    from_entity_id: str
    to_entity_id: str
    relation_type: str
    context_node_id: str
    source_accession: str
    published_on: date
    evidence_text: str
    confidence: str = "explicit"


@dataclass(frozen=True)
class CrossCikGraph:
    root_cik: str
    analysis_date: date
    references: tuple[CrossCikReference, ...]
    resolutions: tuple[CrossCikResolution, ...]
    resolved_documents: tuple[FilingDocument, ...]
    entity_nodes: tuple[LegalEntityNode, ...]
    entity_roles: tuple[LegalEntityRole, ...]
    entity_relations: tuple[LegalEntityRelation, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class CrossCikExpandedPacket:
    packet: SecInstrumentPacket
    cross_cik_graph: CrossCikGraph


def normalize_cik_text(value: str | int) -> str:
    digits = re.sub(r"\D", "", str(value))
    if not digits:
        raise ValueError(f"invalid CIK: {value}")
    return digits.zfill(10)


def cik_from_accession(accession: str | None) -> str | None:
    if not accession:
        return None
    match = re.match(r"^(\d{10})-\d{2}-\d{6}$", accession)
    return match.group(1) if match else None


def _accession_from_digits(digits: str) -> str:
    return f"{digits[:10]}-{digits[10:12]}-{digits[12:]}"


def _target_cik_from_source_reference(reference: SourceReference) -> str | None:
    if reference.cited_url:
        match = _ARCHIVE_URL_RE.search(reference.cited_url)
        if match:
            return normalize_cik_text(match.group("cik"))
    match = _EXPLICIT_CIK_RE.search(reference.reference_text)
    if match:
        return normalize_cik_text(match.group("cik"))
    if reference.cited_accession:
        return cik_from_accession(reference.cited_accession)
    return None


def _raw_archive_references(
    raw_text: str,
    *,
    source_node_id: str,
    source_cik: str,
    source_accession: str,
    source_published_on: date,
) -> tuple[CrossCikReference, ...]:
    references: list[CrossCikReference] = []
    seen: set[tuple[str, str]] = set()
    for match in _ARCHIVE_URL_RE.finditer(raw_text):
        target_cik = normalize_cik_text(match.group("cik"))
        if target_cik == source_cik:
            continue
        url = match.group(0)
        accession = _accession_from_digits(match.group("accession"))
        start = max(0, match.start() - 500)
        end = min(len(raw_text), match.end() + 500)
        context = re.sub(r"\s+", " ", html_to_text(raw_text[start:end])).strip()
        key = (target_cik, url)
        if key in seen:
            continue
        seen.add(key)
        references.append(
            CrossCikReference(
                reference_id=f"{source_node_id}:xcik-url-{len(references) + 1}",
                source_node_id=source_node_id,
                source_cik=source_cik,
                source_accession=source_accession,
                source_published_on=source_published_on,
                target_cik=target_cik,
                reference_text=context or url,
                cited_accession=accession,
                cited_url=url,
                confidence="high",
            )
        )
    return tuple(references)


def extract_cross_cik_references(
    raw_text: str,
    *,
    source_node_id: str,
    source_cik: str,
    source_accession: str,
    source_published_on: date,
) -> tuple[CrossCikReference, ...]:
    source_cik = normalize_cik_text(source_cik)
    references: list[CrossCikReference] = list(
        _raw_archive_references(
            raw_text,
            source_node_id=source_node_id,
            source_cik=source_cik,
            source_accession=source_accession,
            source_published_on=source_published_on,
        )
    )
    seen = {(item.target_cik, item.cited_accession, item.cited_url) for item in references}
    for reference in extract_source_references(
        raw_text,
        source_node_id=source_node_id,
        source_accession=source_accession,
        source_published_on=source_published_on,
    ):
        target_cik = _target_cik_from_source_reference(reference)
        if target_cik is None or target_cik == source_cik:
            continue
        key = (target_cik, reference.cited_accession, reference.cited_url)
        if key in seen:
            continue
        seen.add(key)
        references.append(
            CrossCikReference(
                reference_id=f"{source_node_id}:xcik-{len(references) + 1}",
                source_node_id=source_node_id,
                source_cik=source_cik,
                source_accession=source_accession,
                source_published_on=source_published_on,
                target_cik=target_cik,
                reference_text=reference.reference_text,
                cited_accession=reference.cited_accession,
                cited_form=reference.cited_form,
                cited_filing_date=reference.cited_filing_date,
                cited_exhibit=reference.cited_exhibit,
                cited_url=reference.cited_url,
                confidence="high" if reference.cited_accession or reference.cited_url else reference.confidence,
            )
        )
    return tuple(references)


def _source_reference(reference: CrossCikReference) -> SourceReference:
    return SourceReference(
        reference_id=reference.reference_id,
        source_node_id=reference.source_node_id,
        source_accession=reference.source_accession,
        source_published_on=reference.source_published_on,
        reference_text=reference.reference_text,
        cited_accession=reference.cited_accession,
        cited_form=reference.cited_form,
        cited_filing_date=reference.cited_filing_date,
        cited_exhibit=reference.cited_exhibit,
        cited_url=reference.cited_url,
        confidence=reference.confidence,
    )


def _source_scan_tuple(node: SourceGraphNode, root_cik: str) -> tuple[str, str, str, date, str, int]:
    source_cik = cik_from_accession(node.accession_number) or root_cik
    return (
        node.node_id,
        source_cik,
        node.accession_number,
        node.filing_date,
        node.url,
        node.depth,
    )


def _canonical_role(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value.lower().replace("-", " ")).strip()
    if cleaned in {"borrower", "co borrower"}:
        return "borrower"
    if cleaned in {"guarantor", "parent guarantor"}:
        return "guarantor"
    return cleaned


def _clean_entity_name(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip(" ,;:-")
    for marker in (". ", "; ", "\n"):
        if marker in cleaned:
            cleaned = cleaned.split(marker)[-1].strip(" ,;:-")
    cleaned = re.sub(r"^(?:and|among|between)\s+", "", cleaned, flags=re.IGNORECASE)
    return cleaned


def extract_explicit_legal_entity_evidence(
    raw_text: str,
    *,
    source_node_id: str,
    source_cik: str,
    source_accession: str,
    source_published_on: date,
) -> tuple[tuple[LegalEntityNode, ...], tuple[LegalEntityRole, ...], tuple[LegalEntityRelation, ...]]:
    plain = html_to_text(raw_text)
    source_cik = normalize_cik_text(source_cik)
    nodes: dict[str, LegalEntityNode] = {
        f"cik:{source_cik}": LegalEntityNode(entity_id=f"cik:{source_cik}", cik=source_cik)
    }
    roles: list[LegalEntityRole] = []
    relations: list[LegalEntityRelation] = []

    for match in _ENTITY_WITH_CIK_RE.finditer(plain):
        target_cik = normalize_cik_text(match.group("cik"))
        name = _clean_entity_name(match.group("name")) or None
        entity_id = f"cik:{target_cik}"
        nodes[entity_id] = LegalEntityNode(entity_id=entity_id, cik=target_cik, name=name)
        start = max(0, match.start() - 80)
        end = min(len(plain), match.end() + 140)
        context = re.sub(r"\s+", " ", plain[start:end]).strip()
        role_match = _ROLE_RE.search(context)
        if role_match:
            roles.append(
                LegalEntityRole(
                    role_id=f"role:{source_node_id}:{len(roles) + 1}",
                    entity_id=entity_id,
                    role=_canonical_role(role_match.group("role")),
                    context_node_id=source_node_id,
                    source_accession=source_accession,
                    published_on=source_published_on,
                    evidence_text=context,
                )
            )

    for match in _SUBSIDIARY_OF_SOURCE_RE.finditer(plain):
        target_cik = normalize_cik_text(match.group("cik"))
        if target_cik == source_cik:
            continue
        name = _clean_entity_name(match.group("name")) or None
        child_id = f"cik:{target_cik}"
        parent_id = f"cik:{source_cik}"
        nodes[child_id] = LegalEntityNode(entity_id=child_id, cik=target_cik, name=name)
        context = re.sub(r"\s+", " ", match.group(0)).strip()
        relations.append(
            LegalEntityRelation(
                relation_id=f"relation:{source_node_id}:{len(relations) + 1}",
                from_entity_id=child_id,
                to_entity_id=parent_id,
                relation_type="wholly_owned_subsidiary_of" if match.group("wholly") else "subsidiary_of",
                context_node_id=source_node_id,
                source_accession=source_accession,
                published_on=source_published_on,
                evidence_text=context,
            )
        )

    unique_roles = {
        (item.entity_id, item.role, item.context_node_id, item.evidence_text): item
        for item in roles
    }
    unique_relations = {
        (item.from_entity_id, item.to_entity_id, item.relation_type, item.context_node_id, item.evidence_text): item
        for item in relations
    }
    return (
        tuple(nodes.values()),
        tuple(unique_roles.values()),
        tuple(unique_relations.values()),
    )


def resolve_cross_cik_graph(
    client: SecClient,
    *,
    root_cik: str,
    analysis_date: date,
    source_nodes: Iterable[SourceGraphNode],
    max_depth: int = 3,
    max_nodes: int = 80,
) -> CrossCikGraph:
    root_cik = normalize_cik_text(root_cik)
    references: list[CrossCikReference] = []
    resolutions: list[CrossCikResolution] = []
    resolved_documents: dict[str, FilingDocument] = {}
    entity_nodes: dict[str, LegalEntityNode] = {
        f"cik:{root_cik}": LegalEntityNode(entity_id=f"cik:{root_cik}", cik=root_cik)
    }
    entity_roles: list[LegalEntityRole] = []
    entity_relations: list[LegalEntityRelation] = []
    warnings: list[str] = []
    filing_cache: dict[str, tuple[SecFiling, ...]] = {}
    index_cache: dict[tuple[str, str], tuple[FilingDocument, ...]] = {}
    text_cache: dict[str, str] = {}
    seen_sources: set[tuple[str, str]] = set()
    seen_refs: set[tuple[str, str, str | None, str | None]] = set()

    queue = [_source_scan_tuple(node, root_cik) for node in source_nodes if node.depth < max_depth]
    while queue and len(seen_sources) < max_nodes:
        source_node_id, source_cik, source_accession, source_date, source_url, depth = queue.pop(0)
        source_key = (source_cik, source_url)
        if source_key in seen_sources or depth >= max_depth:
            continue
        seen_sources.add(source_key)
        try:
            raw_text = text_cache.get(source_url)
            if raw_text is None:
                raw_text = _get_text(client, source_url)
                text_cache[source_url] = raw_text
        except Exception as exc:
            warnings.append(f"failed cross-CIK source fetch {source_url}: {exc}")
            continue

        new_nodes, new_roles, new_relations = extract_explicit_legal_entity_evidence(
            raw_text,
            source_node_id=source_node_id,
            source_cik=source_cik,
            source_accession=source_accession,
            source_published_on=source_date,
        )
        for node in new_nodes:
            existing = entity_nodes.get(node.entity_id)
            if existing is None or (existing.name is None and node.name):
                entity_nodes[node.entity_id] = node
        entity_roles.extend(new_roles)
        entity_relations.extend(new_relations)

        for reference in extract_cross_cik_references(
            raw_text,
            source_node_id=source_node_id,
            source_cik=source_cik,
            source_accession=source_accession,
            source_published_on=source_date,
        ):
            ref_key = (reference.source_node_id, reference.target_cik, reference.cited_accession, reference.cited_url)
            if ref_key in seen_refs:
                continue
            seen_refs.add(ref_key)
            references.append(reference)
            resolution_id = f"xcik-resolution:{len(resolutions) + 1}"

            if reference.cited_filing_date and reference.cited_filing_date > analysis_date:
                resolutions.append(
                    CrossCikResolution(
                        resolution_id, reference.reference_id, source_node_id, source_cik, reference.target_cik,
                        "blocked_cutoff", "cited filing date is after analysis cutoff",
                        reference.cited_accession, None,
                    )
                )
                continue

            filings = filing_cache.get(reference.target_cik)
            if filings is None:
                try:
                    filings = client.filings_as_of(reference.target_cik, analysis_date, forms=REFERENCE_FORMS)
                    filings = tuple(item for item in filings if item.filing_date <= analysis_date)
                    filing_cache[reference.target_cik] = filings
                except Exception as exc:
                    resolutions.append(
                        CrossCikResolution(
                            resolution_id, reference.reference_id, source_node_id, source_cik, reference.target_cik,
                            "target_cik_unavailable", f"failed target CIK filing lookup: {exc}",
                            reference.cited_accession, None,
                        )
                    )
                    continue

            base_reference = _source_reference(reference)
            target_filing, filing_reason = _match_target_filing(base_reference, filings)
            if target_filing is None:
                resolutions.append(
                    CrossCikResolution(
                        resolution_id, reference.reference_id, source_node_id, source_cik, reference.target_cik,
                        "unresolved", filing_reason, reference.cited_accession, None,
                    )
                )
                continue
            if target_filing.filing_date > source_date:
                resolutions.append(
                    CrossCikResolution(
                        resolution_id, reference.reference_id, source_node_id, source_cik, reference.target_cik,
                        "blocked_temporal", "target filing post-dates the referencing source document",
                        target_filing.accession_number, None,
                    )
                )
                continue

            cache_key = (reference.target_cik, target_filing.accession_number)
            documents = index_cache.get(cache_key)
            if documents is None:
                try:
                    index_html = _get_text(client, filing_index_url(target_filing))
                    documents = parse_filing_documents(index_html, target_filing)
                    index_cache[cache_key] = documents
                except Exception as exc:
                    resolutions.append(
                        CrossCikResolution(
                            resolution_id, reference.reference_id, source_node_id, source_cik, reference.target_cik,
                            "filing_resolved_document_unavailable", f"target filing index fetch failed: {exc}",
                            target_filing.accession_number, None,
                        )
                    )
                    continue

            target_document, document_reason = _match_target_document(base_reference, documents)
            if target_document is None:
                resolutions.append(
                    CrossCikResolution(
                        resolution_id, reference.reference_id, source_node_id, source_cik, reference.target_cik,
                        "filing_resolved", document_reason, target_filing.accession_number, None,
                    )
                )
                continue

            resolved_documents[target_document.url] = target_document
            resolutions.append(
                CrossCikResolution(
                    resolution_id, reference.reference_id, source_node_id, source_cik, reference.target_cik,
                    "resolved_cross_cik", f"explicit target CIK; {filing_reason}; {document_reason}",
                    target_filing.accession_number, target_document.url,
                )
            )

            target_node_id = f"cross-cik:{reference.target_cik}:{target_filing.accession_number}:{target_document.document}"
            queue.append(
                (
                    target_node_id,
                    reference.target_cik,
                    target_filing.accession_number,
                    target_filing.filing_date,
                    target_document.url,
                    depth + 1,
                )
            )

            # Resolve ordinary same-CIK references inside the foreign document too.
            remaining_depth = max_depth - (depth + 1)
            if remaining_depth > 0:
                try:
                    subgraph = resolve_source_document_graph(
                        client,
                        cik=reference.target_cik,
                        analysis_date=analysis_date,
                        seed_filings=(),
                        seed_documents=(target_document,),
                        max_depth=remaining_depth,
                        max_nodes=max(1, max_nodes - len(seen_sources)),
                    )
                    for document in subgraph.resolved_documents:
                        resolved_documents[document.url] = document
                    for node in subgraph.nodes:
                        queue.append(_source_scan_tuple(node, reference.target_cik))
                    warnings.extend(subgraph.warnings)
                except Exception as exc:
                    warnings.append(f"failed same-CIK resolution inside foreign CIK {reference.target_cik}: {exc}")

    if queue:
        warnings.append(f"cross-CIK graph stopped at max_nodes={max_nodes}")

    unique_roles = {
        (item.entity_id, item.role, item.context_node_id, item.evidence_text): item
        for item in entity_roles
    }
    unique_relations = {
        (item.from_entity_id, item.to_entity_id, item.relation_type, item.context_node_id, item.evidence_text): item
        for item in entity_relations
    }
    return CrossCikGraph(
        root_cik=root_cik,
        analysis_date=analysis_date,
        references=tuple(references),
        resolutions=tuple(resolutions),
        resolved_documents=tuple(resolved_documents.values()),
        entity_nodes=tuple(entity_nodes.values()),
        entity_roles=tuple(unique_roles.values()),
        entity_relations=tuple(unique_relations.values()),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def expand_packet_with_cross_cik(
    client: SecClient,
    *,
    expanded: ExpandedInstrumentPacket,
    root_cik: str,
    max_depth: int = 3,
    max_nodes: int = 80,
    max_candidates_per_exhibit: int = 20,
) -> CrossCikExpandedPacket:
    graph = resolve_cross_cik_graph(
        client,
        root_cik=root_cik,
        analysis_date=expanded.packet.analysis_date,
        source_nodes=expanded.graph.nodes,
        max_depth=max_depth,
        max_nodes=max_nodes,
    )
    packet = expanded.packet
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
            raw_text = _get_text(client, document.url)
            new_spans, new_candidates = extract_source_candidates(
                raw_text,
                document,
                max_candidates=max_candidates_per_exhibit,
            )
            spans.extend(new_spans)
            candidates.extend(new_candidates)
        except Exception as exc:
            warnings.append(f"failed to extract cross-CIK debt document {document.document}: {exc}")

    return CrossCikExpandedPacket(
        packet=SecInstrumentPacket(
            ticker=packet.ticker,
            company_name=packet.company_name,
            analysis_date=packet.analysis_date,
            documents=tuple(documents),
            spans=tuple(spans),
            candidates=tuple(candidates),
            warnings=tuple(dict.fromkeys(warnings)),
        ),
        cross_cik_graph=graph,
    )


def cross_cik_graph_to_dict(graph: CrossCikGraph) -> dict[str, Any]:
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

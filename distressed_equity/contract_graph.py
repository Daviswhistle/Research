from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Iterable

from .contract_identity import ContractIdentity, extract_contract_identities, reverse_search_contract_identity
from .sec import SecClient, SecFiling
from .sec_instruments import _get_text, extract_source_candidates
from .source_graph import (
    REFERENCE_FORMS,
    ExpandedInstrumentPacket,
    SourceDocumentGraph,
    SourceGraphEdge,
    SourceGraphNode,
    SourceReference,
    resolve_source_document_graph,
)


def _node_for_document(document, *, depth: int) -> SourceGraphNode:
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


def _synthetic_reference(node: SourceGraphNode, identity: ContractIdentity, ordinal: int) -> SourceReference:
    return SourceReference(
        reference_id=f"{node.node_id}:contract{ordinal}",
        source_node_id=node.node_id,
        source_accession=node.accession_number,
        source_published_on=node.filing_date,
        reference_text=f"{identity.title} dated {identity.execution_date.isoformat()}",
        confidence="medium",
    )


def _merge_subgraph(
    *,
    nodes: dict[str, SourceGraphNode],
    references: list[SourceReference],
    edges: list[SourceGraphEdge],
    resolved_documents: dict[str, object],
    warnings: list[str],
    subgraph: SourceDocumentGraph,
    depth_offset: int,
    max_depth: int,
) -> None:
    known_refs = {item.reference_id for item in references}
    known_edge_keys = {(item.from_node_id, item.reference_id, item.to_node_id, item.status) for item in edges}
    for node in subgraph.nodes:
        adjusted_depth = min(max_depth, depth_offset + node.depth)
        candidate = replace(node, depth=adjusted_depth)
        existing = nodes.get(candidate.node_id)
        if existing is None or candidate.depth < existing.depth:
            nodes[candidate.node_id] = candidate
    for reference in subgraph.references:
        if reference.reference_id not in known_refs:
            references.append(reference)
            known_refs.add(reference.reference_id)
    for edge in subgraph.edges:
        key = (edge.from_node_id, edge.reference_id, edge.to_node_id, edge.status)
        if key in known_edge_keys:
            continue
        edges.append(replace(edge, edge_id=f"edge:{len(edges) + 1}"))
        known_edge_keys.add(key)
    for document in subgraph.resolved_documents:
        resolved_documents[document.url] = document
    warnings.extend(subgraph.warnings)


def enhance_expanded_packet_with_contract_identity(
    client: SecClient,
    *,
    expanded: ExpandedInstrumentPacket,
    cik: str,
    seed_filings: Iterable[SecFiling],
    max_depth: int = 3,
    max_nodes: int = 80,
    max_contract_search_filings: int = 40,
    contract_days_before_execution: int = 30,
    contract_days_after_execution: int = 550,
    max_candidates_per_exhibit: int = 20,
) -> ExpandedInstrumentPacket:
    """Resolve locator-less contract references by exact contract kind + execution date.

    This is deliberately a fallback layer. A contract identity is only linked when
    exactly one SEC exhibit within the bounded historical search window contains
    the same canonical contract kind and exact execution date. Multiple matches
    remain ambiguous and are never ranked to a winner.
    """

    graph = expanded.graph
    packet = expanded.packet
    all_filings = client.filings_as_of(cik, packet.analysis_date, forms=REFERENCE_FORMS)
    all_filings = tuple(item for item in all_filings if item.filing_date <= packet.analysis_date)

    nodes = {item.node_id: item for item in graph.nodes}
    references = list(graph.references)
    edges = list(graph.edges)
    resolved_documents = {item.url: item for item in graph.resolved_documents}
    warnings = list(graph.warnings)
    index_cache: dict[str, tuple] = {}
    text_cache: dict[str, str] = {}

    # Preserve existing explicit-reference resolutions. Contract identity is only
    # added for source documents that do not already have the same identity edge.
    seen_identity_keys: set[tuple[str, str, date]] = set()
    for reference in references:
        for identity in extract_contract_identities(reference.reference_text):
            seen_identity_keys.add((reference.source_node_id, identity.kind, identity.execution_date))

    made_progress = True
    while made_progress and len(nodes) < max_nodes:
        made_progress = False
        current_nodes = sorted(nodes.values(), key=lambda item: (item.depth, item.node_id))
        for source in current_nodes:
            if source.depth >= max_depth:
                continue
            try:
                source_text = text_cache.get(source.url)
                if source_text is None:
                    source_text = _get_text(client, source.url)
                    text_cache[source.url] = source_text
            except Exception as exc:
                warnings.append(f"failed contract-identity source fetch {source.url}: {exc}")
                continue

            identities = extract_contract_identities(source_text)
            for ordinal, identity in enumerate(identities, 1):
                identity_key = (source.node_id, identity.kind, identity.execution_date)
                if identity_key in seen_identity_keys:
                    continue
                seen_identity_keys.add(identity_key)

                search = reverse_search_contract_identity(
                    client,
                    identity=identity,
                    filings=all_filings,
                    source_published_on=source.filing_date,
                    max_filings=max_contract_search_filings,
                    days_before_execution=contract_days_before_execution,
                    days_after_execution=contract_days_after_execution,
                    index_cache=index_cache,
                    text_cache=text_cache,
                )
                warnings.extend(search.warnings)
                candidates = [item for item in search.candidates if item.url != source.url]

                # A contract exhibit normally states its own title and execution
                # date. Do not turn that self-description into a fake unresolved
                # reference edge when the reverse search finds only the source
                # document itself.
                if search.candidates and not candidates and all(item.url == source.url for item in search.candidates):
                    continue

                reference = _synthetic_reference(source, identity, ordinal)
                references.append(reference)
                edge_id = f"edge:{len(edges) + 1}"

                if not candidates:
                    edges.append(
                        SourceGraphEdge(
                            edge_id=edge_id,
                            from_node_id=source.node_id,
                            reference_id=reference.reference_id,
                            to_node_id=None,
                            status="unresolved_contract_identity",
                            reason=(
                                f"no unique SEC exhibit matched {identity.kind} dated "
                                f"{identity.execution_date.isoformat()} within bounded historical search"
                            ),
                        )
                    )
                    continue
                if len(candidates) > 1:
                    edges.append(
                        SourceGraphEdge(
                            edge_id=edge_id,
                            from_node_id=source.node_id,
                            reference_id=reference.reference_id,
                            to_node_id=None,
                            status="ambiguous_contract_identity",
                            reason=(
                                f"{len(candidates)} SEC exhibits matched {identity.kind} dated "
                                f"{identity.execution_date.isoformat()}; no automatic selection"
                            ),
                        )
                    )
                    continue

                document = candidates[0]
                target_depth = source.depth + 1
                if target_depth > max_depth:
                    edges.append(
                        SourceGraphEdge(
                            edge_id=edge_id,
                            from_node_id=source.node_id,
                            reference_id=reference.reference_id,
                            to_node_id=None,
                            status="blocked_depth",
                            reason=f"contract identity resolved beyond max_depth={max_depth}",
                            target_accession=document.source_accession,
                            target_exhibit=document.document_type,
                        )
                    )
                    continue

                target_node = _node_for_document(document, depth=target_depth)
                if target_node.node_id not in nodes and len(nodes) < max_nodes:
                    nodes[target_node.node_id] = target_node
                resolved_documents[document.url] = document
                edges.append(
                    SourceGraphEdge(
                        edge_id=edge_id,
                        from_node_id=source.node_id,
                        reference_id=reference.reference_id,
                        to_node_id=target_node.node_id,
                        status="resolved_by_contract_identity",
                        reason=(
                            f"unique SEC exhibit matched canonical contract kind {identity.kind} "
                            f"and exact execution date {identity.execution_date.isoformat()}"
                        ),
                        target_accession=document.source_accession,
                        target_exhibit=document.document_type,
                    )
                )
                made_progress = True

                # Once a locator-less reference is resolved, let the existing
                # explicit-reference resolver walk any references inside that old
                # exhibit. Depth is adjusted back into the parent graph.
                remaining_depth = max_depth - target_depth
                if remaining_depth > 0:
                    try:
                        subgraph = resolve_source_document_graph(
                            client,
                            cik=cik,
                            analysis_date=packet.analysis_date,
                            seed_filings=(),
                            seed_documents=(document,),
                            max_depth=remaining_depth,
                            max_nodes=max(1, max_nodes - len(nodes)),
                        )
                        _merge_subgraph(
                            nodes=nodes,
                            references=references,
                            edges=edges,
                            resolved_documents=resolved_documents,
                            warnings=warnings,
                            subgraph=subgraph,
                            depth_offset=target_depth,
                            max_depth=max_depth,
                        )
                    except Exception as exc:
                        warnings.append(f"failed nested resolution from contract identity {document.url}: {exc}")

        if len(nodes) >= max_nodes:
            warnings.append(f"contract-identity graph stopped at max_nodes={max_nodes}")
            break

    existing_urls = {item.url for item in packet.documents}
    documents = list(packet.documents)
    spans = list(packet.spans)
    candidates = list(packet.candidates)
    packet_warnings = list(packet.warnings)
    for document in resolved_documents.values():
        if document.url in existing_urls:
            continue
        existing_urls.add(document.url)
        documents.append(document)
        try:
            document_text = text_cache.get(document.url)
            if document_text is None:
                document_text = _get_text(client, document.url)
                text_cache[document.url] = document_text
            new_spans, new_candidates = extract_source_candidates(
                document_text,
                document,
                max_candidates=max_candidates_per_exhibit,
            )
            spans.extend(new_spans)
            candidates.extend(new_candidates)
        except Exception as exc:
            packet_warnings.append(f"failed to extract contract-identity document {document.document}: {exc}")

    new_graph = SourceDocumentGraph(
        cik=graph.cik,
        analysis_date=graph.analysis_date,
        nodes=tuple(nodes.values()),
        references=tuple(references),
        edges=tuple(edges),
        resolved_documents=tuple(resolved_documents.values()),
        warnings=tuple(dict.fromkeys(warnings)),
    )
    new_packet = replace(
        packet,
        documents=tuple(documents),
        spans=tuple(spans),
        candidates=tuple(candidates),
        warnings=tuple(dict.fromkeys(packet_warnings + warnings)),
    )
    return ExpandedInstrumentPacket(packet=new_packet, graph=new_graph)

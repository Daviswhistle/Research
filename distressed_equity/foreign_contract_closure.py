from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Iterable, Protocol

from .contract_graph import enhance_expanded_packet_with_contract_identity
from .cross_cik import (
    CrossCikExpandedPacket,
    CrossCikGraph,
    expand_packet_with_cross_cik,
)
from .legal_name_alias import build_legal_name_alias_graphs
from .sec import SecClient
from .source_graph import (
    ExpandedInstrumentPacket,
    SourceDocumentGraph,
    SourceGraphNode,
    resolve_source_document_graph,
)


class LegalNameGraphLike(Protocol):
    cik: str

    @property
    def alias_names(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class ClosureForeignContractGraph:
    target_cik: str
    entry_document_url: str
    entry_accession: str
    cross_cik_hops: int
    local_depth_budget: int
    graph: SourceDocumentGraph
    entry_global_depth: int
    legal_name_aliases: tuple[str, ...]
    closure_cross_cik_graph: CrossCikGraph | None = None


def _reference_key(item) -> tuple:
    return (
        item.source_node_id,
        item.source_cik,
        item.target_cik,
        item.cited_accession,
        item.cited_url,
        item.reference_text,
    )


def _resolution_key(item) -> tuple:
    return (
        item.source_node_id,
        item.source_cik,
        item.target_cik,
        item.status,
        item.target_accession,
        item.target_document_url,
        item.reason,
    )


def merge_cross_cik_graphs(base: CrossCikGraph, extra: CrossCikGraph) -> CrossCikGraph:
    refs = list(base.references)
    seen_refs = {_reference_key(item) for item in refs}
    for item in extra.references:
        if _reference_key(item) not in seen_refs:
            refs.append(item)
            seen_refs.add(_reference_key(item))

    resolutions = list(base.resolutions)
    seen_resolutions = {_resolution_key(item) for item in resolutions}
    for item in extra.resolutions:
        if _resolution_key(item) in seen_resolutions:
            continue
        resolutions.append(replace(item, resolution_id=f"xcik-resolution:{len(resolutions) + 1}"))
        seen_resolutions.add(_resolution_key(item))

    documents = {item.url: item for item in base.resolved_documents}
    documents.update({item.url: item for item in extra.resolved_documents})

    entities = {item.entity_id: item for item in base.entity_nodes}
    for item in extra.entity_nodes:
        old = entities.get(item.entity_id)
        if old is None or (old.name is None and item.name):
            entities[item.entity_id] = item

    roles = {
        (item.entity_id, item.role, item.context_node_id, item.evidence_text): item
        for item in base.entity_roles
    }
    roles.update({
        (item.entity_id, item.role, item.context_node_id, item.evidence_text): item
        for item in extra.entity_roles
    })
    relations = {
        (item.from_entity_id, item.to_entity_id, item.relation_type, item.context_node_id, item.evidence_text): item
        for item in base.entity_relations
    }
    relations.update({
        (item.from_entity_id, item.to_entity_id, item.relation_type, item.context_node_id, item.evidence_text): item
        for item in extra.entity_relations
    })
    return CrossCikGraph(
        root_cik=base.root_cik,
        analysis_date=base.analysis_date,
        references=tuple(refs),
        resolutions=tuple(resolutions),
        resolved_documents=tuple(documents.values()),
        entity_nodes=tuple(entities.values()),
        entity_roles=tuple(roles.values()),
        entity_relations=tuple(relations.values()),
        warnings=tuple(dict.fromkeys(base.warnings + extra.warnings)),
    )


def _alias_names(graphs: Iterable[LegalNameGraphLike], cik: str) -> tuple[str, ...]:
    for graph in graphs:
        if graph.cik == cik:
            return tuple(dict.fromkeys(name for name in graph.alias_names if name))
    return ()


def _target_depths(graph: CrossCikGraph, source_depths: dict[str, int]) -> dict[tuple[str, str], int]:
    documents = {item.url: item for item in graph.resolved_documents}
    known = dict(source_depths)
    output: dict[tuple[str, str], int] = {}
    changed = True
    while changed:
        changed = False
        for item in graph.resolutions:
            if item.status != "resolved_cross_cik" or not item.target_document_url:
                continue
            source_depth = known.get(item.source_node_id)
            if source_depth is None:
                continue
            target_depth = source_depth + 1
            key = (item.target_cik, item.target_document_url)
            old = output.get(key)
            if old is None or target_depth < old:
                output[key] = target_depth
            document = documents.get(item.target_document_url)
            if document is None:
                continue
            target_node_id = (
                f"cross-cik:{item.target_cik}:{item.target_accession or document.source_accession}:"
                f"{document.document}"
            )
            old_depth = known.get(target_node_id)
            if old_depth is None or target_depth < old_depth:
                known[target_node_id] = target_depth
                changed = True
    return output


def _scan_nodes(graph_item, seen: set[tuple[str, str]], max_depth: int):
    base_depth = getattr(graph_item, "entry_global_depth", None)
    if base_depth is None:
        base_depth = graph_item.cross_cik_hops
    nodes: list[SourceGraphNode] = []
    depths: dict[str, int] = {}
    owners: dict[str, int] = {}
    for node in graph_item.graph.nodes:
        global_depth = base_depth + node.depth
        key = (graph_item.target_cik, node.url)
        if key in seen or global_depth >= max_depth:
            continue
        seen.add(key)
        adjusted = replace(node, depth=global_depth)
        nodes.append(adjusted)
        depths[adjusted.node_id] = global_depth
    return nodes, depths


def expand_foreign_contract_closure(
    client: SecClient,
    *,
    cross_expanded: CrossCikExpandedPacket,
    legal_name_graphs: Iterable[LegalNameGraphLike],
    legacy_expand: Callable,
    max_depth: int = 3,
    max_nodes: int = 80,
    max_contract_search_filings: int = 40,
    contract_days_before_execution: int = 30,
    contract_days_after_execution: int = 550,
    max_candidates_per_exhibit: int = 20,
    max_iterations: int = 8,
):
    """Repeat foreign contract fallback and explicit cross-CIK scans to closure."""

    from .foreign_contract_graph import ForeignContractExpansion

    first = legacy_expand(
        client,
        cross_expanded=cross_expanded,
        legal_name_graphs=legal_name_graphs,
        max_depth=max_depth,
        max_nodes=max_nodes,
        max_contract_search_filings=max_contract_search_filings,
        contract_days_before_execution=contract_days_before_execution,
        contract_days_after_execution=contract_days_after_execution,
        max_candidates_per_exhibit=max_candidates_per_exhibit,
    )
    current = first.cross_expanded
    graphs: list[object] = list(first.graphs)
    legal_graphs = tuple(legal_name_graphs)
    warnings = list(first.warnings)
    seen_scan_nodes: set[tuple[str, str]] = set()
    seen_entries = {(item.target_cik, item.entry_document_url) for item in first.graphs}
    pending = list(first.graphs)
    iterations = 1

    while pending and iterations < max_iterations:
        iterations += 1
        scan_nodes: list[SourceGraphNode] = []
        source_depths: dict[str, int] = {}
        owner_by_node: dict[str, int] = {}
        for owner_index, item in enumerate(pending):
            nodes, depths = _scan_nodes(item, seen_scan_nodes, max_depth)
            for node in nodes:
                owner_by_node[node.node_id] = owner_index
            scan_nodes.extend(nodes)
            source_depths.update(depths)
        if not scan_nodes:
            break

        remaining = max_nodes - len(seen_scan_nodes)
        if remaining <= 0:
            warnings.append(f"cross-CIK closure stopped at max_nodes={max_nodes}")
            break
        scan_nodes = scan_nodes[:remaining]
        synthetic = SourceDocumentGraph(
            cik=current.cross_cik_graph.root_cik,
            analysis_date=current.packet.analysis_date,
            nodes=tuple(scan_nodes),
            references=(),
            edges=(),
            resolved_documents=(),
            warnings=(),
        )
        try:
            crossed = expand_packet_with_cross_cik(
                client,
                expanded=ExpandedInstrumentPacket(packet=current.packet, graph=synthetic),
                root_cik=current.cross_cik_graph.root_cik,
                max_depth=max_depth,
                max_nodes=remaining,
                max_candidates_per_exhibit=max_candidates_per_exhibit,
            )
        except Exception as exc:
            warnings.append(f"failed cross-CIK closure scan: {exc}")
            break

        old_resolution_keys = {_resolution_key(item) for item in current.cross_cik_graph.resolutions}
        fresh_resolutions = [
            item for item in crossed.cross_cik_graph.resolutions
            if _resolution_key(item) not in old_resolution_keys
        ]
        merged = merge_cross_cik_graphs(current.cross_cik_graph, crossed.cross_cik_graph)
        current = CrossCikExpandedPacket(packet=crossed.packet, cross_cik_graph=merged)
        warnings.extend(crossed.cross_cik_graph.warnings)
        if not fresh_resolutions:
            break

        target_depths = _target_depths(crossed.cross_cik_graph, source_depths)
        encountered = (
            {merged.root_cik}
            | {item.target_cik for item in merged.references}
            | {item.cik for item in merged.entity_nodes}
        )
        try:
            legal_graphs, legal_warnings = build_legal_name_alias_graphs(
                client,
                ciks=encountered,
                analysis_date=current.packet.analysis_date,
                existing=legal_graphs,
            )
            warnings.extend(legal_warnings)
        except Exception as exc:
            warnings.append(f"failed legal-name refresh during cross-CIK closure: {exc}")

        documents = {item.url: item for item in merged.resolved_documents}
        next_pending: list[object] = []
        for resolution in fresh_resolutions:
            if resolution.status != "resolved_cross_cik" or not resolution.target_document_url:
                continue
            entry_key = (resolution.target_cik, resolution.target_document_url)
            if entry_key in seen_entries:
                continue
            seen_entries.add(entry_key)
            entry_depth = target_depths.get(entry_key)
            if entry_depth is None:
                warnings.append(
                    f"skipped locator-less fallback for {resolution.target_cik}: global target depth unavailable"
                )
                continue
            local_depth = max_depth - entry_depth
            if local_depth <= 0:
                warnings.append(
                    f"skipped locator-less fallback for {resolution.target_cik}: target depth={entry_depth} exhausts max_depth={max_depth}"
                )
                continue
            entry = documents.get(resolution.target_document_url)
            if entry is None:
                warnings.append(f"closure missing resolved document {resolution.target_document_url}")
                continue
            aliases = _alias_names(legal_graphs, resolution.target_cik)
            try:
                same_graph = resolve_source_document_graph(
                    client,
                    cik=resolution.target_cik,
                    analysis_date=current.packet.analysis_date,
                    seed_filings=(),
                    seed_documents=(entry,),
                    max_depth=local_depth,
                    max_nodes=max(1, max_nodes - len(seen_scan_nodes)),
                )
                expanded = enhance_expanded_packet_with_contract_identity(
                    client,
                    expanded=ExpandedInstrumentPacket(packet=current.packet, graph=same_graph),
                    cik=resolution.target_cik,
                    seed_filings=(),
                    max_depth=local_depth,
                    max_nodes=max(1, max_nodes - len(seen_scan_nodes)),
                    max_contract_search_filings=max_contract_search_filings,
                    contract_days_before_execution=contract_days_before_execution,
                    contract_days_after_execution=contract_days_after_execution,
                    max_candidates_per_exhibit=max_candidates_per_exhibit,
                    alias_groups=(aliases,) if len(aliases) > 1 else (),
                )
            except Exception as exc:
                warnings.append(
                    f"failed closure locator-less fallback for CIK {resolution.target_cik}: {exc}"
                )
                continue
            current = CrossCikExpandedPacket(
                packet=expanded.packet,
                cross_cik_graph=current.cross_cik_graph,
            )
            closure_item = ClosureForeignContractGraph(
                target_cik=resolution.target_cik,
                entry_document_url=entry.url,
                entry_accession=entry.source_accession,
                cross_cik_hops=0,
                local_depth_budget=local_depth,
                graph=expanded.graph,
                entry_global_depth=entry_depth,
                legal_name_aliases=aliases,
                closure_cross_cik_graph=crossed.cross_cik_graph,
            )
            graphs.append(closure_item)
            next_pending.append(closure_item)
        pending = next_pending

    if pending and iterations >= max_iterations:
        warnings.append(f"cross-CIK closure stopped at max_iterations={max_iterations}")

    return ForeignContractExpansion(
        cross_expanded=current,
        graphs=tuple(graphs),
        warnings=tuple(dict.fromkeys(warnings)),
    )

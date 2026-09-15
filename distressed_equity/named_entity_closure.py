from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Protocol

from .cross_cik import (
    CrossCikExpandedPacket,
    cik_from_accession,
    expand_packet_with_cross_cik,
)
from .foreign_contract_closure import merge_cross_cik_graphs
from .foreign_contract_graph import ForeignContractExpansion, expand_foreign_contract_identities
from .legal_name_alias import build_legal_name_alias_graphs
from .named_entity_contracts import (
    ExternalEntityCandidate,
    NamedEntityContractExpansion,
    NamedEntityContractGraph,
    NamedEntityContractResolution,
    resolve_named_entity_contracts,
)
from .sec import SecClient, normalize_cik
from .source_graph import ExpandedInstrumentPacket, SourceDocumentGraph, SourceGraphNode


class LegalNameGraphLike(Protocol):
    cik: str

    @property
    def alias_names(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class NamedEntityCrossCikClosure:
    cross_expanded: CrossCikExpandedPacket
    foreign_expansion: ForeignContractExpansion | None
    legal_name_graphs: tuple[LegalNameGraphLike, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class NamedEntityFixedPointClosure:
    cross_expanded: CrossCikExpandedPacket
    foreign_expansion: ForeignContractExpansion | None
    named_expansion: NamedEntityContractExpansion
    legal_name_graphs: tuple[LegalNameGraphLike, ...]
    iterations: int
    warnings: tuple[str, ...]


def _named_seed_nodes(
    named_expansion: NamedEntityContractExpansion,
    *,
    max_depth: int,
) -> tuple[SourceGraphNode, ...]:
    documents = {item.url: item for item in named_expansion.graph.resolved_documents}
    seeds: dict[tuple[str, str], SourceGraphNode] = {}
    for resolution in named_expansion.graph.resolutions:
        if (
            resolution.status != "resolved_named_entity_contract"
            or not resolution.target_cik
            or not resolution.target_document_url
        ):
            continue
        document = documents.get(resolution.target_document_url)
        if document is None:
            continue
        entry_depth = resolution.source_depth + 1
        if entry_depth >= max_depth:
            continue
        key = (resolution.target_cik, document.url)
        old = seeds.get(key)
        node = SourceGraphNode(
            node_id=(
                f"named-entity:{resolution.target_cik}:"
                f"{document.source_accession}:{document.document}"
            ),
            node_type="exhibit",
            accession_number=document.source_accession,
            filing_date=document.filing_date,
            form=document.filing_form,
            url=document.url,
            document_type=document.document_type,
            description=document.description,
            depth=entry_depth,
        )
        if old is None or node.depth < old.depth:
            seeds[key] = node
    return tuple(seeds.values())


def _resolved_named_target_ciks(expansion: NamedEntityContractExpansion) -> set[str]:
    return {
        normalize_cik(item.target_cik)
        for item in expansion.graph.resolutions
        if item.status == "resolved_named_entity_contract" and item.target_cik
    }


def _refresh_named_target_legal_graphs(
    client: SecClient,
    *,
    expansion: NamedEntityContractExpansion,
    legal_name_graphs: Iterable[LegalNameGraphLike],
) -> tuple[tuple[LegalNameGraphLike, ...], tuple[str, ...]]:
    existing = tuple(legal_name_graphs)
    target_ciks = _resolved_named_target_ciks(expansion)
    if not target_ciks:
        return existing, ()
    try:
        graphs, warnings = build_legal_name_alias_graphs(
            client,
            ciks={item.cik for item in existing} | target_ciks,
            analysis_date=expansion.packet.analysis_date,
            existing=existing,
        )
        return tuple(graphs), tuple(warnings)
    except Exception as exc:
        return existing, (f"failed legal-name refresh for named-entity targets: {exc}",)


def close_named_entity_cross_cik(
    client: SecClient,
    *,
    cross_expanded: CrossCikExpandedPacket,
    named_expansion: NamedEntityContractExpansion,
    legal_name_graphs: Iterable[LegalNameGraphLike],
    existing_foreign_expansion: ForeignContractExpansion | None = None,
    max_depth: int = 3,
    max_nodes: int = 80,
    max_contract_search_filings: int = 40,
    contract_days_before_execution: int = 30,
    contract_days_after_execution: int = 550,
    max_candidates_per_exhibit: int = 20,
) -> NamedEntityCrossCikClosure:
    """Restart explicit cross-CIK closure from uniquely named-entity-resolved exhibits.

    The name-origin edge itself remains separate provenance. Only explicit SEC
    locators found inside the confirmed exhibit can create `resolved_cross_cik`
    edges. The named CIK boundary consumes one global depth level before the
    exhibit is scanned.
    """

    legal_graphs, name_warnings = _refresh_named_target_legal_graphs(
        client,
        expansion=named_expansion,
        legal_name_graphs=legal_name_graphs,
    )
    warnings: list[str] = list(name_warnings)
    seeds = _named_seed_nodes(named_expansion, max_depth=max_depth)
    if not seeds:
        return NamedEntityCrossCikClosure(
            cross_expanded=CrossCikExpandedPacket(
                packet=named_expansion.packet,
                cross_cik_graph=cross_expanded.cross_cik_graph,
            ),
            foreign_expansion=existing_foreign_expansion,
            legal_name_graphs=legal_graphs,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    synthetic = SourceDocumentGraph(
        cik=cross_expanded.cross_cik_graph.root_cik,
        analysis_date=named_expansion.packet.analysis_date,
        nodes=seeds,
        references=(),
        edges=(),
        resolved_documents=(),
        warnings=(),
    )
    try:
        crossed = expand_packet_with_cross_cik(
            client,
            expanded=ExpandedInstrumentPacket(
                packet=named_expansion.packet,
                graph=synthetic,
            ),
            root_cik=cross_expanded.cross_cik_graph.root_cik,
            max_depth=max_depth,
            max_nodes=max_nodes,
            max_candidates_per_exhibit=max_candidates_per_exhibit,
        )
    except Exception as exc:
        warnings.append(f"failed named-entity cross-CIK restart: {exc}")
        return NamedEntityCrossCikClosure(
            cross_expanded=CrossCikExpandedPacket(
                packet=named_expansion.packet,
                cross_cik_graph=cross_expanded.cross_cik_graph,
            ),
            foreign_expansion=existing_foreign_expansion,
            legal_name_graphs=legal_graphs,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    fresh_resolved = [
        item
        for item in crossed.cross_cik_graph.resolutions
        if item.status == "resolved_cross_cik"
    ]
    if not fresh_resolved:
        warnings.extend(crossed.cross_cik_graph.warnings)
        return NamedEntityCrossCikClosure(
            cross_expanded=CrossCikExpandedPacket(
                packet=crossed.packet,
                cross_cik_graph=merge_cross_cik_graphs(
                    cross_expanded.cross_cik_graph,
                    crossed.cross_cik_graph,
                ),
            ),
            foreign_expansion=existing_foreign_expansion,
            legal_name_graphs=legal_graphs,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    merged_graph = merge_cross_cik_graphs(
        cross_expanded.cross_cik_graph,
        crossed.cross_cik_graph,
    )
    merged = CrossCikExpandedPacket(packet=crossed.packet, cross_cik_graph=merged_graph)
    encountered = (
        {merged_graph.root_cik}
        | {item.source_cik for item in merged_graph.references}
        | {item.target_cik for item in merged_graph.references}
        | {item.cik for item in merged_graph.entity_nodes}
    )
    try:
        legal_graphs, legal_warnings = build_legal_name_alias_graphs(
            client,
            ciks=encountered,
            analysis_date=merged.packet.analysis_date,
            existing=legal_graphs,
        )
        warnings.extend(legal_warnings)
    except Exception as exc:
        warnings.append(f"failed legal-name refresh after named-entity cross-CIK restart: {exc}")

    try:
        foreign = expand_foreign_contract_identities(
            client,
            cross_expanded=merged,
            legal_name_graphs=legal_graphs,
            max_depth=max_depth,
            max_nodes=max_nodes,
            max_contract_search_filings=max_contract_search_filings,
            contract_days_before_execution=contract_days_before_execution,
            contract_days_after_execution=contract_days_after_execution,
            max_candidates_per_exhibit=max_candidates_per_exhibit,
        )
        warnings.extend(foreign.warnings)
        return NamedEntityCrossCikClosure(
            cross_expanded=foreign.cross_expanded,
            foreign_expansion=foreign,
            legal_name_graphs=tuple(legal_graphs),
            warnings=tuple(dict.fromkeys(warnings)),
        )
    except Exception as exc:
        warnings.append(f"failed foreign closure after named-entity cross-CIK restart: {exc}")
        return NamedEntityCrossCikClosure(
            cross_expanded=merged,
            foreign_expansion=existing_foreign_expansion,
            legal_name_graphs=tuple(legal_graphs),
            warnings=tuple(dict.fromkeys(warnings)),
        )


def _candidate_key(item: ExternalEntityCandidate) -> tuple[str, str, str]:
    return (item.normalized_name, item.cik, item.source_kind)


def _resolution_key(item: NamedEntityContractResolution) -> tuple:
    return (
        item.source_node_id,
        item.source_cik,
        item.contract_kind,
        item.execution_date,
        item.party_role,
        item.normalized_party_name,
        item.status,
        item.target_cik,
        item.target_document_url,
        item.candidate_ciks,
        item.confirmed_ciks,
    )


def merge_named_entity_graphs(
    base: NamedEntityContractGraph,
    extra: NamedEntityContractGraph,
) -> NamedEntityContractGraph:
    """Merge named-entity passes while preserving one deterministic graph ledger."""

    candidates = {_candidate_key(item): item for item in base.candidates}
    for item in extra.candidates:
        candidates.setdefault(_candidate_key(item), item)

    resolutions = list(base.resolutions)
    seen = {_resolution_key(item) for item in resolutions}
    for item in extra.resolutions:
        key = _resolution_key(item)
        if key in seen:
            continue
        resolutions.append(
            replace(item, resolution_id=f"named-entity-resolution:{len(resolutions) + 1}")
        )
        seen.add(key)

    documents = {item.url: item for item in base.resolved_documents}
    documents.update({item.url: item for item in extra.resolved_documents})
    return NamedEntityContractGraph(
        analysis_date=base.analysis_date,
        candidates=tuple(candidates.values()),
        resolutions=tuple(resolutions),
        resolved_documents=tuple(documents.values()),
        warnings=tuple(dict.fromkeys(base.warnings + extra.warnings)),
    )


def _source_key(node: SourceGraphNode, root_cik: str) -> tuple[str, str]:
    return (cik_from_accession(node.accession_number) or normalize_cik(root_cik), node.url)


def _post_explicit_named_sources(
    cross_expanded: CrossCikExpandedPacket,
    foreign_expansion: ForeignContractExpansion | None,
    *,
    max_depth: int,
) -> tuple[SourceGraphNode, ...]:
    """Return source nodes exposed by explicit/foreign closure, not name-only targets.

    This intentionally prevents direct name-only -> name-only recursion. A new
    named pass is enabled only after an explicit SEC locator hop has exposed a
    cross-CIK entry (plus any same-CIK/locator-less documents reachable from it).
    """

    chosen: dict[tuple[str, str], SourceGraphNode] = {}
    documents = {item.url: item for item in cross_expanded.cross_cik_graph.resolved_documents}
    for resolution in cross_expanded.cross_cik_graph.resolutions:
        if (
            resolution.status != "resolved_cross_cik"
            or not resolution.target_document_url
            or resolution.target_depth is None
            or resolution.target_depth >= max_depth
        ):
            continue
        document = documents.get(resolution.target_document_url)
        if document is None:
            continue
        key = (normalize_cik(resolution.target_cik), document.url)
        node = SourceGraphNode(
            node_id=(
                f"named-after-cross:{resolution.target_cik}:"
                f"{document.source_accession}:{document.document}"
            ),
            node_type="exhibit",
            accession_number=document.source_accession,
            filing_date=document.filing_date,
            form=document.filing_form,
            url=document.url,
            document_type=document.document_type,
            description=document.description,
            depth=resolution.target_depth,
        )
        old = chosen.get(key)
        if old is None or node.depth < old.depth:
            chosen[key] = node

    if foreign_expansion is not None:
        for item in foreign_expansion.graphs:
            entry_depth = getattr(item, "entry_global_depth", None)
            if entry_depth is None:
                continue
            for node in item.graph.nodes:
                global_depth = entry_depth + node.depth
                if global_depth >= max_depth:
                    continue
                adjusted = replace(node, depth=global_depth)
                key = (normalize_cik(item.target_cik), adjusted.url)
                old = chosen.get(key)
                if old is None or adjusted.depth < old.depth:
                    chosen[key] = adjusted

    return tuple(sorted(chosen.values(), key=lambda item: (item.depth, item.node_id)))


def _new_named_target_pass(
    expansion: NamedEntityContractExpansion,
    seen_targets: set[tuple[str, str]],
) -> NamedEntityContractExpansion | None:
    resolutions: list[NamedEntityContractResolution] = []
    target_urls: set[str] = set()
    for item in expansion.graph.resolutions:
        if (
            item.status != "resolved_named_entity_contract"
            or not item.target_cik
            or not item.target_document_url
        ):
            continue
        key = (normalize_cik(item.target_cik), item.target_document_url)
        if key in seen_targets:
            continue
        seen_targets.add(key)
        resolutions.append(item)
        target_urls.add(item.target_document_url)
    if not resolutions:
        return None
    documents = tuple(
        item for item in expansion.graph.resolved_documents if item.url in target_urls
    )
    graph = NamedEntityContractGraph(
        analysis_date=expansion.graph.analysis_date,
        candidates=(),
        resolutions=tuple(resolutions),
        resolved_documents=documents,
        warnings=(),
    )
    return NamedEntityContractExpansion(packet=expansion.packet, graph=graph)


def close_named_entity_fixed_point(
    client: SecClient,
    *,
    cross_expanded: CrossCikExpandedPacket,
    named_expansion: NamedEntityContractExpansion,
    initial_source_nodes: Iterable[SourceGraphNode],
    legal_name_graphs: Iterable[LegalNameGraphLike],
    existing_foreign_expansion: ForeignContractExpansion | None = None,
    max_depth: int = 3,
    max_nodes: int = 80,
    max_contract_search_filings: int = 40,
    contract_days_before_execution: int = 30,
    contract_days_after_execution: int = 550,
    max_candidates_per_exhibit: int = 20,
    max_iterations: int = 8,
) -> NamedEntityFixedPointClosure:
    """Alternate named-entity and explicit/foreign closure until no new path remains.

    Safety invariant: a second name-origin edge is never opened directly from a
    name-origin target. Each additional named pass must be fed by a document that
    became reachable through an explicit SEC locator hop (and its bounded
    same-CIK/locator-less closure). Global depth, a unique-source node budget and
    an iteration cap bound the process.
    """

    if max_depth < 0:
        raise ValueError("max_depth cannot be negative")
    if max_nodes <= 0:
        raise ValueError("max_nodes must be positive")
    if max_iterations <= 0:
        raise ValueError("max_iterations must be positive")

    root_cik = normalize_cik(cross_expanded.cross_cik_graph.root_cik)
    current_cross = CrossCikExpandedPacket(
        packet=named_expansion.packet,
        cross_cik_graph=cross_expanded.cross_cik_graph,
    )
    current_foreign = existing_foreign_expansion
    legal_graphs = tuple(legal_name_graphs)
    aggregate_graph = named_expansion.graph
    warnings: list[str] = list(named_expansion.graph.warnings)
    seen_source_keys = {
        _source_key(item, root_cik) for item in tuple(initial_source_nodes)
    }
    seen_named_targets: set[tuple[str, str]] = set()
    pending = _new_named_target_pass(named_expansion, seen_named_targets)
    iterations = 0

    while pending is not None:
        if iterations >= max_iterations:
            warnings.append(
                f"named-entity fixed point stopped at max_iterations={max_iterations}"
            )
            break
        iterations += 1

        closure = close_named_entity_cross_cik(
            client,
            cross_expanded=current_cross,
            named_expansion=pending,
            legal_name_graphs=legal_graphs,
            existing_foreign_expansion=current_foreign,
            max_depth=max_depth,
            max_nodes=max_nodes,
            max_contract_search_filings=max_contract_search_filings,
            contract_days_before_execution=contract_days_before_execution,
            contract_days_after_execution=contract_days_after_execution,
            max_candidates_per_exhibit=max_candidates_per_exhibit,
        )
        current_cross = closure.cross_expanded
        current_foreign = closure.foreign_expansion
        legal_graphs = tuple(closure.legal_name_graphs)
        warnings.extend(closure.warnings)

        available = _post_explicit_named_sources(
            current_cross,
            current_foreign,
            max_depth=max_depth,
        )
        fresh = [
            item for item in available
            if _source_key(item, root_cik) not in seen_source_keys
        ]
        remaining = max_nodes - len(seen_source_keys)
        if remaining <= 0:
            if fresh:
                warnings.append(
                    f"named-entity fixed point stopped at max_nodes={max_nodes}"
                )
            break
        fresh = fresh[:remaining]
        if not fresh:
            break
        for item in fresh:
            seen_source_keys.add(_source_key(item, root_cik))

        next_named = resolve_named_entity_contracts(
            client,
            packet=current_cross.packet,
            source_nodes=tuple(fresh),
            root_cik=root_cik,
            known_legal_name_graphs=legal_graphs,
            max_contract_search_filings=max_contract_search_filings,
            contract_days_before_execution=contract_days_before_execution,
            contract_days_after_execution=contract_days_after_execution,
            max_candidates_per_exhibit=max_candidates_per_exhibit,
        )
        warnings.extend(next_named.graph.warnings)
        aggregate_graph = merge_named_entity_graphs(aggregate_graph, next_named.graph)
        current_cross = CrossCikExpandedPacket(
            packet=next_named.packet,
            cross_cik_graph=current_cross.cross_cik_graph,
        )
        pending = _new_named_target_pass(next_named, seen_named_targets)

    aggregate = NamedEntityContractExpansion(
        packet=current_cross.packet,
        graph=aggregate_graph,
    )
    return NamedEntityFixedPointClosure(
        cross_expanded=current_cross,
        foreign_expansion=current_foreign,
        named_expansion=aggregate,
        legal_name_graphs=legal_graphs,
        iterations=iterations,
        warnings=tuple(dict.fromkeys(warnings)),
    )

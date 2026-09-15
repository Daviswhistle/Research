from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from .cross_cik import CrossCikExpandedPacket, expand_packet_with_cross_cik
from .foreign_contract_closure import merge_cross_cik_graphs
from .foreign_contract_graph import ForeignContractExpansion, expand_foreign_contract_identities
from .legal_name_alias import build_legal_name_alias_graphs
from .named_entity_contracts import NamedEntityContractExpansion
from .sec import SecClient
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

    legal_graphs = tuple(legal_name_graphs)
    warnings: list[str] = []
    seeds = _named_seed_nodes(named_expansion, max_depth=max_depth)
    if not seeds:
        return NamedEntityCrossCikClosure(
            cross_expanded=CrossCikExpandedPacket(
                packet=named_expansion.packet,
                cross_cik_graph=cross_expanded.cross_cik_graph,
            ),
            foreign_expansion=existing_foreign_expansion,
            legal_name_graphs=legal_graphs,
            warnings=(),
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
            warnings=tuple(warnings),
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

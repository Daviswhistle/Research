from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Iterable, Protocol

from .contract_graph import enhance_expanded_packet_with_contract_identity
from .cross_cik import CrossCikExpandedPacket
from .sec import SecClient
from .source_graph import ExpandedInstrumentPacket, SourceDocumentGraph, resolve_source_document_graph


class LegalNameGraphLike(Protocol):
    cik: str

    @property
    def alias_names(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class ForeignContractGraph:
    target_cik: str
    entry_document_url: str
    entry_accession: str
    cross_cik_hops: int
    local_depth_budget: int
    graph: SourceDocumentGraph


@dataclass(frozen=True)
class ForeignContractExpansion:
    cross_expanded: CrossCikExpandedPacket
    graphs: tuple[ForeignContractGraph, ...]
    warnings: tuple[str, ...]


def _resolved_cik_hops(cross_expanded: CrossCikExpandedPacket) -> dict[str, int]:
    graph = cross_expanded.cross_cik_graph
    hops = {graph.root_cik: 0}
    changed = True
    while changed:
        changed = False
        for item in graph.resolutions:
            if item.status != "resolved_cross_cik" or item.source_cik not in hops:
                continue
            candidate = hops[item.source_cik] + 1
            previous = hops.get(item.target_cik)
            if previous is None or candidate < previous:
                hops[item.target_cik] = candidate
                changed = True
    return hops


def _alias_groups_for_cik(
    legal_name_graphs: Iterable[LegalNameGraphLike],
    cik: str,
) -> tuple[tuple[str, ...], ...]:
    for graph in legal_name_graphs:
        if graph.cik != cik:
            continue
        names = tuple(dict.fromkeys(name for name in graph.alias_names if name))
        return (names,) if len(names) > 1 else ()
    return ()


def expand_foreign_contract_identities(
    client: SecClient,
    *,
    cross_expanded: CrossCikExpandedPacket,
    legal_name_graphs: Iterable[LegalNameGraphLike],
    max_depth: int = 3,
    max_nodes: int = 80,
    max_contract_search_filings: int = 40,
    contract_days_before_execution: int = 30,
    contract_days_after_execution: int = 550,
    max_candidates_per_exhibit: int = 20,
) -> ForeignContractExpansion:
    """Run locator-less contract fallback inside explicitly resolved foreign CIKs.

    The foreign CIK must already have been proven by the cross-CIK resolver. Each
    entry document is then treated as a same-CIK seed and searched only against
    that foreign CIK's point-in-time filing universe. Legal-name aliases are also
    selected by exact CIK, so root-company aliases cannot leak into a foreign
    borrower's contract identity.
    """

    if max_depth < 0:
        raise ValueError("max_depth cannot be negative")
    if max_nodes <= 0:
        raise ValueError("max_nodes must be positive")

    graph = cross_expanded.cross_cik_graph
    packet = cross_expanded.packet
    legal_name_graphs = tuple(legal_name_graphs)
    documents_by_url = {item.url: item for item in graph.resolved_documents}
    hops = _resolved_cik_hops(cross_expanded)
    warnings: list[str] = []
    outputs: list[ForeignContractGraph] = []
    seen_entries: set[tuple[str, str]] = set()
    seen_nodes: set[tuple[str, str]] = set()
    current_packet = packet

    for resolution in graph.resolutions:
        if (
            resolution.status != "resolved_cross_cik"
            or not resolution.target_document_url
            or resolution.target_cik == graph.root_cik
        ):
            continue
        entry_key = (resolution.target_cik, resolution.target_document_url)
        if entry_key in seen_entries:
            continue
        seen_entries.add(entry_key)

        entry = documents_by_url.get(resolution.target_document_url)
        if entry is None:
            warnings.append(
                f"foreign contract fallback missing resolved document: {resolution.target_document_url}"
            )
            continue

        cross_hops = hops.get(resolution.target_cik, 1)
        local_depth = max_depth - cross_hops
        if local_depth <= 0:
            warnings.append(
                f"foreign contract fallback skipped {entry.url}: cross-CIK hops={cross_hops} exhaust max_depth={max_depth}"
            )
            continue

        remaining_nodes = max_nodes - len(seen_nodes)
        if remaining_nodes <= 0:
            warnings.append(f"foreign contract fallback stopped at max_nodes={max_nodes}")
            break

        try:
            same_cik_graph = resolve_source_document_graph(
                client,
                cik=resolution.target_cik,
                analysis_date=packet.analysis_date,
                seed_filings=(),
                seed_documents=(entry,),
                max_depth=local_depth,
                max_nodes=remaining_nodes,
            )
            expanded = enhance_expanded_packet_with_contract_identity(
                client,
                expanded=ExpandedInstrumentPacket(packet=current_packet, graph=same_cik_graph),
                cik=resolution.target_cik,
                seed_filings=(),
                max_depth=local_depth,
                max_nodes=remaining_nodes,
                max_contract_search_filings=max_contract_search_filings,
                contract_days_before_execution=contract_days_before_execution,
                contract_days_after_execution=contract_days_after_execution,
                max_candidates_per_exhibit=max_candidates_per_exhibit,
                alias_groups=_alias_groups_for_cik(legal_name_graphs, resolution.target_cik),
            )
        except Exception as exc:
            warnings.append(
                f"failed foreign locator-less contract fallback for CIK {resolution.target_cik} from {entry.url}: {exc}"
            )
            continue

        current_packet = expanded.packet
        for node in expanded.graph.nodes:
            seen_nodes.add((resolution.target_cik, node.url))
        warnings.extend(expanded.graph.warnings)
        outputs.append(
            ForeignContractGraph(
                target_cik=resolution.target_cik,
                entry_document_url=entry.url,
                entry_accession=entry.source_accession,
                cross_cik_hops=cross_hops,
                local_depth_budget=local_depth,
                graph=expanded.graph,
            )
        )

    return ForeignContractExpansion(
        cross_expanded=CrossCikExpandedPacket(
            packet=current_packet,
            cross_cik_graph=graph,
        ),
        graphs=tuple(outputs),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def foreign_contract_expansion_to_dict(expansion: ForeignContractExpansion) -> dict[str, Any]:
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

    return {
        "graphs": convert(expansion.graphs),
        "warnings": list(expansion.warnings),
    }


_legacy_expand_foreign_contract_identities = expand_foreign_contract_identities


def expand_foreign_contract_identities(
    client: SecClient,
    *,
    cross_expanded: CrossCikExpandedPacket,
    legal_name_graphs: Iterable[LegalNameGraphLike],
    max_depth: int = 3,
    max_nodes: int = 80,
    max_contract_search_filings: int = 40,
    contract_days_before_execution: int = 30,
    contract_days_after_execution: int = 550,
    max_candidates_per_exhibit: int = 20,
    max_iterations: int = 8,
) -> ForeignContractExpansion:
    """Run the legacy foreign pass, then close newly exposed cross-CIK links."""

    from .foreign_contract_closure import expand_foreign_contract_closure

    return expand_foreign_contract_closure(
        client,
        cross_expanded=cross_expanded,
        legal_name_graphs=legal_name_graphs,
        legacy_expand=_legacy_expand_foreign_contract_identities,
        max_depth=max_depth,
        max_nodes=max_nodes,
        max_contract_search_filings=max_contract_search_filings,
        contract_days_before_execution=contract_days_before_execution,
        contract_days_after_execution=contract_days_after_execution,
        max_candidates_per_exhibit=max_candidates_per_exhibit,
        max_iterations=max_iterations,
    )

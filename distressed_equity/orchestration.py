from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date
from typing import Any, Iterable

from .agent_results import (
    AgentResult,
    agent_result_from_dict,
    agent_result_template,
    ingest_agent_results,
    ingestion_report_to_dict,
)
from .capital_stack_diff import capital_stack_diff_to_dict, diff_capital_stack_packet
from .contract_graph import enhance_expanded_packet_with_contract_identity
from .cross_cik import cross_cik_graph_to_dict, expand_packet_with_cross_cik
from .evidence import EvidenceRecord, validate_point_in_time
from .foreign_contract_graph import (
    expand_foreign_contract_identities,
    foreign_contract_expansion_to_dict,
)
from .io import screening_candidate_from_dict
from .legal_name_alias import (
    alias_groups_from_graphs,
    build_legal_name_alias_graph,
    build_legal_name_alias_graphs,
    legal_name_alias_graph_to_dict,
)
from .market import HistoricalMarketProvider, market_snapshot
from .named_entity_closure import close_named_entity_fixed_point
from .named_entity_contracts import (
    named_entity_contract_graph_to_dict,
    resolve_named_entity_contracts,
)
from .prefill import build_screening_draft, build_sec_prefill_tasks
from .screening import screen_candidate
from .sec import SecClient, snapshot_to_dict, snapshot_to_evidence
from .sec_debt import (
    build_capital_stack_agent_task,
    build_capital_stack_packet,
    capital_stack_packet_to_dict,
)
from .sec_instruments import (
    build_instrument_verification_task,
    build_sec_instrument_packet,
    instrument_verification_template,
    sec_instrument_packet_to_dict,
)
from .source_graph import (
    expand_instrument_packet_with_references,
    source_document_graph_to_dict,
)


@dataclass(frozen=True)
class ResearchBundle:
    ticker: str | None
    company_name: str
    analysis_date: date
    packet: dict[str, Any]
    warnings: tuple[str, ...]


def _jsonable(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if hasattr(value, "__dataclass_fields__"):
        return _jsonable(asdict(value))
    return value


def _named_entity_source_nodes(root_nodes, foreign_graphs, max_depth: int):
    """Combine root and foreign contract sources without losing global depth."""

    chosen = {}
    for node in root_nodes:
        if node.depth >= max_depth:
            continue
        key = (node.accession_number, node.url)
        current = chosen.get(key)
        if current is None or node.depth < current.depth:
            chosen[key] = node

    for item in foreign_graphs:
        entry_depth = getattr(item, "entry_global_depth", None)
        if entry_depth is None:
            continue
        for node in item.graph.nodes:
            global_depth = entry_depth + node.depth
            if global_depth >= max_depth:
                continue
            adjusted = replace(node, depth=global_depth)
            key = (adjusted.accession_number, adjusted.url)
            current = chosen.get(key)
            if current is None or adjusted.depth < current.depth:
                chosen[key] = adjusted

    return tuple(sorted(chosen.values(), key=lambda item: (item.depth, item.node_id)))


def _market_context(
    provider: HistoricalMarketProvider,
    ticker: str,
    analysis_date: date,
    *,
    history_years: int,
    shares_outstanding: float | None,
) -> tuple[dict[str, Any] | None, EvidenceRecord | None, tuple[str, ...]]:
    warnings: list[str] = []
    matches = [security for security in provider.universe(analysis_date) if security.symbol.upper() == ticker.upper()]
    if not matches:
        return None, None, (f"{ticker} was not found in {provider.name} universe at the cutoff",)
    if len(matches) > 1:
        warnings.append(f"multiple {provider.name} securities matched ticker {ticker}; first match used")
    security = matches[0]
    start_year = max(1900, analysis_date.year - max(history_years, 1))
    snapshot = market_snapshot(
        provider,
        security,
        analysis_date,
        history_start=date(start_year, 1, 1),
        shares_outstanding=shares_outstanding,
    )
    warnings.extend(snapshot.warnings)
    evidence = None
    if snapshot.price is not None:
        evidence = EvidenceRecord(
            evidence_id="market:cutoff_price",
            claim=f"{ticker} raw/as-traded close={snapshot.price.close:g} on {snapshot.price.date.isoformat()}",
            source=snapshot.price.source or provider.name,
            published_on=snapshot.price.date,
            evidence_type="fact",
            event_on=snapshot.price.date,
            notes="Deterministic market-provider cutoff price; not an adjusted drawdown value.",
        )
    return _jsonable(snapshot), evidence, tuple(warnings)


def build_research_bundle(
    sec_client: SecClient,
    *,
    analysis_date: date,
    ticker: str | None = None,
    cik: str | int | None = None,
    filing_limit: int = 3,
    max_snippets_per_filing: int = 50,
    instrument_filing_limit: int = 6,
    max_instrument_exhibits_per_filing: int = 6,
    max_instrument_candidates_per_exhibit: int = 20,
    resolve_incorporated_references: bool = True,
    resolve_contract_identity_references: bool = True,
    resolve_cross_cik_references: bool = True,
    reference_depth: int = 3,
    reference_max_nodes: int = 80,
    cross_cik_max_nodes: int = 80,
    contract_search_max_filings: int = 40,
    contract_days_before_execution: int = 30,
    contract_days_after_execution: int = 550,
    market_provider: HistoricalMarketProvider | None = None,
    history_years: int = 5,
) -> ResearchBundle:
    snapshot = sec_client.snapshot(
        analysis_date=analysis_date,
        ticker=ticker,
        cik=cik,
        forms=("10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A"),
        filing_limit=max(filing_limit * 4, instrument_filing_limit * 2, 20),
    )
    warnings = list(snapshot.warnings)

    legal_name_graph = build_legal_name_alias_graph(
        sec_client,
        cik=snapshot.cik,
        analysis_date=analysis_date,
    )
    legal_name_payload = legal_name_alias_graph_to_dict(legal_name_graph)
    legal_alias_groups = alias_groups_from_graphs((legal_name_graph,))
    warnings.extend(legal_name_graph.warnings)

    point_in_time_name = legal_name_graph.canonical_name_as_of or snapshot.cik
    if point_in_time_name != snapshot.company_name:
        snapshot = replace(snapshot, company_name=point_in_time_name)

    evidence = list(snapshot_to_evidence(snapshot))
    screening_draft = build_screening_draft(snapshot)

    capital_stack = build_capital_stack_packet(
        sec_client,
        ticker=snapshot.ticker,
        company_name=snapshot.company_name,
        analysis_date=analysis_date,
        filings=snapshot.filings,
        filing_limit=filing_limit,
        max_snippets_per_filing=max_snippets_per_filing,
    )
    diff = diff_capital_stack_packet(capital_stack)
    warnings.extend(capital_stack.warnings)
    warnings.extend(diff.warnings)

    instrument_packet = build_sec_instrument_packet(
        sec_client,
        ticker=snapshot.ticker,
        company_name=snapshot.company_name,
        analysis_date=analysis_date,
        filings=snapshot.filings,
        filing_limit=instrument_filing_limit,
        max_exhibits_per_filing=max_instrument_exhibits_per_filing,
        max_candidates_per_exhibit=max_instrument_candidates_per_exhibit,
    )
    source_graph_payload = None
    cross_cik_graph_payload = None
    named_entity_contract_graph_payload = None
    if resolve_incorporated_references:
        expanded = expand_instrument_packet_with_references(
            sec_client,
            packet=instrument_packet,
            cik=snapshot.cik,
            seed_filings=snapshot.filings,
            max_depth=reference_depth,
            max_nodes=reference_max_nodes,
            max_candidates_per_exhibit=max_instrument_candidates_per_exhibit,
        )
        if resolve_contract_identity_references:
            expanded = enhance_expanded_packet_with_contract_identity(
                sec_client,
                expanded=expanded,
                cik=snapshot.cik,
                seed_filings=snapshot.filings,
                max_depth=reference_depth,
                max_nodes=reference_max_nodes,
                max_contract_search_filings=contract_search_max_filings,
                contract_days_before_execution=contract_days_before_execution,
                contract_days_after_execution=contract_days_after_execution,
                max_candidates_per_exhibit=max_instrument_candidates_per_exhibit,
                alias_groups=legal_alias_groups,
            )
        source_graph_payload = source_document_graph_to_dict(expanded.graph)
        warnings.extend(expanded.graph.warnings)
        if resolve_cross_cik_references:
            cross_expanded = expand_packet_with_cross_cik(
                sec_client,
                expanded=expanded,
                root_cik=snapshot.cik,
                max_depth=reference_depth,
                max_nodes=cross_cik_max_nodes,
                max_candidates_per_exhibit=max_instrument_candidates_per_exhibit,
            )
            instrument_packet = cross_expanded.packet
            encountered_ciks = {
                item.cik for item in cross_expanded.cross_cik_graph.entity_nodes
            } | {
                item.target_cik for item in cross_expanded.cross_cik_graph.references
            } | {snapshot.cik}
            legal_graphs, legal_graph_warnings = build_legal_name_alias_graphs(
                sec_client,
                ciks=encountered_ciks,
                analysis_date=analysis_date,
                existing=(legal_name_graph,),
            )
            warnings.extend(legal_graph_warnings)

            foreign_expansion = None
            named_fixed_point_iterations = None
            if resolve_contract_identity_references:
                foreign_expansion = expand_foreign_contract_identities(
                    sec_client,
                    cross_expanded=cross_expanded,
                    legal_name_graphs=legal_graphs,
                    max_depth=reference_depth,
                    max_nodes=cross_cik_max_nodes,
                    max_contract_search_filings=contract_search_max_filings,
                    contract_days_before_execution=contract_days_before_execution,
                    contract_days_after_execution=contract_days_after_execution,
                    max_candidates_per_exhibit=max_instrument_candidates_per_exhibit,
                )
                cross_expanded = foreign_expansion.cross_expanded
                instrument_packet = cross_expanded.packet
                warnings.extend(foreign_expansion.warnings)

                named_sources = _named_entity_source_nodes(
                    expanded.graph.nodes,
                    foreign_expansion.graphs,
                    reference_depth,
                )
                named_expansion = resolve_named_entity_contracts(
                    sec_client,
                    packet=instrument_packet,
                    source_nodes=named_sources,
                    root_cik=snapshot.cik,
                    known_legal_name_graphs=legal_graphs,
                    max_contract_search_filings=contract_search_max_filings,
                    contract_days_before_execution=contract_days_before_execution,
                    contract_days_after_execution=contract_days_after_execution,
                    max_candidates_per_exhibit=max_instrument_candidates_per_exhibit,
                )
                warnings.extend(named_expansion.graph.warnings)

                named_fixed = close_named_entity_fixed_point(
                    sec_client,
                    cross_expanded=cross_expanded,
                    named_expansion=named_expansion,
                    initial_source_nodes=named_sources,
                    legal_name_graphs=legal_graphs,
                    existing_foreign_expansion=foreign_expansion,
                    max_depth=reference_depth,
                    max_nodes=cross_cik_max_nodes,
                    max_contract_search_filings=contract_search_max_filings,
                    contract_days_before_execution=contract_days_before_execution,
                    contract_days_after_execution=contract_days_after_execution,
                    max_candidates_per_exhibit=max_instrument_candidates_per_exhibit,
                )
                cross_expanded = named_fixed.cross_expanded
                instrument_packet = cross_expanded.packet
                legal_graphs = tuple(named_fixed.legal_name_graphs)
                foreign_expansion = named_fixed.foreign_expansion
                named_expansion = named_fixed.named_expansion
                named_fixed_point_iterations = named_fixed.iterations
                named_entity_contract_graph_payload = named_entity_contract_graph_to_dict(
                    named_expansion.graph
                )
                warnings.extend(named_fixed.warnings)

            # Serialize only after all foreign and alternating named/explicit closure
            # passes so top-level provenance matches the final packet.
            cross_cik_graph_payload = cross_cik_graph_to_dict(cross_expanded.cross_cik_graph)
            cross_cik_graph_payload["legal_name_alias_graphs"] = [
                legal_name_alias_graph_to_dict(item) for item in legal_graphs
            ]
            if named_fixed_point_iterations is not None:
                cross_cik_graph_payload["named_entity_fixed_point_iterations"] = (
                    named_fixed_point_iterations
                )
            if foreign_expansion is not None:
                foreign_payload = foreign_contract_expansion_to_dict(foreign_expansion)
                cross_cik_graph_payload["foreign_contract_graphs"] = foreign_payload["graphs"]
                cross_cik_graph_payload["foreign_contract_warnings"] = foreign_payload["warnings"]
            if named_entity_contract_graph_payload is not None:
                cross_cik_graph_payload["named_entity_contract_graph"] = (
                    named_entity_contract_graph_payload
                )
            warnings.extend(cross_expanded.cross_cik_graph.warnings)
        else:
            instrument_packet = expanded.packet
    warnings.extend(instrument_packet.warnings)

    tasks = [task for task in build_sec_prefill_tasks(snapshot) if task.name != "capital_stack_extractor"]
    tasks.insert(0, build_capital_stack_agent_task(capital_stack))
    instrument_task = build_instrument_verification_task(instrument_packet)

    market_payload = None
    if market_provider is not None and snapshot.ticker:
        shares_fact = snapshot.facts.get("shares_outstanding")
        shares = shares_fact.value if shares_fact is not None else None
        market_payload, market_evidence, market_warnings = _market_context(
            market_provider,
            snapshot.ticker,
            analysis_date,
            history_years=history_years,
            shares_outstanding=shares,
        )
        warnings.extend(market_warnings)
        if market_evidence is not None:
            evidence.append(market_evidence)
            candidate = screening_draft["screening_candidate_draft"]
            if candidate["capital_structure"].get("current_price") is None:
                candidate["capital_structure"]["current_price"] = market_payload["price"]["close"]
            candidate.setdefault("metadata", {})["adjusted_drawdown_proxy"] = market_payload.get(
                "adjusted_drawdown_from_peak"
            )
            candidate["metadata"]["market_provider"] = market_provider.name

    pit_violations = validate_point_in_time(tuple(evidence), analysis_date)
    packet = {
        "snapshot": snapshot_to_dict(snapshot),
        "evidence": [_jsonable(item) for item in evidence],
        "point_in_time_violations": list(pit_violations),
        "screening_draft": screening_draft,
        "capital_stack_packet": capital_stack_packet_to_dict(capital_stack),
        "capital_stack_diff": capital_stack_diff_to_dict(diff),
        "legal_name_alias_graph": legal_name_payload,
        "debt_instrument_source_packet": sec_instrument_packet_to_dict(instrument_packet),
        "source_document_graph": source_graph_payload,
        "cross_cik_graph": cross_cik_graph_payload,
        "named_entity_contract_graph": named_entity_contract_graph_payload,
        "debt_instrument_verification": {
            "task": _jsonable(instrument_task),
            "result_template": instrument_verification_template(instrument_packet),
            "result_kind": "debt_instrument_snapshots",
            "ingestion_target": "distressed-equity-debt-ledger",
        },
        "market_snapshot": market_payload,
        "agent_tasks": [
            {
                "task": _jsonable(task),
                "result_template": agent_result_template(task.name, analysis_date),
            }
            for task in tasks
        ],
    }
    return ResearchBundle(
        ticker=snapshot.ticker,
        company_name=snapshot.company_name,
        analysis_date=analysis_date,
        packet=packet,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _unwrap_agent_result(raw: dict[str, Any]) -> dict[str, Any]:
    nested = raw.get("agent_result")
    return nested if isinstance(nested, dict) else raw


def ingest_research_bundle(
    bundle: ResearchBundle | dict[str, Any],
    raw_results: Iterable[dict[str, Any]],
    *,
    allow_overwrite: bool = False,
    apply_low_confidence: bool = False,
) -> dict[str, Any]:
    packet = bundle.packet if isinstance(bundle, ResearchBundle) else bundle
    parsed: list[AgentResult] = [agent_result_from_dict(_unwrap_agent_result(raw)) for raw in raw_results]
    report = ingest_agent_results(
        packet,
        parsed,
        allow_overwrite=allow_overwrite,
        apply_low_confidence=apply_low_confidence,
    )
    payload = ingestion_report_to_dict(report)
    if report.ready_for_screening:
        candidate = screening_candidate_from_dict(report.screening_candidate_draft)
        payload["screening_result"] = _jsonable(screen_candidate(candidate))
    else:
        payload["screening_result"] = None
    return payload


def render_research_summary(bundle: ResearchBundle, merged: dict[str, Any] | None = None) -> str:
    packet = bundle.packet
    draft = packet["screening_draft"]["screening_candidate_draft"]
    diff = packet.get("capital_stack_diff") or {}
    source_packet = packet.get("debt_instrument_source_packet") or {}
    name_graph = packet.get("legal_name_alias_graph") or {}
    name_records = name_graph.get("records", []) if isinstance(name_graph, dict) else []
    name_transitions = name_graph.get("transitions", []) if isinstance(name_graph, dict) else []
    graph = packet.get("source_document_graph") or {}
    graph_edges = graph.get("edges", []) if isinstance(graph, dict) else []
    resolved_edges = sum(1 for edge in graph_edges if str(edge.get("status") or "").startswith("resolved"))
    cross_graph = packet.get("cross_cik_graph") or {}
    cross_resolutions = cross_graph.get("resolutions", []) if isinstance(cross_graph, dict) else []
    resolved_cross = sum(1 for item in cross_resolutions if item.get("status") == "resolved_cross_cik")
    entity_nodes = cross_graph.get("entity_nodes", []) if isinstance(cross_graph, dict) else []
    entity_roles = cross_graph.get("entity_roles", []) if isinstance(cross_graph, dict) else []
    entity_relations = cross_graph.get("entity_relations", []) if isinstance(cross_graph, dict) else []
    cross_name_graphs = cross_graph.get("legal_name_alias_graphs", []) if isinstance(cross_graph, dict) else []
    foreign_contract_graphs = cross_graph.get("foreign_contract_graphs", []) if isinstance(cross_graph, dict) else []
    foreign_contract_resolved = sum(
        1
        for item in foreign_contract_graphs
        for edge in (item.get("graph") or {}).get("edges", [])
        if edge.get("status") == "resolved_by_contract_identity"
    )
    named_graph = packet.get("named_entity_contract_graph") or {}
    named_resolutions = named_graph.get("resolutions", []) if isinstance(named_graph, dict) else []
    named_resolved = sum(
        1 for item in named_resolutions if item.get("status") == "resolved_named_entity_contract"
    )
    named_iterations = (
        cross_graph.get("named_entity_fixed_point_iterations")
        if isinstance(cross_graph, dict)
        else None
    )
    lines = [
        f"# {bundle.company_name} ({bundle.ticker or 'CIK'}) research workspace",
        "",
        f"Analysis cutoff: **{bundle.analysis_date.isoformat()}**",
        "",
        "## Deterministic evidence state",
        "",
        f"- SEC filing/XBRL evidence rows: {len(packet.get('evidence', []))}",
        f"- Legal-name records: {len(name_records)} / completed rename transitions: {len(name_transitions)}",
        f"- Canonical legal name at cutoff: {name_graph.get('canonical_name_as_of')}",
        f"- Capital-stack snippets: {len((packet.get('capital_stack_packet') or {}).get('snippets', []))}",
        f"- Filing-to-filing change candidates: {len(diff.get('changes', []))}",
        f"- Debt-relevant SEC exhibit documents: {len(source_packet.get('documents', []))}",
        f"- Source-backed debt instrument candidates: {len(source_packet.get('candidates', []))}",
        f"- Incorporation/reference graph edges: {len(graph_edges)} ({resolved_edges} resolved)",
        f"- Cross-CIK SEC resolutions: {len(cross_resolutions)} ({resolved_cross} resolved)",
        f"- Cross-CIK legal-name graphs frozen: {len(cross_name_graphs)}",
        f"- Foreign-CIK contract graphs: {len(foreign_contract_graphs)} ({foreign_contract_resolved} locator-less contracts resolved)",
        f"- Named external-entity contract resolutions: {len(named_resolutions)} ({named_resolved} resolved)",
        *(
            [f"- Alternating named/explicit closure iterations: {named_iterations}"]
            if named_iterations is not None
            else []
        ),
        f"- Explicit legal-entity graph: {len(entity_nodes)} entities / {len(entity_roles)} roles / {len(entity_relations)} relations",
        f"- Cutoff share price: {draft.get('capital_structure', {}).get('current_price')}",
        "",
        "## Research loop",
        "",
        "1. Review `legal_name_alias_graph.json`; only cutoff-safe SEC rename evidence can bridge legal names.",
        "2. Review filing-to-filing capital-stack changes against source filings.",
        "3. Review the source-document graph; inspect unresolved/ambiguous locator and contract-identity edges.",
        "4. Review `cross_cik_graph.json`; explicit foreign CIK traversal, foreign legal-name graphs, foreign locator-less contracts, and alternating named-entity provenance are frozen separately.",
        "5. Review `debt_instrument_template.json`; verify/delete candidate fields against cited exhibit spans.",
        "6. Feed verified snapshots to `distressed-equity-debt-ledger` for stable instrument identity and amendment tracking.",
        "7. Have screening agents return the supplied structured result templates.",
        "8. Use `distressed-equity-covenant` for source-backed maintenance-covenant EBITDA/headroom arithmetic.",
        "9. Re-run this command with `--result` files or call `distressed-equity-ingest`.",
        "",
    ]
    if merged is not None:
        lines.extend(["## Ingestion / screen", ""])
        lines.append(f"- Ready for screening: **{merged.get('ready_for_screening')}**")
        if merged.get("missing_for_screening"):
            lines.append("- Missing: " + ", ".join(merged["missing_for_screening"]))
        if merged.get("conflicts"):
            lines.append("- Conflicts: " + "; ".join(merged["conflicts"]))
        result = merged.get("screening_result")
        if result:
            lines.append(f"- Priority: **{result['priority']}**")
            lines.append(f"- Base common-equity multiple: **{result['base_equity_multiple']:.2f}x**")
            rp = result.get("required_probability")
            lines.append(f"- Required Probability: **{rp:.1%}**" if rp is not None else "- Required Probability: **unresolved**")
        lines.append("")
    if bundle.warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {item}" for item in bundle.warnings)
        lines.append("")
    return "\n".join(lines)

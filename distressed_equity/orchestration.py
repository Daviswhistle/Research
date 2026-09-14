from __future__ import annotations

from dataclasses import asdict, dataclass
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
from .evidence import EvidenceRecord, validate_point_in_time
from .io import screening_candidate_from_dict
from .market import HistoricalMarketProvider, market_snapshot
from .prefill import build_screening_draft, build_sec_prefill_tasks
from .screening import screen_candidate
from .sec import SecClient, snapshot_to_dict, snapshot_to_evidence
from .sec_debt import (
    build_capital_stack_agent_task,
    build_capital_stack_packet,
    capital_stack_packet_to_dict,
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
    market_provider: HistoricalMarketProvider | None = None,
    history_years: int = 5,
) -> ResearchBundle:
    snapshot = sec_client.snapshot(
        analysis_date=analysis_date,
        ticker=ticker,
        cik=cik,
        forms=("10-K", "10-K/A", "10-Q", "10-Q/A", "8-K"),
        filing_limit=max(filing_limit * 4, 20),
    )
    evidence = list(snapshot_to_evidence(snapshot))
    screening_draft = build_screening_draft(snapshot)
    warnings = list(snapshot.warnings)

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

    tasks = [task for task in build_sec_prefill_tasks(snapshot) if task.name != "capital_stack_extractor"]
    tasks.insert(0, build_capital_stack_agent_task(capital_stack))

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
    lines = [
        f"# {bundle.company_name} ({bundle.ticker or 'CIK'}) research workspace",
        "",
        f"Analysis cutoff: **{bundle.analysis_date.isoformat()}**",
        "",
        "## Deterministic evidence state",
        "",
        f"- SEC filing/XBRL evidence rows: {len(packet.get('evidence', []))}",
        f"- Capital-stack snippets: {len((packet.get('capital_stack_packet') or {}).get('snippets', []))}",
        f"- Filing-to-filing change candidates: {len(diff.get('changes', []))}",
        f"- Cutoff share price: {draft.get('capital_structure', {}).get('current_price')}",
        "",
        "## Research loop",
        "",
        "1. Review capital-stack amendment candidates against source filings.",
        "2. Have agents return the supplied structured result templates.",
        "3. Use `distressed-equity-covenant` for source-backed maintenance-covenant EBITDA/headroom arithmetic.",
        "4. Re-run this command with `--result` files or call `distressed-equity-ingest`.",
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

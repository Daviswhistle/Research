from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
from typing import Any

from .alpha_vantage import AlphaVantageProvider
from .debt_instruments import build_debt_instrument_ledger, debt_instrument_ledger_to_dict, debt_snapshots_from_dict
from .instrument_verification import validate_debt_snapshots_against_source_packet
from .orchestration import ResearchBundle, build_research_bundle, ingest_research_bundle, render_research_summary
from .sec import SecClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build/resume a point-in-time distressed-equity research workspace"
    )
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--ticker")
    identity.add_argument("--cik")
    parser.add_argument("--analysis-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--workspace", required=True, help="Directory for packet/task/result artifacts")
    parser.add_argument("--user-agent", help="SEC User-Agent; defaults to SEC_USER_AGENT")
    parser.add_argument("--filing-limit", type=int, default=3)
    parser.add_argument("--max-snippets-per-filing", type=int, default=50)
    parser.add_argument("--instrument-filing-limit", type=int, default=6)
    parser.add_argument("--max-instrument-exhibits-per-filing", type=int, default=6)
    parser.add_argument("--max-instrument-candidates-per-exhibit", type=int, default=20)
    parser.add_argument("--reference-depth", type=int, default=3)
    parser.add_argument("--reference-max-nodes", type=int, default=80)
    parser.add_argument(
        "--no-resolve-references",
        action="store_true",
        help="Do not follow incorporation-by-reference links to older SEC filings/exhibits",
    )
    parser.add_argument("--market-provider", choices=("none", "alpha-vantage"), default="none")
    parser.add_argument("--alpha-vantage-key", help="Defaults to ALPHA_VANTAGE_API_KEY")
    parser.add_argument("--history-years", type=int, default=5)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Rebuild the point-in-time packet even when research_packet.json already exists",
    )
    parser.add_argument(
        "--result",
        action="append",
        default=[],
        help="Structured agent-result JSON; repeat to ingest multiple results",
    )
    parser.add_argument(
        "--debt-instruments",
        help="Optional JSON containing verified filing-level debt instrument snapshots for stable-ID matching",
    )
    parser.add_argument("--allow-overwrite", action="store_true")
    parser.add_argument("--apply-low-confidence", action="store_true")
    return parser


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _load_json_any(path: str | Path) -> dict[str, Any] | list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, (dict, list)):
        raise ValueError(f"expected JSON object or list: {path}")
    return payload


def _bundle_from_frozen_packet(packet: dict[str, Any], cutoff: date) -> ResearchBundle:
    snapshot = packet.get("snapshot") or {}
    packet_date = str(snapshot.get("analysis_date") or "")[:10]
    if packet_date and packet_date != cutoff.isoformat():
        raise ValueError(
            f"workspace packet cutoff {packet_date} does not match requested {cutoff.isoformat()}; use --refresh intentionally"
        )
    return ResearchBundle(
        ticker=snapshot.get("ticker"),
        company_name=str(snapshot.get("company_name") or snapshot.get("cik") or "Unknown company"),
        analysis_date=cutoff,
        packet=packet,
        warnings=tuple(str(item) for item in snapshot.get("warnings", []) if str(item).strip()),
    )


def _write_bundle_artifacts(workspace: Path, bundle: ResearchBundle) -> None:
    packet = bundle.packet
    _write_json(workspace / "research_packet.json", packet)
    _write_json(workspace / "capital_stack.json", packet["capital_stack_packet"])
    _write_json(workspace / "capital_stack_diff.json", packet["capital_stack_diff"])
    _write_json(workspace / "tasks.json", {"tasks": packet["agent_tasks"]})
    source_packet = packet.get("debt_instrument_source_packet")
    if source_packet is not None:
        _write_json(workspace / "debt_instrument_sources.json", source_packet)
    graph = packet.get("source_document_graph")
    if graph is not None:
        _write_json(workspace / "source_document_graph.json", graph)
    verification = packet.get("debt_instrument_verification")
    if isinstance(verification, dict):
        _write_json(workspace / "debt_instrument_task.json", verification.get("task", {}))
        _write_json(workspace / "debt_instrument_template.json", verification.get("result_template", {}))
    if packet.get("market_snapshot") is not None:
        _write_json(workspace / "market_snapshot.json", packet["market_snapshot"])


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cutoff = date.fromisoformat(args.analysis_date)
    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    packet_path = workspace / "research_packet.json"

    if packet_path.exists() and not args.refresh:
        bundle = _bundle_from_frozen_packet(_load_json(packet_path), cutoff)
    else:
        sec_client = SecClient(user_agent=args.user_agent)
        market_provider = None
        if args.market_provider == "alpha-vantage":
            market_provider = AlphaVantageProvider(
                api_key=args.alpha_vantage_key or os.getenv("ALPHA_VANTAGE_API_KEY")
            )

        bundle = build_research_bundle(
            sec_client,
            analysis_date=cutoff,
            ticker=args.ticker,
            cik=args.cik,
            filing_limit=args.filing_limit,
            max_snippets_per_filing=args.max_snippets_per_filing,
            instrument_filing_limit=args.instrument_filing_limit,
            max_instrument_exhibits_per_filing=args.max_instrument_exhibits_per_filing,
            max_instrument_candidates_per_exhibit=args.max_instrument_candidates_per_exhibit,
            resolve_incorporated_references=not args.no_resolve_references,
            reference_depth=args.reference_depth,
            reference_max_nodes=args.reference_max_nodes,
            market_provider=market_provider,
            history_years=args.history_years,
        )
        _write_bundle_artifacts(workspace, bundle)

    debt_ledger_payload = None
    debt_source_validation_warnings: tuple[str, ...] = ()
    if args.debt_instruments:
        raw_debt = _load_json_any(args.debt_instruments)
        source_packet = bundle.packet.get("debt_instrument_source_packet")
        if isinstance(source_packet, dict):
            source_validation = validate_debt_snapshots_against_source_packet(source_packet, raw_debt)
            if not source_validation.valid:
                raise ValueError("invalid source-backed debt snapshots: " + "; ".join(source_validation.errors))
            snapshots = source_validation.snapshots
            debt_source_validation_warnings = source_validation.warnings
        else:
            snapshots = debt_snapshots_from_dict(raw_debt)
        ledger = build_debt_instrument_ledger(snapshots)
        debt_ledger_payload = debt_instrument_ledger_to_dict(ledger)
        _write_json(workspace / "debt_instrument_ledger.json", debt_ledger_payload)

    merged = None
    if args.result:
        raw_results = [_load_json(path) for path in args.result]
        merged = ingest_research_bundle(
            bundle,
            raw_results,
            allow_overwrite=args.allow_overwrite,
            apply_low_confidence=args.apply_low_confidence,
        )
        if debt_ledger_payload is not None:
            merged["debt_instrument_ledger"] = debt_ledger_payload
        _write_json(workspace / "merged.json", merged)

    summary = render_research_summary(bundle, merged)
    if debt_ledger_payload is not None:
        summary += (
            "\n## Debt instrument identity ledger\n\n"
            f"- Versions: {len(debt_ledger_payload['versions'])}\n"
            f"- Amendment/change candidates: {len(debt_ledger_payload['changes'])}\n"
            f"- New/unmatched stable IDs: {len(debt_ledger_payload['unmatched_versions'])}\n"
        )
        if debt_source_validation_warnings:
            summary += "- Source validation warnings: " + "; ".join(debt_source_validation_warnings) + "\n"
    (workspace / "summary.md").write_text(summary, encoding="utf-8")

    print(workspace / "summary.md")
    if merged is not None and merged.get("errors"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

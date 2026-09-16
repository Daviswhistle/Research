from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
from typing import Any

from .alpha_vantage import AlphaVantageProvider
from .bond_market import build_bond_market_packet, bond_market_packet_to_dict
from .csv_bond_market import CsvBondMarketProvider
from .debt_instruments import build_debt_instrument_ledger, debt_instrument_ledger_to_dict, debt_snapshots_from_dict
from .debt_lineage import (
    build_debt_lineage_graph,
    debt_lineage_event_template,
    debt_lineage_graph_to_dict,
    debt_lineage_source_packet_fingerprint,
    debt_lineage_verification_template,
    promote_verified_debt_lineage_events,
    validate_debt_lineage_events_against_source_packet,
)
from .eodhd_bonds import EodhdBondProvider
from .instrument_verification import validate_debt_snapshots_against_source_packet
from .orchestration import ResearchBundle, build_research_bundle, ingest_research_bundle, render_research_summary
from .sec import SecClient, normalize_cik


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build/resume a point-in-time distressed-equity research workspace")
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
    parser.add_argument("--cross-cik-max-nodes", type=int, default=80)
    parser.add_argument("--contract-search-max-filings", type=int, default=40)
    parser.add_argument("--contract-days-before-execution", type=int, default=30)
    parser.add_argument("--contract-days-after-execution", type=int, default=550)
    parser.add_argument("--no-resolve-references", action="store_true", help="Do not follow incorporation-by-reference links to older SEC filings/exhibits")
    parser.add_argument("--no-contract-identity-fallback", action="store_true", help="Do not reverse-search locator-less contract title + execution-date references")
    parser.add_argument("--no-cross-cik", action="store_true", help="Do not follow explicit foreign-CIK SEC Archives/CIK references")
    parser.add_argument("--market-provider", choices=("none", "alpha-vantage"), default="none")
    parser.add_argument("--alpha-vantage-key", help="Defaults to ALPHA_VANTAGE_API_KEY")
    parser.add_argument("--history-years", type=int, default=5)
    parser.add_argument(
        "--bond-market-provider",
        choices=("none", "csv", "eodhd"),
        default="none",
        help="Historical corporate-bond provider; requires --debt-instruments",
    )
    parser.add_argument("--bond-observations-csv", help="Normalized CUSIP/ISIN bond history for --bond-market-provider csv")
    parser.add_argument("--eodhd-key", help="Defaults to EODHD_API_KEY")
    parser.add_argument("--bond-lookback-days", type=int, default=365)
    parser.add_argument("--bond-max-staleness-days", type=int, default=30)
    parser.add_argument("--bond-probability-horizon-years", type=float, default=1.0)
    parser.add_argument(
        "--bond-recovery-rates",
        default="0.20,0.40,0.60",
        help="Comma-separated recovery assumptions for spread-implied risk-neutral stress proxies",
    )
    parser.add_argument("--refresh", action="store_true", help="Rebuild the point-in-time packet even when research_packet.json already exists")
    parser.add_argument("--result", action="append", default=[], help="Structured agent-result JSON; repeat to ingest multiple results")
    parser.add_argument("--debt-instruments", help="Optional JSON containing verified filing-level debt instrument snapshots for stable-ID matching")
    parser.add_argument("--debt-lineage", help="Optional source-backed candidate debt lineage JSON; requires --debt-instruments")
    parser.add_argument("--debt-lineage-verification", help="Fingerprint-bound lineage verification JSON; requires --debt-lineage")
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


def _same_path(left: str | Path, right: str | Path) -> bool:
    return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()


def _recovery_rates(raw: str) -> tuple[float, ...]:
    values = tuple(float(item.strip()) for item in raw.split(",") if item.strip())
    if not values:
        raise ValueError("--bond-recovery-rates must contain at least one value")
    return values


def _bond_market_provider(args: argparse.Namespace):
    if args.bond_market_provider == "none":
        return None
    if args.bond_market_provider == "csv":
        if not args.bond_observations_csv:
            raise ValueError("--bond-market-provider csv requires --bond-observations-csv")
        return CsvBondMarketProvider(args.bond_observations_csv)
    return EodhdBondProvider(api_key=args.eodhd_key or os.getenv("EODHD_API_KEY"))


def _bundle_from_frozen_packet(packet: dict[str, Any], cutoff: date, *, requested_ticker: str | None = None, requested_cik: str | None = None) -> ResearchBundle:
    snapshot = packet.get("snapshot") or {}
    packet_date = str(snapshot.get("analysis_date") or "")[:10]
    if packet_date and packet_date != cutoff.isoformat():
        raise ValueError(f"workspace packet cutoff {packet_date} does not match requested {cutoff.isoformat()}; use --refresh intentionally")
    frozen_ticker = str(snapshot.get("ticker") or "").strip().upper() or None
    frozen_cik_raw = snapshot.get("cik")
    frozen_cik = normalize_cik(frozen_cik_raw) if frozen_cik_raw else None
    if requested_ticker is not None:
        requested = requested_ticker.strip().upper()
        if frozen_ticker != requested:
            raise ValueError(f"workspace packet ticker {frozen_ticker or 'unknown'} does not match requested {requested}; use --refresh intentionally")
    if requested_cik is not None:
        requested = normalize_cik(requested_cik)
        if frozen_cik != requested:
            raise ValueError(f"workspace packet CIK {frozen_cik or 'unknown'} does not match requested {requested}; use --refresh intentionally")
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
    if packet.get("legal_name_alias_graph") is not None:
        _write_json(workspace / "legal_name_alias_graph.json", packet["legal_name_alias_graph"])
    if packet.get("debt_instrument_source_packet") is not None:
        _write_json(workspace / "debt_instrument_sources.json", packet["debt_instrument_source_packet"])
    if packet.get("source_document_graph") is not None:
        _write_json(workspace / "source_document_graph.json", packet["source_document_graph"])
    if packet.get("cross_cik_graph") is not None:
        _write_json(workspace / "cross_cik_graph.json", packet["cross_cik_graph"])
    if packet.get("named_entity_contract_graph") is not None:
        _write_json(workspace / "named_entity_contract_graph.json", packet["named_entity_contract_graph"])
    verification = packet.get("debt_instrument_verification")
    if isinstance(verification, dict):
        _write_json(workspace / "debt_instrument_task.json", verification.get("task", {}))
        _write_json(workspace / "debt_instrument_template.json", verification.get("result_template", {}))
    if packet.get("market_snapshot") is not None:
        _write_json(workspace / "market_snapshot.json", packet["market_snapshot"])


def _clear_stale_lineage_artifacts(workspace: Path) -> None:
    for name in ("debt_lineage.json", "debt_lineage_verification_template.json"):
        path = workspace / name
        if path.exists():
            path.unlink()


def _clear_stale_bond_market_artifact(workspace: Path) -> None:
    path = workspace / "bond_market.json"
    if path.exists():
        path.unlink()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cutoff = date.fromisoformat(args.analysis_date)
    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    packet_path = workspace / "research_packet.json"

    if args.debt_lineage_verification and not args.debt_lineage:
        raise ValueError("--debt-lineage-verification requires --debt-lineage")
    if args.bond_market_provider != "none" and not args.debt_instruments:
        raise ValueError("--bond-market-provider requires --debt-instruments so market data binds only to verified stable debt IDs")
    # Load an in-place approval before any stale derived artifacts are invalidated.
    lineage_verification_input = _load_json(args.debt_lineage_verification) if args.debt_lineage_verification else None

    if packet_path.exists() and not args.refresh:
        bundle = _bundle_from_frozen_packet(_load_json(packet_path), cutoff, requested_ticker=args.ticker, requested_cik=args.cik)
    else:
        sec_client = SecClient(user_agent=args.user_agent)
        market_provider = None
        if args.market_provider == "alpha-vantage":
            market_provider = AlphaVantageProvider(api_key=args.alpha_vantage_key or os.getenv("ALPHA_VANTAGE_API_KEY"))
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
            resolve_contract_identity_references=not args.no_contract_identity_fallback,
            resolve_cross_cik_references=not args.no_cross_cik,
            reference_depth=args.reference_depth,
            reference_max_nodes=args.reference_max_nodes,
            cross_cik_max_nodes=args.cross_cik_max_nodes,
            contract_search_max_filings=args.contract_search_max_filings,
            contract_days_before_execution=args.contract_days_before_execution,
            contract_days_after_execution=args.contract_days_after_execution,
            market_provider=market_provider,
            history_years=args.history_years,
        )
        _write_bundle_artifacts(workspace, bundle)

    debt_ledger = None
    debt_ledger_payload = None
    debt_lineage_payload = None
    bond_market_payload = None
    debt_source_validation_warnings: tuple[str, ...] = ()
    debt_lineage_validation_warnings: tuple[str, ...] = ()
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
        debt_ledger = build_debt_instrument_ledger(snapshots)
        debt_ledger_payload = debt_instrument_ledger_to_dict(debt_ledger)
        _write_json(workspace / "debt_instrument_ledger.json", debt_ledger_payload)
        _write_json(workspace / "debt_lineage_template.json", debt_lineage_event_template(debt_ledger))
        # The ledger is the identity basis for every derived lineage/market artifact.
        # Once it changes, old derived outputs must disappear even if a replacement fails.
        _clear_stale_lineage_artifacts(workspace)
        _clear_stale_bond_market_artifact(workspace)

        provider = _bond_market_provider(args)
        if provider is not None:
            bond_packet = build_bond_market_packet(
                provider,
                debt_ledger,
                cutoff,
                lookback_days=args.bond_lookback_days,
                max_staleness_days=args.bond_max_staleness_days,
                recovery_rates=_recovery_rates(args.bond_recovery_rates),
                probability_horizon_years=args.bond_probability_horizon_years,
            )
            bond_market_payload = bond_market_packet_to_dict(bond_packet)
            _write_json(workspace / "bond_market.json", bond_market_payload)

    if args.debt_lineage:
        if debt_ledger is None:
            raise ValueError("--debt-lineage requires --debt-instruments")
        source_packet = bundle.packet.get("debt_instrument_source_packet")
        if not isinstance(source_packet, dict):
            raise ValueError("workspace debt lineage requires a frozen debt_instrument_source_packet")
        raw_lineage = _load_json_any(args.debt_lineage)
        lineage_validation = validate_debt_lineage_events_against_source_packet(source_packet, raw_lineage, debt_ledger)
        if not lineage_validation.valid:
            raise ValueError("invalid source-backed debt lineage events: " + "; ".join(lineage_validation.errors))
        debt_lineage_validation_warnings = lineage_validation.warnings
        events = lineage_validation.events
        source_fingerprint = debt_lineage_source_packet_fingerprint(source_packet)
        verification_template = debt_lineage_verification_template(
            events,
            source_packet_fingerprint=source_fingerprint,
        )
        verification_path = workspace / "debt_lineage_verification_template.json"
        verification_is_in_place = bool(
            lineage_verification_input is not None
            and args.debt_lineage_verification
            and _same_path(verification_path, args.debt_lineage_verification)
        )
        if lineage_verification_input is None or not verification_is_in_place:
            _write_json(verification_path, verification_template)
        if lineage_verification_input is not None:
            events = promote_verified_debt_lineage_events(
                events,
                lineage_verification_input,
                source_packet_fingerprint=source_fingerprint,
            )
        lineage = build_debt_lineage_graph(debt_ledger, events)
        debt_lineage_payload = debt_lineage_graph_to_dict(lineage)
        _write_json(workspace / "debt_lineage.json", debt_lineage_payload)
        if verification_is_in_place:
            # Restore the already-loaded approved artifact only after successful
            # validation/promotion so a failed rebuild cannot leave stale approval.
            _write_json(verification_path, lineage_verification_input)

    merged = None
    if args.result:
        raw_results = [_load_json(path) for path in args.result]
        merged = ingest_research_bundle(bundle, raw_results, allow_overwrite=args.allow_overwrite, apply_low_confidence=args.apply_low_confidence)
        if debt_ledger_payload is not None:
            merged["debt_instrument_ledger"] = debt_ledger_payload
        if bond_market_payload is not None:
            merged["bond_market"] = bond_market_payload
        if debt_lineage_payload is not None:
            merged["debt_lineage"] = debt_lineage_payload
        _write_json(workspace / "merged.json", merged)

    summary = render_research_summary(bundle, merged)
    if debt_ledger_payload is not None:
        summary += (
            "\n## Debt instrument identity ledger\n\n"
            f"- Versions: {len(debt_ledger_payload['versions'])}\n"
            f"- Amendment/change candidates: {len(debt_ledger_payload['changes'])}\n"
            f"- New/unmatched stable IDs: {len(debt_ledger_payload['unmatched_versions'])}\n"
            "- Lineage research template: debt_lineage_template.json\n"
        )
        if debt_source_validation_warnings:
            summary += "- Source validation warnings: " + "; ".join(debt_source_validation_warnings) + "\n"
    if bond_market_payload is not None:
        assessments = bond_market_payload["assessments"]
        observed = [item for item in assessments if item.get("observation") is not None]
        spread_known = [item for item in observed if item.get("spread_bps") is not None]
        low_price = [
            item for item in observed
            if item.get("observation", {}).get("price_pct_par") is not None
            and item["observation"]["price_pct_par"] < 80
        ]
        summary += (
            "\n## Historical bond market stress\n\n"
            f"- Provider: {bond_market_payload['provider']}\n"
            f"- Stable debt instruments assessed: {len(assessments)}\n"
            f"- Instruments with cutoff/lookback market observations: {len(observed)}\n"
            f"- Instruments below 80% of par: {len(low_price)}\n"
            f"- Instruments with benchmarked credit spread: {len(spread_known)}\n"
            "- Probability fields are recovery-sensitive, constant-hazard risk-neutral stress proxies; they are not physical default forecasts.\n"
            "- Artifact: bond_market.json\n"
        )
    if debt_lineage_payload is not None:
        events = debt_lineage_payload["events"]
        impacts = debt_lineage_payload["impacts"]
        verified = sum(1 for event in events if event["status"] == "verified")
        candidates = sum(1 for event in events if event["status"] == "candidate")
        explicit_accounting = sum(1 for impact in impacts if impact["accounting_treatment"] not in {"undetermined", "not_applicable"})
        summary += (
            "\n## Debt exchange / modification lineage\n\n"
            f"- Events: {len(events)} (verified {verified}, candidate {candidates})\n"
            f"- Root instruments: {len(debt_lineage_payload['root_instruments'])}\n"
            f"- Terminal instruments: {len(debt_lineage_payload['terminal_instruments'])}\n"
            f"- Events with explicit accounting treatment: {explicit_accounting}\n"
            f"- Unresolved/candidate events: {len(debt_lineage_payload['unresolved_events'])}\n"
            "- Verification template: debt_lineage_verification_template.json\n"
        )
        if debt_lineage_validation_warnings:
            summary += "- Lineage source validation warnings: " + "; ".join(debt_lineage_validation_warnings) + "\n"
    (workspace / "summary.md").write_text(summary, encoding="utf-8")

    print(workspace / "summary.md")
    if merged is not None and merged.get("errors"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

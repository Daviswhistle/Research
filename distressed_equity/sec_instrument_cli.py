from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
from typing import Any

from .contract_graph import enhance_expanded_packet_with_contract_identity
from .cross_cik import cross_cik_graph_to_dict, expand_packet_with_cross_cik
from .sec import SecClient
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract source-backed debt-instrument candidates from SEC exhibits"
    )
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--ticker")
    identity.add_argument("--cik")
    parser.add_argument("--analysis-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--user-agent", help="Defaults to SEC_USER_AGENT")
    parser.add_argument("--filing-limit", type=int, default=12)
    parser.add_argument("--max-exhibits-per-filing", type=int, default=8)
    parser.add_argument("--max-candidates-per-exhibit", type=int, default=20)
    parser.add_argument("--reference-depth", type=int, default=3)
    parser.add_argument("--reference-max-nodes", type=int, default=80)
    parser.add_argument("--cross-cik-max-nodes", type=int, default=80)
    parser.add_argument("--contract-search-max-filings", type=int, default=40)
    parser.add_argument("--contract-days-before-execution", type=int, default=30)
    parser.add_argument("--contract-days-after-execution", type=int, default=550)
    parser.add_argument(
        "--no-resolve-references",
        action="store_true",
        help="Do not follow incorporation-by-reference links to older SEC filings/exhibits",
    )
    parser.add_argument(
        "--no-contract-identity-fallback",
        action="store_true",
        help="Do not reverse-search locator-less contract title + execution-date references",
    )
    parser.add_argument(
        "--no-cross-cik",
        action="store_true",
        help="Do not follow explicit foreign-CIK SEC Archives/CIK references",
    )
    parser.add_argument("--output", "-o", help="Expanded source packet JSON path; stdout when omitted")
    parser.add_argument("--graph-output", help="Optional same-CIK source-document graph JSON path")
    parser.add_argument("--cross-cik-output", help="Optional cross-CIK legal-entity/source graph JSON path")
    parser.add_argument("--task-output", help="Optional verification task JSON path")
    parser.add_argument("--template-output", help="Optional ledger-ready verification template JSON path")
    return parser


def _write(path: str | None, payload: object) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def _jsonable(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cutoff = date.fromisoformat(args.analysis_date)
    client = SecClient(user_agent=args.user_agent)
    snapshot = client.snapshot(
        analysis_date=cutoff,
        ticker=args.ticker,
        cik=args.cik,
        forms=("10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A"),
        filing_limit=max(args.filing_limit * 3, args.filing_limit),
    )
    packet = build_sec_instrument_packet(
        client,
        ticker=snapshot.ticker,
        company_name=snapshot.company_name,
        analysis_date=cutoff,
        filings=snapshot.filings,
        filing_limit=args.filing_limit,
        max_exhibits_per_filing=args.max_exhibits_per_filing,
        max_candidates_per_exhibit=args.max_candidates_per_exhibit,
    )

    graph_payload = None
    cross_cik_payload = None
    if not args.no_resolve_references:
        expanded = expand_instrument_packet_with_references(
            client,
            packet=packet,
            cik=snapshot.cik,
            seed_filings=snapshot.filings,
            max_depth=args.reference_depth,
            max_nodes=args.reference_max_nodes,
            max_candidates_per_exhibit=args.max_candidates_per_exhibit,
        )
        if not args.no_contract_identity_fallback:
            expanded = enhance_expanded_packet_with_contract_identity(
                client,
                expanded=expanded,
                cik=snapshot.cik,
                seed_filings=snapshot.filings,
                max_depth=args.reference_depth,
                max_nodes=args.reference_max_nodes,
                max_contract_search_filings=args.contract_search_max_filings,
                contract_days_before_execution=args.contract_days_before_execution,
                contract_days_after_execution=args.contract_days_after_execution,
                max_candidates_per_exhibit=args.max_candidates_per_exhibit,
            )
        graph_payload = source_document_graph_to_dict(expanded.graph)
        if not args.no_cross_cik:
            cross_expanded = expand_packet_with_cross_cik(
                client,
                expanded=expanded,
                root_cik=snapshot.cik,
                max_depth=args.reference_depth,
                max_nodes=args.cross_cik_max_nodes,
                max_candidates_per_exhibit=args.max_candidates_per_exhibit,
            )
            packet = cross_expanded.packet
            cross_cik_payload = cross_cik_graph_to_dict(cross_expanded.cross_cik_graph)
        else:
            packet = expanded.packet

    _write(args.output, sec_instrument_packet_to_dict(packet))
    if args.graph_output and graph_payload is not None:
        _write(args.graph_output, graph_payload)
    if args.cross_cik_output and cross_cik_payload is not None:
        _write(args.cross_cik_output, cross_cik_payload)

    if args.task_output:
        _write(
            args.task_output,
            {
                "ticker": snapshot.ticker,
                "company_name": snapshot.company_name,
                "analysis_date": cutoff.isoformat(),
                "task": _jsonable(asdict(build_instrument_verification_task(packet))),
            },
        )
    if args.template_output:
        _write(args.template_output, instrument_verification_template(packet))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

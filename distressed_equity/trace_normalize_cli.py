from __future__ import annotations

import argparse
import csv
from datetime import date
import json
from pathlib import Path
import sys

from .trace_security_master import (
    build_trace_security_master_resolver,
    read_trace_daily_list_events,
    read_trace_security_master_snapshot,
)
from .trace_symbol_transactions import read_trace_enhanced_files_with_security_master
from .trace_transactions import (
    TraceAggregationConfig,
    normalize_trace_transactions,
    read_trace_enhanced_files,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize raw FINRA TRACE Enhanced Historical transaction files into "
            "one explicit daily bond observation per CUSIP"
        )
    )
    parser.add_argument(
        "input",
        nargs="+",
        help=(
            "One or more Enhanced Historical pipe-delimited files. CUSIP-bearing files work directly; "
            "symbol-only files require source-backed point-in-time identity inputs below."
        ),
    )
    parser.add_argument("--output", "-o", required=True, help="Normalized daily CSV output")
    parser.add_argument(
        "--metadata-output",
        help="Optional JSON with lifecycle/filter/identity counts, config, and unresolved warnings",
    )
    parser.add_argument(
        "--trace-daily-list-csv",
        action="append",
        default=[],
        help=(
            "FINRA TRACE Corporate/Agency Daily List security export containing effective-dated "
            "addition/deletion/change identity events; repeat for multiple files"
        ),
    )
    parser.add_argument(
        "--trace-security-master-csv",
        action="append",
        default=[],
        help=(
            "CUSIP-licensed TRACE Security Master snapshot containing Symbol and CUSIP; repeat for multiple files. "
            "Requires --trace-security-master-as-of and is never backfilled before that date."
        ),
    )
    parser.add_argument(
        "--trace-security-master-as-of",
        help="Earliest validity date for supplied Security Master snapshot(s), YYYY-MM-DD",
    )
    parser.add_argument(
        "--aggregation",
        choices=("vwap", "last"),
        default="vwap",
        help="Daily price/yield policy: quantity-weighted VWAP or last execution",
    )
    parser.add_argument(
        "--dissemination-policy",
        choices=("disseminated", "all"),
        default="disseminated",
        help="Default uses disseminated reports only to avoid inter-dealer two-sided double counting",
    )
    parser.add_argument(
        "--unresolved-correction-policy",
        choices=("error", "warn"),
        default="error",
        help="What to do when a cancel/correction/reversal target is outside the supplied input set",
    )
    parser.add_argument(
        "--include-weighted-average-price",
        action="store_true",
        help="Include Trade Modifier 4=W reports; excluded by default to avoid re-aggregating an already weighted price",
    )
    parser.add_argument(
        "--include-special-price",
        action="store_true",
        help="Include Special Price Indicator=Y reports; excluded by default",
    )
    parser.add_argument(
        "--include-primary-market",
        action="store_true",
        help="Include Trading Market Indicator=P1 reports when allowed by the dissemination policy",
    )
    return parser


def _write_csv(path: Path, result) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "date",
        "cusip",
        "price_pct_par",
        "yield_pct",
        "volume",
        "source",
        "trade_count",
        "latest_execution_time",
        "aggregation_policy",
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for daily in result.observations:
            observation = daily.observation
            writer.writerow(
                {
                    "date": observation.date.isoformat(),
                    "cusip": observation.cusip or "",
                    "price_pct_par": "" if observation.price_pct_par is None else observation.price_pct_par,
                    "yield_pct": "" if observation.yield_pct is None else observation.yield_pct,
                    "volume": "" if observation.volume is None else observation.volume,
                    "source": observation.source or "",
                    "trade_count": daily.trade_count,
                    "latest_execution_time": daily.latest_execution_time,
                    "aggregation_policy": daily.aggregation_policy,
                }
            )


def _identity_read(args: argparse.Namespace):
    if args.trace_security_master_csv and not args.trace_security_master_as_of:
        raise ValueError("--trace-security-master-csv requires --trace-security-master-as-of")
    if args.trace_security_master_as_of and not args.trace_security_master_csv:
        raise ValueError("--trace-security-master-as-of requires --trace-security-master-csv")

    events = []
    if args.trace_daily_list_csv:
        events.extend(read_trace_daily_list_events(args.trace_daily_list_csv))
    if args.trace_security_master_csv:
        snapshot_date = date.fromisoformat(args.trace_security_master_as_of)
        events.extend(
            read_trace_security_master_snapshot(
                args.trace_security_master_csv,
                as_of=snapshot_date,
            )
        )
    if not events:
        return read_trace_enhanced_files(args.input), {
            "mode": "raw_cusip_only",
            "resolver_interval_count": 0,
            "symbol_resolved_record_count": 0,
            "raw_cusip_record_count": None,
        }

    resolver = build_trace_security_master_resolver(events)
    identity = read_trace_enhanced_files_with_security_master(args.input, resolver)
    return identity.transactions, {
        "mode": "point_in_time_symbol_cusip",
        "resolver_interval_count": identity.resolver_interval_count,
        "symbol_resolved_record_count": identity.symbol_resolved_record_count,
        "raw_cusip_record_count": identity.raw_cusip_record_count,
        "daily_list_inputs": [str(Path(item)) for item in args.trace_daily_list_csv],
        "security_master_inputs": [str(Path(item)) for item in args.trace_security_master_csv],
        "security_master_as_of": args.trace_security_master_as_of,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = TraceAggregationConfig(
        aggregation=args.aggregation,
        dissemination_policy=args.dissemination_policy,
        unresolved_correction_policy=args.unresolved_correction_policy,
        include_weighted_average_price=args.include_weighted_average_price,
        include_special_price=args.include_special_price,
        secondary_market_only=not args.include_primary_market,
    )
    transactions, identity_metadata = _identity_read(args)
    result = normalize_trace_transactions(transactions, config=config)

    output = Path(args.output)
    _write_csv(output, result)

    metadata = {
        "inputs": [str(Path(item)) for item in args.input],
        "output": str(output),
        "identity": identity_metadata,
        "config": {
            "aggregation": config.aggregation,
            "dissemination_policy": config.dissemination_policy,
            "unresolved_correction_policy": config.unresolved_correction_policy,
            "include_weighted_average_price": config.include_weighted_average_price,
            "include_special_price": config.include_special_price,
            "secondary_market_only": config.secondary_market_only,
        },
        "parsed_record_count": result.parsed_record_count,
        "exact_duplicate_record_count": result.exact_duplicate_record_count,
        "active_record_count": result.active_record_count,
        "included_record_count": result.included_record_count,
        "daily_observation_count": len(result.observations),
        "cancelled_record_count": result.cancelled_record_count,
        "unresolved_correction_count": result.unresolved_correction_count,
        "warnings": list(result.warnings),
    }
    if args.metadata_output:
        metadata_path = Path(args.metadata_output)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for warning in result.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

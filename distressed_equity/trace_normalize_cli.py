from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

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
        help="One or more official CUSIP-bearing Enhanced Historical pipe-delimited files",
    )
    parser.add_argument("--output", "-o", required=True, help="Normalized daily CSV output")
    parser.add_argument(
        "--metadata-output",
        help="Optional JSON with lifecycle/filter counts, config, and unresolved warnings",
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
    transactions = read_trace_enhanced_files(args.input)
    result = normalize_trace_transactions(transactions, config=config)

    output = Path(args.output)
    _write_csv(output, result)

    metadata = {
        "inputs": [str(Path(item)) for item in args.input],
        "output": str(output),
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

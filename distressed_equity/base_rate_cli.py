from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json

from .base_rates import load_base_rate_library


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Point-in-time distress base-rate summary")
    parser.add_argument("library", help="Base-rate cases JSON/JSONL")
    parser.add_argument("--cutoff", required=True, help="Only outcomes known by this YYYY-MM-DD are usable")
    parser.add_argument("--industry-group")
    parser.add_argument("--impairment-type")
    parser.add_argument("--leverage-bucket")
    parser.add_argument("--exclude-case", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    library = load_base_rate_library(args.library)
    summary = library.summarize(
        date.fromisoformat(args.cutoff),
        industry_group=args.industry_group,
        impairment_type=args.impairment_type,
        leverage_bucket=args.leverage_bucket,
        exclude_case_ids=args.exclude_case,
    )
    print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

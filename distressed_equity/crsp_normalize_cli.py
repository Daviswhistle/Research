from __future__ import annotations

import argparse
import json
from pathlib import Path

from .crsp_normalize import (
    CrspColumnConfig,
    CrspNormalizeConfig,
    crsp_normalization_result_to_dict,
    normalize_crsp_exports,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize licensed CRSP/WRDS-like stock-name and market exports into the point-in-time "
            "securities/prices CSV contract consumed by CsvMarketProvider"
        )
    )
    parser.add_argument("--stocknames-csv", required=True)
    parser.add_argument("--market-csv", required=True)
    parser.add_argument("--securities-output", required=True)
    parser.add_argument("--prices-output", required=True)
    parser.add_argument("--metadata-output")
    parser.add_argument(
        "--return-semantics",
        choices=("legacy", "already_total"),
        required=True,
        help=(
            "legacy = combine return and optional delisting-return fields as (1+RET)*(1+DLRET)-1; "
            "already_total = treat the selected return field as the complete total return and never add DLRET"
        ),
    )
    parser.add_argument(
        "--share-codes",
        default="10,11",
        help="Comma-separated CRSP share codes to retain; use 'all' to disable the share-code filter",
    )
    parser.add_argument(
        "--missing-return-code",
        action="append",
        default=[],
        type=float,
        help="Numeric vendor missing-return sentinel to treat as missing; repeat as needed",
    )
    parser.add_argument(
        "--shares-outstanding-scale",
        type=float,
        help=(
            "Explicit multiplier for a supplied shares-outstanding column. No shares column is auto-detected "
            "because units differ across export contracts."
        ),
    )

    # Stock-name/history overrides.
    parser.add_argument("--stocknames-permno-column")
    parser.add_argument("--stocknames-start-column")
    parser.add_argument("--stocknames-end-column")
    parser.add_argument("--stocknames-ticker-column")
    parser.add_argument("--stocknames-name-column")
    parser.add_argument("--stocknames-share-code-column")
    parser.add_argument("--stocknames-exchange-code-column")

    # Market-history overrides.
    parser.add_argument("--market-permno-column")
    parser.add_argument("--market-date-column")
    parser.add_argument("--market-price-column")
    parser.add_argument("--market-return-column")
    parser.add_argument("--market-delisting-return-column")
    parser.add_argument("--market-shares-outstanding-column")
    return parser


def _share_codes(value: str) -> tuple[int, ...] | None:
    clean = value.strip().lower()
    if clean == "all":
        return None
    output: list[int] = []
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        try:
            parsed = int(item)
        except ValueError as exc:
            raise ValueError("--share-codes must contain integers or 'all'") from exc
        output.append(parsed)
    if not output:
        raise ValueError("--share-codes must contain at least one code or 'all'")
    if len(set(output)) != len(output):
        raise ValueError("--share-codes contains duplicate codes")
    return tuple(output)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    columns = CrspColumnConfig(
        stocknames_permno=args.stocknames_permno_column,
        stocknames_start=args.stocknames_start_column,
        stocknames_end=args.stocknames_end_column,
        stocknames_ticker=args.stocknames_ticker_column,
        stocknames_name=args.stocknames_name_column,
        stocknames_share_code=args.stocknames_share_code_column,
        stocknames_exchange_code=args.stocknames_exchange_code_column,
        market_permno=args.market_permno_column,
        market_date=args.market_date_column,
        market_price=args.market_price_column,
        market_return=args.market_return_column,
        market_delisting_return=args.market_delisting_return_column,
        market_shares_outstanding=args.market_shares_outstanding_column,
    )
    config = CrspNormalizeConfig(
        return_semantics=args.return_semantics,
        share_codes=_share_codes(args.share_codes),
        missing_return_codes=tuple(args.missing_return_code),
        shares_outstanding_scale=args.shares_outstanding_scale,
        columns=columns,
    )
    result = normalize_crsp_exports(
        args.stocknames_csv,
        args.market_csv,
        args.securities_output,
        args.prices_output,
        config=config,
    )
    payload = crsp_normalization_result_to_dict(result)
    payload["inputs"] = {
        "stocknames_csv": str(Path(args.stocknames_csv)),
        "market_csv": str(Path(args.market_csv)),
    }
    payload["outputs"] = {
        "securities_csv": str(Path(args.securities_output)),
        "prices_csv": str(Path(args.prices_output)),
    }
    payload["config"] = {
        "return_semantics": config.return_semantics,
        "share_codes": None if config.share_codes is None else list(config.share_codes),
        "missing_return_codes": list(config.missing_return_codes),
        "shares_outstanding_scale": config.shares_outstanding_scale,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.metadata_output:
        target = Path(args.metadata_output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path

from .alpha_vantage import AlphaVantageProvider
from .csv_market import CsvMarketProvider
from .replay import DistressScanConfig, ReplayRun, run_historical_replay, seed_to_dict


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Survivorship-aware historical price-distress replay"
    )
    parser.add_argument("--analysis-date", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--provider",
        choices=("alpha-vantage", "csv"),
        required=True,
    )
    parser.add_argument("--api-key", help="Alpha Vantage API key; defaults to ALPHA_VANTAGE_API_KEY")
    parser.add_argument("--securities-csv", help="Normalized bulk security-master CSV")
    parser.add_argument("--prices-csv", help="Normalized bulk price CSV")
    parser.add_argument("--symbols", help="Comma-separated symbols; recommended for Alpha Vantage")
    parser.add_argument("--symbols-file", help="One symbol per line")
    parser.add_argument("--exchange", action="append", default=[])
    parser.add_argument("--min-drawdown", type=float, default=0.60)
    parser.add_argument("--min-price", type=float, default=0.10)
    parser.add_argument("--lookback-years", type=int, default=5)
    parser.add_argument("--max-securities", type=int)
    parser.add_argument("--allow-slow-full-universe", action="store_true")
    parser.add_argument(
        "--allow-survivorship-unsafe-universe",
        action="store_true",
        help="Allow replay when provider lacks point-in-time membership provenance; output is explicitly marked unsafe",
    )
    parser.add_argument("--output", "-o", help="JSON output path; stdout when omitted")
    parser.add_argument("--markdown-output", help="Optional human-readable summary path")
    return parser


def _symbols(args: argparse.Namespace) -> tuple[str, ...] | None:
    values: list[str] = []
    if args.symbols:
        values.extend(part.strip() for part in args.symbols.split(",") if part.strip())
    if args.symbols_file:
        values.extend(
            line.strip()
            for line in Path(args.symbols_file).read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    return tuple(dict.fromkeys(value.upper() for value in values)) if values else None


def _provider(args: argparse.Namespace):
    if args.provider == "alpha-vantage":
        return AlphaVantageProvider(api_key=args.api_key)
    if not args.securities_csv or not args.prices_csv:
        raise SystemExit("csv provider requires --securities-csv and --prices-csv")
    return CsvMarketProvider(args.securities_csv, args.prices_csv)


def _payload(run: ReplayRun) -> dict[str, object]:
    return {
        "provider": run.provider,
        "analysis_date": run.analysis_date.isoformat(),
        "historical_universe_size": run.historical_universe_size,
        "scanned_security_count": run.scanned_security_count,
        "survivorship_guard": run.survivorship_guard,
        "candidates": [seed_to_dict(seed) for seed in run.candidates],
        "skipped": [asdict(skip) for skip in run.skipped],
    }


def _markdown(run: ReplayRun) -> str:
    lines = [
        f"# Historical distress replay — {run.analysis_date.isoformat()}",
        "",
        f"- provider: `{run.provider}`",
        f"- historical universe: **{run.historical_universe_size:,}**",
        f"- scanned: **{run.scanned_security_count:,}**",
        f"- price-distress candidates: **{len(run.candidates):,}**",
        f"- survivorship guard: {run.survivorship_guard}",
        "",
        "| Symbol | Exchange | Raw cutoff price | Adjusted drawdown | Adjusted peak date |",
        "|---|---|---:|---:|---|",
    ]
    for seed in run.candidates:
        lines.append(
            f"| {seed.security.symbol} | {seed.security.exchange or '—'} | "
            f"{seed.raw_price.close:.2f} ({seed.raw_price.date.isoformat()}) | "
            f"{seed.adjusted_drawdown_from_peak:.1%} | {seed.peak_adjusted_price.date.isoformat()} |"
        )
    lines.extend(
        [
            "",
            "> 이 단계의 drawdown은 후보 생성용 adjusted-price proxy입니다. 최종 distress gate의 peak market cap/EV 재구성을 대체하지 않습니다.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    provider = _provider(args)
    cutoff = date.fromisoformat(args.analysis_date)
    config = DistressScanConfig(
        min_adjusted_drawdown=args.min_drawdown,
        lookback_years=args.lookback_years,
        min_raw_price=args.min_price,
        exchanges=tuple(args.exchange),
    )
    run = run_historical_replay(
        provider,
        cutoff,
        config=config,
        symbols=_symbols(args),
        allow_slow_full_universe=args.allow_slow_full_universe,
        allow_survivorship_unsafe_universe=args.allow_survivorship_unsafe_universe,
        max_securities=args.max_securities,
    )
    text = json.dumps(_payload(run), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    if args.markdown_output:
        target = Path(args.markdown_output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_markdown(run), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
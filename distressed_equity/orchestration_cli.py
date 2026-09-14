from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .alpha_vantage import AlphaVantageProvider
from .orchestration import build_research_bundle, ingest_research_bundle, render_research_summary
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
    parser.add_argument("--market-provider", choices=("none", "alpha-vantage"), default="none")
    parser.add_argument("--alpha-vantage-key", help="Defaults to ALPHA_VANTAGE_API_KEY")
    parser.add_argument("--history-years", type=int, default=5)
    parser.add_argument(
        "--result",
        action="append",
        default=[],
        help="Structured agent-result JSON; repeat to ingest multiple results",
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from datetime import date

    cutoff = date.fromisoformat(args.analysis_date)
    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)

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
        market_provider=market_provider,
        history_years=args.history_years,
    )

    _write_json(workspace / "research_packet.json", bundle.packet)
    _write_json(workspace / "capital_stack.json", bundle.packet["capital_stack_packet"])
    _write_json(workspace / "capital_stack_diff.json", bundle.packet["capital_stack_diff"])
    _write_json(workspace / "tasks.json", {"tasks": bundle.packet["agent_tasks"]})
    if bundle.packet.get("market_snapshot") is not None:
        _write_json(workspace / "market_snapshot.json", bundle.packet["market_snapshot"])

    merged = None
    if args.result:
        raw_results = [_load_json(path) for path in args.result]
        merged = ingest_research_bundle(
            bundle,
            raw_results,
            allow_overwrite=args.allow_overwrite,
            apply_low_confidence=args.apply_low_confidence,
        )
        _write_json(workspace / "merged.json", merged)

    (workspace / "summary.md").write_text(
        render_research_summary(bundle, merged), encoding="utf-8"
    )

    print(workspace / "summary.md")
    if merged is not None and merged.get("errors"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

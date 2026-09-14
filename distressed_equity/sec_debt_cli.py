from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
from typing import Any

from .sec import SecClient
from .sec_debt import (
    build_capital_stack_agent_task,
    build_capital_stack_packet,
    capital_stack_packet_to_dict,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract point-in-time SEC debt/covenant evidence snippets"
    )
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--ticker")
    identity.add_argument("--cik")
    parser.add_argument("--analysis-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--user-agent", help="Defaults to SEC_USER_AGENT")
    parser.add_argument("--filing-limit", type=int, default=3)
    parser.add_argument("--max-snippets-per-filing", type=int, default=50)
    parser.add_argument("--output", "-o", help="Capital-stack packet JSON path")
    parser.add_argument("--task-output", help="Optional agent task JSON path")
    return parser


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


def _write(path: str | None, payload: object) -> None:
    text = json.dumps(_jsonable(payload), ensure_ascii=False, indent=2) + "\n"
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cutoff = date.fromisoformat(args.analysis_date)
    client = SecClient(user_agent=args.user_agent)
    snapshot = client.snapshot(
        analysis_date=cutoff,
        ticker=args.ticker,
        cik=args.cik,
        forms=("10-K", "10-K/A", "10-Q", "10-Q/A", "8-K"),
        filing_limit=max(args.filing_limit * 3, args.filing_limit),
    )
    packet = build_capital_stack_packet(
        client,
        ticker=snapshot.ticker,
        company_name=snapshot.company_name,
        analysis_date=cutoff,
        filings=snapshot.filings,
        filing_limit=args.filing_limit,
        max_snippets_per_filing=args.max_snippets_per_filing,
    )
    _write(args.output, capital_stack_packet_to_dict(packet))
    if args.task_output:
        _write(
            args.task_output,
            {
                "ticker": snapshot.ticker,
                "company_name": snapshot.company_name,
                "analysis_date": cutoff.isoformat(),
                "task": asdict(build_capital_stack_agent_task(packet)),
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

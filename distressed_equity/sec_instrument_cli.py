from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
from typing import Any

from .sec import SecClient
from .sec_instruments import (
    build_instrument_verification_task,
    build_sec_instrument_packet,
    instrument_verification_template,
    sec_instrument_packet_to_dict,
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
    parser.add_argument("--output", "-o", help="Source packet JSON path; stdout when omitted")
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
    _write(args.output, sec_instrument_packet_to_dict(packet))

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

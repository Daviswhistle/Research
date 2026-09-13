from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
from typing import Any

from .evidence import validate_point_in_time
from .sec import DEFAULT_FORMS, SecClient, snapshot_to_dict, snapshot_to_evidence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Point-in-time SEC evidence snapshot for distressed-equity research")
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--ticker", help="U.S. listed-company ticker, resolved through SEC company_tickers.json")
    identity.add_argument("--cik", help="SEC CIK; leading zeros are optional")
    parser.add_argument("--analysis-date", required=True, help="Historical information cutoff, YYYY-MM-DD")
    parser.add_argument(
        "--user-agent",
        help="SEC User-Agent identifying the application and contact; defaults to SEC_USER_AGENT",
    )
    parser.add_argument(
        "--forms",
        default=",".join(DEFAULT_FORMS),
        help="Comma-separated filing forms to retain",
    )
    parser.add_argument("--filing-limit", type=int, default=20)
    parser.add_argument("--output", "-o", help="JSON output path; stdout when omitted")
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cutoff = date.fromisoformat(args.analysis_date)
    forms = tuple(form.strip() for form in args.forms.split(",") if form.strip())
    client = SecClient(user_agent=args.user_agent)
    snapshot = client.snapshot(
        analysis_date=cutoff,
        ticker=args.ticker,
        cik=args.cik,
        forms=forms,
        filing_limit=args.filing_limit,
    )
    evidence = snapshot_to_evidence(snapshot)
    payload = {
        "snapshot": snapshot_to_dict(snapshot),
        "evidence": [_jsonable(asdict(record)) for record in evidence],
        "point_in_time_violations": list(validate_point_in_time(evidence, cutoff)),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

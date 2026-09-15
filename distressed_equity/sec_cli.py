from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
from typing import Any

from .agent_results import agent_result_template
from .evidence import validate_point_in_time
from .prefill import build_screening_draft, build_sec_prefill_tasks
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
    parser.add_argument("--output", "-o", help="JSON research packet path; stdout when omitted")
    parser.add_argument("--tasks-output", help="Optional JSON path containing agent research tasks only")
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


def _write_json(path: str, payload: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(_jsonable(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


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
    tasks = build_sec_prefill_tasks(snapshot)
    templates = {task.name: agent_result_template(task.name, cutoff) for task in tasks}
    payload = {
        "snapshot": snapshot_to_dict(snapshot),
        "evidence": [_jsonable(asdict(record)) for record in evidence],
        "point_in_time_violations": list(validate_point_in_time(evidence, cutoff)),
        "screening_draft": build_screening_draft(snapshot),
        "agent_tasks": [_jsonable(asdict(task)) for task in tasks],
        "agent_result_templates": templates,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    else:
        print(text, end="")

    if args.tasks_output:
        _write_json(
            args.tasks_output,
            {
                "ticker": snapshot.ticker,
                "company_name": snapshot.company_name,
                "analysis_date": snapshot.analysis_date.isoformat(),
                "tasks": [asdict(task) for task in tasks],
                "result_templates": templates,
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

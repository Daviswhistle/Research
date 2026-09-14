from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
from typing import Any

from .agent_results import (
    agent_result_from_dict,
    agent_result_template,
    ingest_agent_results,
    ingestion_report_to_dict,
)
from .io import screening_candidate_from_dict
from .screening import screen_candidate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate structured agent research results and patch a SEC screening draft"
    )
    parser.add_argument("packet", help="Research packet JSON produced by distressed-equity-sec")
    parser.add_argument(
        "--result",
        action="append",
        default=[],
        help="Agent-result JSON path; repeat for multiple tasks",
    )
    parser.add_argument("--output", "-o", help="Merged/validated research-state JSON path")
    parser.add_argument(
        "--allow-overwrite",
        action="store_true",
        help="Allow a validated patch to replace an already populated non-null value",
    )
    parser.add_argument(
        "--apply-low-confidence",
        action="store_true",
        help="Apply low-confidence patches; default is evidence-only without mutation",
    )
    parser.add_argument(
        "--template-task",
        help="Instead of ingesting, print a result template for this task name",
    )
    parser.add_argument(
        "--analysis-date",
        help="Required with --template-task; YYYY-MM-DD",
    )
    return parser


def _load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _write(payload: object, output: str | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if output:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.template_task:
        if not args.analysis_date:
            raise SystemExit("--analysis-date is required with --template-task")
        _write(
            agent_result_template(args.template_task, date.fromisoformat(args.analysis_date)),
            args.output,
        )
        return 0

    packet = _load_json(args.packet)
    results = [agent_result_from_dict(_load_json(path)) for path in args.result]
    report = ingest_agent_results(
        packet,
        results,
        allow_overwrite=args.allow_overwrite,
        apply_low_confidence=args.apply_low_confidence,
    )
    payload = ingestion_report_to_dict(report)
    if report.ready_for_screening:
        candidate = screening_candidate_from_dict(report.screening_candidate_draft)
        payload["screening_result"] = asdict(screen_candidate(candidate))
    else:
        payload["screening_result"] = None
    _write(payload, args.output)
    return 0 if not report.errors else 2


if __name__ == "__main__":
    raise SystemExit(main())

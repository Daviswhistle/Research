from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path

from .agent_results import agent_result_template
from .agents import build_screening_tasks
from .io import load_screening_universe
from .screen_report import render_universe_markdown
from .screening import ScreeningConfig, screen_universe


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Carvana-2022 style distressed-equity universe screener")
    parser.add_argument("universe", help="Path to JSON or JSONL candidate universe")
    parser.add_argument("--output", "-o", help="Markdown output path; stdout when omitted")
    parser.add_argument("--json-output", help="Optional machine-readable screening result JSON path")
    parser.add_argument("--tasks-output", help="Optional agent research-task manifest JSON path")
    parser.add_argument("--min-drawdown", type=float, default=0.60)
    parser.add_argument("--min-base-multiple", type=float, default=3.0)
    parser.add_argument("--max-critical-assumptions", type=int, default=2)
    parser.add_argument("--runway-months", type=int, default=60)
    return parser


def _write_json(path: str, payload: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    candidates = load_screening_universe(args.universe)
    config = ScreeningConfig(
        min_drawdown_from_peak=args.min_drawdown,
        min_base_equity_multiple=args.min_base_multiple,
        max_critical_assumptions=args.max_critical_assumptions,
        runway_projection_months=args.runway_months,
    )
    results = screen_universe(candidates, config)
    report = render_universe_markdown(results)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report, encoding="utf-8")
    else:
        print(report, end="")

    if args.json_output:
        _write_json(args.json_output, [asdict(result) for result in results])

    if args.tasks_output:
        candidates_by_ticker = {candidate.ticker: candidate for candidate in candidates}
        manifest = []
        for result in results:
            candidate = candidates_by_ticker[result.ticker]
            tasks = build_screening_tasks(candidate, result)
            if tasks:
                cutoff = date.fromisoformat(candidate.analysis_date) if candidate.analysis_date else None
                templates = (
                    {task.name: agent_result_template(task.name, cutoff) for task in tasks}
                    if cutoff is not None
                    else {}
                )
                manifest.append(
                    {
                        "ticker": result.ticker,
                        "company_name": result.company_name,
                        "priority": result.priority,
                        "analysis_date": candidate.analysis_date,
                        "tasks": [asdict(task) for task in tasks],
                        "result_templates": templates,
                    }
                )
        _write_json(args.tasks_output, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

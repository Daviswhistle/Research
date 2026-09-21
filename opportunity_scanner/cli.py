from __future__ import annotations

import argparse
from pathlib import Path

from research_pipeline.models import ResearchQuery
from research_pipeline.pipeline import ResearchPipeline

from .filters import MinimumEvidenceGate, PermissionGate, ValidationBudgetGate
from .report import render_markdown
from .scoring import OpportunityAxisScorer
from .selector import ParetoLayerSelector
from .source import JsonlOpportunitySource


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prioritize cheap, falsifiable profit experiments."
    )
    parser.add_argument("--input", required=True, help="Opportunity JSONL")
    parser.add_argument("--output", help="Write Markdown report to this path")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--min-signals", type=int, default=2)
    parser.add_argument("--max-cash", type=float, default=500.0)
    parser.add_argument("--max-hours", type=float, default=24.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    pipeline = ResearchPipeline(
        sources=[JsonlOpportunitySource(args.input)],
        filters=[
            PermissionGate(),
            MinimumEvidenceGate(minimum=args.min_signals),
            ValidationBudgetGate(
                max_cash_usd=args.max_cash,
                max_human_hours=args.max_hours,
            ),
        ],
        scorers=[OpportunityAxisScorer()],
        selector=ParetoLayerSelector(),
    )
    query = ResearchQuery(
        limit=args.limit,
        context={
            "minimum_revenue_signals": args.min_signals,
            "max_cash_usd": args.max_cash,
            "max_human_hours": args.max_hours,
        },
    )
    report = render_markdown(pipeline.run(query))

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

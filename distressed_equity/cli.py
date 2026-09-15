from __future__ import annotations

import argparse
from pathlib import Path

from .engine import analyze_case
from .io import load_case
from .report import render_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Distressed-equity convexity research engine")
    parser.add_argument("case", help="Path to case JSON")
    parser.add_argument("--output", "-o", help="Markdown output path; stdout when omitted")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    case = load_case(args.case)
    result = analyze_case(case)
    report = render_markdown(case, result)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report, encoding="utf-8")
    else:
        print(report, end="")
    return 0

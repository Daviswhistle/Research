from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .debt_instruments import build_debt_instrument_ledger, debt_instrument_ledger_to_dict, debt_snapshots_from_dict
from .instrument_verification import validate_debt_snapshots_against_source_packet


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Match debt instruments across filings and report amendment candidates"
    )
    parser.add_argument("input", help="JSON with filing-level debt instrument snapshots")
    parser.add_argument("--output", "-o", help="JSON ledger output; stdout when omitted")
    parser.add_argument(
        "--source-packet",
        help="Optional SEC debt_instrument_sources.json; when supplied every snapshot must cite valid frozen source spans",
    )
    parser.add_argument("--match-threshold", type=float, default=55.0)
    parser.add_argument("--ambiguity-margin", type=float, default=8.0)
    return parser


def _load(path: str | Path) -> dict[str, Any] | list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, (dict, list)):
        raise ValueError("debt instrument input must be a JSON object or list")
    return payload


def _load_object(path: str | Path) -> dict[str, Any]:
    payload = _load(path)
    if not isinstance(payload, dict):
        raise ValueError("source packet must be a JSON object")
    return payload


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    raw = _load(args.input)
    validation_warnings: list[str] = []
    if args.source_packet:
        validation = validate_debt_snapshots_against_source_packet(
            _load_object(args.source_packet),
            raw,
        )
        if not validation.valid:
            raise ValueError("invalid source-backed debt snapshots: " + "; ".join(validation.errors))
        snapshots = validation.snapshots
        validation_warnings.extend(validation.warnings)
    else:
        snapshots = debt_snapshots_from_dict(raw)

    ledger = build_debt_instrument_ledger(
        snapshots,
        match_threshold=args.match_threshold,
        ambiguity_margin=args.ambiguity_margin,
    )
    payload = debt_instrument_ledger_to_dict(ledger)
    if validation_warnings:
        payload["source_validation_warnings"] = validation_warnings
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

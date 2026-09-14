from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Any

from .agent_results import agent_result_from_dict, validate_agent_result
from .covenants import covenant_model_to_dict


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calculate covenant EBITDA/headroom and emit an ingestible covenant patch"
    )
    parser.add_argument("input", help="JSON file containing analysis_date, evidence and covenant models")
    parser.add_argument("--output", "-o", help="JSON output path; stdout when omitted")
    return parser


def _load(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("covenant input must be a JSON object")
    return payload


def _write(payload: object, output: str | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if output:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def build_covenant_agent_result(payload: dict[str, Any]) -> dict[str, Any]:
    analysis_date = date.fromisoformat(str(payload["analysis_date"])[:10])
    evidence = payload.get("evidence", [])
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("at least one evidence record is required")
    evidence_ids = {
        str(item.get("evidence_id") or "").strip()
        for item in evidence
        if isinstance(item, dict) and str(item.get("evidence_id") or "").strip()
    }
    if not evidence_ids:
        raise ValueError("evidence records must have evidence_id values")

    models_raw = payload.get("models")
    if not isinstance(models_raw, list) or not models_raw:
        raise ValueError("models must be a non-empty list")

    modeled: list[dict[str, Any]] = []
    covenant_risks: list[dict[str, Any]] = []
    all_refs: set[str] = set()
    for idx, raw in enumerate(models_raw):
        if not isinstance(raw, dict):
            raise ValueError(f"model {idx} must be an object")
        result = covenant_model_to_dict(raw)
        refs = set(result["definition"].get("source_refs", []))
        if not refs:
            raise ValueError(f"model {idx} must include definition.source_refs")
        unknown = refs - evidence_ids
        if unknown:
            raise ValueError(f"model {idx} references unknown evidence IDs: {sorted(unknown)}")
        all_refs.update(refs)
        modeled.append(result)
        covenant_risks.append(result["screening_covenant_risk"])

    raw_result = {
        "schema_version": "1",
        "task_name": "capital_stack_extractor",
        "analysis_date": analysis_date.isoformat(),
        "status": "complete",
        "evidence": evidence,
        "patches": [
            {
                "path": "covenants",
                "value": covenant_risks,
                "evidence_refs": sorted(all_refs),
                "confidence": str(payload.get("confidence") or "medium"),
                "rationale": "Deterministic covenant headroom calculation from source-backed covenant definitions and inputs.",
            }
        ],
        "unresolved": list(payload.get("unresolved", [])),
        "warnings": list(payload.get("warnings", [])),
    }
    parsed = agent_result_from_dict(raw_result)
    validation = validate_agent_result(parsed, expected_analysis_date=analysis_date)
    if not validation.valid:
        raise ValueError("invalid generated agent result: " + "; ".join(validation.errors))
    return {
        "analysis_date": analysis_date.isoformat(),
        "models": modeled,
        "agent_result": raw_result,
        "validation_warnings": list(validation.warnings),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_covenant_agent_result(_load(args.input))
    _write(payload, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

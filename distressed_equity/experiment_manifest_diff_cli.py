from __future__ import annotations

import argparse
import json
from pathlib import Path

from .experiment_manifest_diff import (
    ExperimentManifestDiff,
    compare_experiment_manifests,
    experiment_manifest_diff_to_dict,
    load_experiment_manifest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare two experiment manifests or analysis-output JSON files and separate input-content, "
            "config, code-revision, and path-only changes"
        )
    )
    parser.add_argument("before", help="Earlier manifest JSON or analysis output containing experiment_manifest")
    parser.add_argument("after", help="Later manifest JSON or analysis output containing experiment_manifest")
    parser.add_argument("--output", "-o", help="JSON diff output; stdout when omitted")
    parser.add_argument("--markdown-output", help="Optional human-readable diff summary")
    return parser


def _yn(value: bool | None) -> str:
    if value is None:
        return "unknown"
    return "yes" if value else "no"


def _short(value: str | None) -> str:
    return "—" if value is None else value[:12]


def _markdown(diff: ExperimentManifestDiff) -> str:
    lines = [
        "# Experiment manifest diff",
        "",
        f"- pipelines: **{diff.before_pipeline} → {diff.after_pipeline}**",
        f"- data/config changed: **{_yn(diff.data_config_changed)}**",
        f"- code revision changed: **{_yn(diff.code_revision_changed)}**",
        f"- exact experiment comparable: **{_yn(diff.exact_experiment_comparable)}**",
        f"- exact experiment changed: **{_yn(diff.experiment_changed)}**",
        f"- change categories: **{', '.join(diff.change_categories)}**",
        "",
        "## Identity",
        "",
        "| Field | Before | After |",
        "|---|---|---|",
        f"| Data/config fingerprint | `{_short(diff.before_data_config_fingerprint)}` | `{_short(diff.after_data_config_fingerprint)}` |",
        f"| Code revision | `{_short(diff.before_code_revision)}` | `{_short(diff.after_code_revision)}` |",
        f"| Experiment fingerprint | `{_short(diff.before_experiment_fingerprint)}` | `{_short(diff.after_experiment_fingerprint)}` |",
        "",
        "## Input roles",
        "",
        "| Role | Status | Content changed | Path changed | Before path | After path |",
        "|---|---|---:|---:|---|---|",
    ]
    for item in diff.input_changes:
        lines.append(
            f"| {item.role} | {item.status} | {_yn(item.content_changed)} | {_yn(item.path_changed)} | "
            f"{item.before_path or '—'} | {item.after_path or '—'} |"
        )

    lines.extend([
        "",
        "## Config changes",
        "",
        "| Path | Status | Before | After |",
        "|---|---|---|---|",
    ])
    if not diff.config_changes:
        lines.append("| — | none | — | — |")
    else:
        for item in diff.config_changes:
            before = json.dumps(item.before, ensure_ascii=False, sort_keys=True)
            after = json.dumps(item.after, ensure_ascii=False, sort_keys=True)
            lines.append(f"| `{item.path}` | {item.status} | `{before}` | `{after}` |")

    if diff.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {item}" for item in diff.warnings)

    lines.extend([
        "",
        "> Both serialized manifests are fingerprint-validated before comparison. Path-only changes are reported for audit but do not alter canonical experiment identity.",
        "",
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    before = load_experiment_manifest(args.before)
    after = load_experiment_manifest(args.after)
    diff = compare_experiment_manifests(before, after)
    payload = experiment_manifest_diff_to_dict(diff)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    if args.markdown_output:
        target = Path(args.markdown_output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_markdown(diff), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

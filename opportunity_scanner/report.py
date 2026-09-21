from __future__ import annotations

from research_pipeline.models import PipelineResult


_SCORE_ORDER = (
    "demand_strength",
    "evidence_quality",
    "automation_fit",
    "distribution_access",
    "cash_ease",
    "human_ease",
    "feedback_speed",
    "safety",
    "repeatability",
    "upside_convexity",
)


def render_markdown(result: PipelineResult) -> str:
    lines = [
        "# Opportunity Scanner",
        "",
        "This report ranks experiments to run, not businesses to declare winners.",
        "Facts and subjective priors remain separate in the input data.",
        "",
        f"- retrieved: {len(result.retrieved)}",
        f"- filtered: {len(result.filtered)}",
        f"- selected: {len(result.selected)}",
        "",
    ]

    for index, candidate in enumerate(result.selected, start=1):
        payload = candidate.payload
        validation = payload.get("validation", {})
        lines.extend(
            [
                f"## {index}. {candidate.title}",
                "",
                f"- mechanism: {payload.get('mechanism', 'unknown')}",
                f"- permission: {payload.get('permission_status', 'unknown')}",
                f"- experiment: {validation.get('experiment', 'n/a')}",
                f"- pass: {validation.get('pass_condition', 'n/a')}",
                f"- kill: {validation.get('kill_condition', 'n/a')}",
                f"- validation cost: ${validation.get('cash_usd', 0)} cash / "
                f"{validation.get('human_hours', 0)} human-hours / "
                f"{validation.get('feedback_days', 0)} days feedback",
                "",
                "| axis | value |",
                "|---|---:|",
            ]
        )
        for key in _SCORE_ORDER:
            lines.append(f"| {key} | {candidate.scores.get(key, 0.0):.3f} |")
        lines.append("")

        signals = payload.get("signals", [])
        if signals:
            lines.append("Evidence:")
            for signal in signals:
                note = signal.get("note", signal.get("kind", "signal"))
                url = signal.get("url", "")
                lines.append(f"- {note} — {url}" if url else f"- {note}")
            lines.append("")

    if result.filtered:
        lines.append("## Filtered out")
        lines.append("")
        for candidate in result.filtered:
            lines.append(f"- {candidate.title}")
        lines.append("")

    if result.warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in result.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    return "\n".join(lines)

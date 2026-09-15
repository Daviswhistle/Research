from __future__ import annotations

from .screening import ScreeningResult


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def _ratio(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def render_universe_markdown(results: tuple[ScreeningResult, ...]) -> str:
    lines: list[str] = ["# Distressed-equity universe screen", ""]
    counts = {key: sum(r.priority == key for r in results) for key in ("DEEP_DIVE_CANDIDATE", "RESEARCH", "DROP")}
    lines.append(
        f"**결과:** deep dive {counts['DEEP_DIVE_CANDIDATE']} / research {counts['RESEARCH']} / drop {counts['DROP']}"
    )
    lines.append("")
    lines.append("## 비교표")
    lines.append("")
    lines.append(
        "| 후보 | 우선순위 | Time to Death | Base payoff | Required P | Critical assumptions | Capture | Survival |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---|")
    for result in results:
        if not result.runway_known:
            ttd = "unknown"
        elif result.time_to_death_months is None:
            ttd = f">{result.runway_projection_months}m"
        else:
            ttd = f"{result.time_to_death_months}m"
        lines.append(
            f"| {result.company_name} ({result.ticker}) | **{result.priority}** | {ttd} | "
            f"{result.base_equity_multiple:.2f}x | {_pct(result.required_probability)} | "
            f"{result.critical_assumption_count} | {_ratio(result.common_equity_capture_ratio)} | {result.survival_gate} |"
        )
    lines.append("")

    for result in results:
        lines.append(f"## {result.company_name} ({result.ticker})")
        lines.append("")
        lines.append(f"**{result.priority}**")
        lines.append("")
        lines.append(
            f"- gates: distress={result.distress_gate}, real_business={result.real_business_gate}, "
            f"survival={result.survival_gate}, base_payoff={result.base_payoff_gate}, assumptions={result.assumption_gate}"
        )
        ttd_detail = (
            str(result.time_to_death_months)
            if result.time_to_death_months is not None
            else (">" + str(result.runway_projection_months) if result.runway_known else "unknown")
        )
        lines.append(
            f"- five metrics: Time to Death={ttd_detail}, Base={result.base_equity_multiple:.2f}x, "
            f"Required P={_pct(result.required_probability)}, Critical assumptions={result.critical_assumption_count}, "
            f"Capture={_ratio(result.common_equity_capture_ratio)}"
        )
        if result.blockers:
            lines.append("- **탈락/저해 요인:** " + "; ".join(result.blockers))
        if result.research_questions:
            lines.append("- **다음 조사:** " + "; ".join(result.research_questions))
        lines.append("")

    lines.append("## 원칙")
    lines.append("")
    lines.append("- 한 점수로 기회와 위험을 상쇄하지 않는다. 각 gate와 5개 핵심 지표를 그대로 노출한다.")
    lines.append("- `RESEARCH`와 `UNKNOWN`은 실패가 아니다. 자료가 부족하거나 refinancing/covenant가 선결조건이라는 뜻이다.")
    lines.append("- Required Probability는 성공확률 예측이 아니라 현재 가격이 요구하는 허들이다.")
    lines.append("- historical replay에서는 분석일 이후 공개된 정보를 evidence ledger에서 금지한다.")
    return "\n".join(lines) + "\n"

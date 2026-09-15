from __future__ import annotations

from .agents import build_research_tasks
from .engine import CaseResult
from .models import CaseInput


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def _num(value: float | None, suffix: str = "") -> str:
    return "—" if value is None else f"{value:,.2f}{suffix}"


def render_markdown(case: CaseInput, result: CaseResult) -> str:
    lines: list[str] = []
    lines.append(f"# {case.company_name} ({case.ticker}) — distressed equity screen")
    lines.append("")
    lines.append(f"**판정:** {result.verdict}")
    lines.append("")
    lines.append("## 사람이 먼저 볼 요약")
    lines.append("")
    ttd = "분석 구간 내 소진 없음" if result.time_to_death_months is None else f"{result.time_to_death_months}개월"
    lines.append(f"- Time to Death: **{ttd}**")
    lines.append(f"- Recovery window 생존 여부: **{result.survives_to_recovery_window}**")
    success = case.probability_view.success_scenario
    base = result.scenarios[success]
    lines.append(f"- {success} 기존주주 회수배수: **{base.equity_multiple:.2f}×**")
    lines.append(f"- 목표 CAGR {case.probability_view.target_cagr:.1%}의 target multiple: **{result.target_multiple:.2f}×**")
    lines.append(f"- Required Probability: **{_pct(result.required_probability)}**")
    low = case.probability_view.reasonable_probability_low
    high = case.probability_view.reasonable_probability_high
    lines.append(f"- 입력된 현실적 성공확률 범위: **{_pct(low)} ~ {_pct(high)}**")
    lines.append(f"- Critical assumptions: **{base.critical_assumption_count}개**")
    lines.append(f"- Common Equity Capture Ratio: **{_num(result.common_equity_capture_ratio)}**")
    if result.pre_recovery_refinancing:
        lines.append(f"- Recovery 전 refinancing: **{', '.join(result.pre_recovery_refinancing)}**")
    if result.pre_recovery_covenants:
        lines.append(f"- Recovery 전 covenant risk: **{', '.join(result.pre_recovery_covenants)}**")
    lines.append("")

    if case.debt_obligations or case.covenants:
        lines.append("## Recovery 전 자본구조 장벽")
        lines.append("")
        if case.debt_obligations:
            lines.append("| 의무 | 금액 | 월 | 현금지급 | 재융자 필요 |")
            lines.append("|---|---:|---:|---|---|")
            for item in case.debt_obligations:
                lines.append(
                    f"| {item.name} | {item.amount:,.0f} | {item.due_month} | "
                    f"{item.cash_payment_required} | {item.refinancing_required} |"
                )
            lines.append("")
        if case.covenants:
            for covenant in case.covenants:
                lines.append(
                    f"- {covenant.name}: breach month if unremedied={covenant.breach_month_if_unremedied}, "
                    f"cure_available={covenant.cure_available}"
                )
            lines.append("")

    lines.append("## 시나리오")
    lines.append("")
    lines.append("| 시나리오 | 기존주주 가치 | 현재주식 기준 가치/주 | 회수배수 | 희석 후 기존주주 지분 | 핵심가정 수 |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for scenario in case.scenarios:
        row = result.scenarios[scenario.name]
        lines.append(
            f"| {scenario.name} | {row.existing_common_value:,.0f} | {row.value_per_current_share:,.2f} | "
            f"{row.equity_multiple:.2f}× | {row.existing_holder_fraction:.1%} | {row.critical_assumption_count} |"
        )
    lines.append("")

    lines.append("## Base-case critical assumptions")
    lines.append("")
    success_input = next(s for s in case.scenarios if s.name == success)
    if success_input.critical_assumptions:
        for assumption in success_input.critical_assumptions:
            lines.append(f"- {assumption}")
    else:
        lines.append("- 명시된 핵심가정 없음")
    lines.append("")

    lines.append("## 에이전트 검증 큐")
    lines.append("")
    lines.append("에이전트는 산술을 덮어쓰지 않는다. 아래 정성 판단과 누락 검증만 수행한다.")
    lines.append("")
    for task in build_research_tasks(case):
        lines.append(f"### {task.name}")
        lines.append(f"{task.objective}")
        lines.append("")
        for item in task.required_output:
            lines.append(f"- {item}")
        lines.append("")

    lines.append("## 해석 원칙")
    lines.append("")
    lines.append("- Required Probability는 예측값이 아니라 **현재 가격이 요구하는 허들**이다.")
    lines.append("- 현실적 성공확률 범위는 별도 조사 결과로 입력하며, 엔진이 임의 생성하지 않는다.")
    lines.append("- 회사 생존과 기존 common equity 생존을 분리한다.")
    lines.append("- recovery 전에 refinancing/covenant가 있으면 성공을 자동 가정하지 않고 `REVIEW`로 남긴다.")
    lines.append("- 증자대금은 생존에 도움을 주지만 신규주식 발행으로 기존주주 몫을 희석한다.")
    lines.append("- lease/SBC/기타 준부채는 현금흐름과 EV bridge에서 이중계산하지 않는다.")
    return "\n".join(lines) + "\n"

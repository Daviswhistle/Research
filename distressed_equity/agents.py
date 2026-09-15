from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TYPE_CHECKING

from .models import CaseInput

if TYPE_CHECKING:
    from .screening import ScreeningCandidate, ScreeningResult


@dataclass(frozen=True)
class AgentTask:
    name: str
    objective: str
    guardrails: tuple[str, ...]
    required_output: tuple[str, ...]


class AgentRunner(Protocol):
    def run(self, task: AgentTask, case: CaseInput) -> str: ...


def _shared_guardrails(analysis_date: str | None = None) -> tuple[str, ...]:
    point_in_time = (
        f"Do not use information published after {analysis_date}."
        if analysis_date
        else "State the information cutoff explicitly before historical replay."
    )
    return (
        "Separate facts, estimates, inferences, and unknowns.",
        "Do not invent a point probability from qualitative evidence.",
        "Prefer primary sources and point-in-time information available at the analysis date.",
        point_in_time,
        "For each material claim record source, publication date, event/effective date, and evidence type.",
        "Actively search for disconfirming evidence.",
    )


def run_research_tasks(runner: AgentRunner, case: CaseInput) -> dict[str, str]:
    return {task.name: runner.run(task, case) for task in build_research_tasks(case)}


def build_research_tasks(case: CaseInput) -> tuple[AgentTask, ...]:
    shared = _shared_guardrails(case.analysis_date)
    return (
        AgentTask(
            name="survival_auditor",
            objective=f"Audit {case.ticker} for omitted paths that can kill common equity before recovery.",
            guardrails=shared,
            required_output=(
                "Debt maturities, covenants, springing maturities, revolver constraints",
                "Mandatory payments, senior claims, contingent liabilities",
                "Likely refinancing or dilution triggers and timing",
                "What the deterministic liquidity model is missing",
            ),
        ),
        AgentTask(
            name="impairment_classifier",
            objective="Decide which drivers look cyclical/temporary versus structural/permanent.",
            guardrails=shared,
            required_output=(
                "Driver-by-driver classification",
                "Evidence for and against reversibility",
                "Earliest observable signs that would update the classification",
            ),
        ),
        AgentTask(
            name="normalization_critic",
            objective="Attack the base-case normalization economics from the bottom up.",
            guardrails=shared,
            required_output=(
                "Volume, price, variable-cost, fixed-cost and maintenance-capex challenges",
                "Lease, SBC, tax, restructuring and working-capital double-count checks",
                "Independent assumptions that must all be true for the base case",
            ),
        ),
        AgentTask(
            name="probability_calibrator",
            objective="Bound a defensible success-probability interval without pretending to know a precise probability.",
            guardrails=shared + (
                "Return an interval only when evidence supports one; otherwise return unresolved.",
                "Compare the interval with the engine's required probability rather than optimizing toward it.",
            ),
            required_output=(
                "Lower and upper defensible probability bounds or unresolved",
                "Evidence that moves the lower bound",
                "Evidence that caps the upper bound",
                "Closest historical/base-rate analogues and why they are imperfect",
            ),
        ),
        AgentTask(
            name="model_verifier",
            objective="Verify that common-shareholder economics are modeled without accounting double counts.",
            guardrails=shared,
            required_output=(
                "Debt/lease/earn-out/TRA treatment",
                "Dilution and stock-compensation treatment",
                "Enterprise-value to existing-common bridge",
                "Any arithmetic or scenario inconsistency",
            ),
        ),
    )


def build_screening_tasks(candidate: "ScreeningCandidate", result: "ScreeningResult") -> tuple[AgentTask, ...]:
    """Build only the research tasks needed to resolve this candidate's live gates."""

    shared = _shared_guardrails(candidate.analysis_date)
    tasks: list[AgentTask] = []
    if result.survival_gate != "PASS":
        tasks.append(
            AgentTask(
                name="survival_auditor",
                objective=f"Resolve {candidate.ticker}'s Time-to-Death versus recovery window without assuming refinancing.",
                guardrails=shared,
                required_output=(
                    "Monthly liquidity bridge through recovery",
                    "Debt wall/refinancing map with lender and collateral facts",
                    "Covenant and springing-maturity map",
                    "Explicit kill conditions for existing common equity",
                ),
            )
        )
    if result.real_business_gate != "PASS" or result.assumption_gate != "PASS":
        tasks.append(
            AgentTask(
                name="business_and_normalization_auditor",
                objective=f"Determine whether {candidate.ticker}'s impairment is temporary and rebuild normalized economics bottom-up.",
                guardrails=shared,
                required_output=(
                    "Structural versus cyclical driver table",
                    "Bottom-up normalized revenue/margin/owner-cash-flow bridge",
                    "Independent critical assumptions with dependencies removed",
                    "Disconfirming evidence and falsification triggers",
                ),
            )
        )
    if result.common_equity_capture_ratio is None or result.pre_recovery_refinancing:
        tasks.append(
            AgentTask(
                name="capital_stack_auditor",
                objective=f"Reconstruct {candidate.ticker}'s point-in-time capital stack and common-equity capture.",
                guardrails=shared,
                required_output=(
                    "Debt, lease, preferred, earn-out, TRA and contingent-claim schedule",
                    "Fully diluted share count and financing-triggered dilution",
                    "Weak/base EV-to-existing-common bridge",
                    "Common-equity capture ratio with double-count checks",
                ),
            )
        )
    if result.priority != "DROP":
        tasks.append(
            AgentTask(
                name="probability_calibrator",
                objective=f"Compare {candidate.ticker}'s defensible success-probability range with its required probability.",
                guardrails=shared + (
                    "Do not return a point estimate unless a statistical model genuinely identifies one.",
                    "Use base rates as anchors, not as substitutes for company-specific causal analysis.",
                ),
                required_output=(
                    "Closest point-in-time historical/base-rate analogues",
                    "Defensible lower/upper success bounds or unresolved",
                    "Evidence that moves each bound",
                    "Required Probability comparison without optimizing assumptions to pass",
                ),
            )
        )
    return tuple(tasks)

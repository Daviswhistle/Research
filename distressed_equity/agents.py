from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import CaseInput


@dataclass(frozen=True)
class AgentTask:
    name: str
    objective: str
    guardrails: tuple[str, ...]
    required_output: tuple[str, ...]


class AgentRunner(Protocol):
    def run(self, task: AgentTask, case: CaseInput) -> str: ...


def run_research_tasks(runner: AgentRunner, case: CaseInput) -> dict[str, str]:
    return {task.name: runner.run(task, case) for task in build_research_tasks(case)}


def build_research_tasks(case: CaseInput) -> tuple[AgentTask, ...]:
    shared = (
        "Separate facts, estimates, inferences, and unknowns.",
        "Do not invent a point probability from qualitative evidence.",
        "Prefer primary sources and point-in-time information available at the analysis date.",
        "Actively search for disconfirming evidence.",
    )
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

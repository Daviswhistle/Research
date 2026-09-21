from __future__ import annotations

from research_pipeline.models import ResearchCandidate, ResearchQuery


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class PermissionGate:
    """Reject only opportunities explicitly marked as prohibited."""

    name = "permission_gate"

    def keep(self, query: ResearchQuery, candidate: ResearchCandidate) -> bool:
        del query
        status = str(candidate.payload.get("permission_status", "unknown")).lower()
        return status != "prohibited"


class MinimumEvidenceGate:
    """Require independent evidence that money actually changes hands."""

    name = "minimum_revenue_evidence"

    def __init__(self, *, minimum: int = 2) -> None:
        if minimum < 0:
            raise ValueError("minimum must be >= 0")
        self._minimum = minimum

    def keep(self, query: ResearchQuery, candidate: ResearchCandidate) -> bool:
        minimum = int(query.context.get("minimum_revenue_signals", self._minimum))
        facts = candidate.payload.get("facts", {})
        count = int(_number(facts.get("independent_revenue_signals"), 0.0))
        return count >= minimum


class ValidationBudgetGate:
    """Keep experiments that can be falsified within the current test budget."""

    name = "validation_budget"

    def __init__(
        self,
        *,
        max_cash_usd: float = 500.0,
        max_human_hours: float = 24.0,
    ) -> None:
        self._max_cash_usd = max_cash_usd
        self._max_human_hours = max_human_hours

    def keep(self, query: ResearchQuery, candidate: ResearchCandidate) -> bool:
        validation = candidate.payload.get("validation", {})
        max_cash = _number(query.context.get("max_cash_usd"), self._max_cash_usd)
        max_hours = _number(
            query.context.get("max_human_hours"), self._max_human_hours
        )
        cash = _number(validation.get("cash_usd"))
        hours = _number(validation.get("human_hours"))
        return cash <= max_cash and hours <= max_hours

from __future__ import annotations

from collections.abc import Sequence

from research_pipeline.models import ResearchCandidate, ResearchQuery


class ParetoLayerSelector:
    """Select by Pareto layers rather than hiding trade-offs in one score."""

    name = "pareto_layers"

    def __init__(
        self,
        *,
        axes: Sequence[str] = (
            "demand_strength",
            "evidence_quality",
            "automation_fit",
            "distribution_access",
            "cash_ease",
            "feedback_speed",
            "safety",
        ),
    ) -> None:
        if not axes:
            raise ValueError("at least one axis is required")
        self._axes = tuple(axes)

    def _dominates(
        self,
        left: ResearchCandidate,
        right: ResearchCandidate,
    ) -> bool:
        left_values = [left.scores.get(axis, 0.0) for axis in self._axes]
        right_values = [right.scores.get(axis, 0.0) for axis in self._axes]
        return all(a >= b for a, b in zip(left_values, right_values)) and any(
            a > b for a, b in zip(left_values, right_values)
        )

    @staticmethod
    def _tie_key(candidate: ResearchCandidate):
        return (
            candidate.scores.get("feasibility_floor", 0.0),
            candidate.scores.get("evidence_quality", 0.0),
            candidate.scores.get("cash_ease", 0.0),
            candidate.scores.get("feedback_speed", 0.0),
            candidate.candidate_id,
        )

    def select(
        self,
        query: ResearchQuery,
        candidates: Sequence[ResearchCandidate],
    ):
        limit = max(query.limit, 0)
        if limit == 0:
            return ()

        remaining = list(candidates)
        selected: list[ResearchCandidate] = []

        while remaining and len(selected) < limit:
            frontier = [
                candidate
                for candidate in remaining
                if not any(
                    other is not candidate and self._dominates(other, candidate)
                    for other in remaining
                )
            ]
            frontier.sort(key=self._tie_key, reverse=True)
            for candidate in frontier:
                if len(selected) >= limit:
                    break
                selected.append(candidate)
            frontier_ids = {id(candidate) for candidate in frontier}
            remaining = [c for c in remaining if id(c) not in frontier_ids]

        return selected

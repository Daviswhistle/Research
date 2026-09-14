from __future__ import annotations

from math import sqrt
from typing import Sequence

from .models import ResearchCandidate, ResearchQuery


def _cosine_similarity(
    left: tuple[float, ...] | None,
    right: tuple[float, ...] | None,
) -> float:
    if left is None or right is None or len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


class TopKSelector:
    name = "top_k"

    def __init__(self, *, score_key: str) -> None:
        self._score_key = score_key

    def select(
        self,
        query: ResearchQuery,
        candidates: Sequence[ResearchCandidate],
    ) -> Sequence[ResearchCandidate]:
        return sorted(
            candidates,
            key=lambda candidate: candidate.scores.get(self._score_key, float("-inf")),
            reverse=True,
        )[: max(query.limit, 0)]


class DiversitySelector:
    """Draft MMR-style selector.

    This deliberately is not a DPP implementation. It validates the pipeline's
    diversity-reranking seam without adding numerical dependencies. A later PR
    can replace it with an exact/greedy DPP implementation behind the same
    Selector protocol.
    """

    name = "diversity_mmr"

    def __init__(
        self,
        *,
        score_key: str,
        relevance_weight: float = 0.8,
    ) -> None:
        if not 0.0 <= relevance_weight <= 1.0:
            raise ValueError("relevance_weight must be between 0 and 1")
        self._score_key = score_key
        self._relevance_weight = relevance_weight

    def select(
        self,
        query: ResearchQuery,
        candidates: Sequence[ResearchCandidate],
    ) -> Sequence[ResearchCandidate]:
        limit = max(query.limit, 0)
        if limit == 0 or not candidates:
            return ()

        remaining = list(candidates)
        raw_scores = [candidate.scores.get(self._score_key, 0.0) for candidate in remaining]
        low = min(raw_scores)
        high = max(raw_scores)
        span = high - low

        def relevance(candidate: ResearchCandidate) -> float:
            value = candidate.scores.get(self._score_key, 0.0)
            if span == 0.0:
                return 1.0
            return (value - low) / span

        selected: list[ResearchCandidate] = []
        while remaining and len(selected) < limit:
            best = None
            best_value = float("-inf")
            for candidate in remaining:
                similarity = max(
                    (
                        _cosine_similarity(candidate.embedding, chosen.embedding)
                        for chosen in selected
                    ),
                    default=0.0,
                )
                value = (
                    self._relevance_weight * relevance(candidate)
                    - (1.0 - self._relevance_weight) * similarity
                )
                if best is None or value > best_value:
                    best = candidate
                    best_value = value

            assert best is not None
            selected.append(best)
            remaining.remove(best)

        return selected

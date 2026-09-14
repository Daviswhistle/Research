from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class ResearchQuery:
    """Immutable execution context shared across pipeline stages."""

    context: Mapping[str, Any] = field(default_factory=dict)
    limit: int = 10


@dataclass
class ResearchCandidate:
    """Common candidate representation used by heterogeneous research sources."""

    candidate_id: str
    title: str
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)
    tags: set[str] = field(default_factory=set)
    embedding: tuple[float, ...] | None = None

    def copy(self) -> "ResearchCandidate":
        return ResearchCandidate(
            candidate_id=self.candidate_id,
            title=self.title,
            source=self.source,
            payload=dict(self.payload),
            scores=dict(self.scores),
            tags=set(self.tags),
            embedding=self.embedding,
        )


@dataclass(frozen=True)
class StageMetric:
    stage: str
    component: str
    input_count: int
    output_count: int
    latency_ms: float
    removed_count: int = 0


@dataclass(frozen=True)
class PipelineResult:
    retrieved: tuple[ResearchCandidate, ...]
    filtered: tuple[ResearchCandidate, ...]
    selected: tuple[ResearchCandidate, ...]
    metrics: tuple[StageMetric, ...]
    warnings: tuple[str, ...] = ()

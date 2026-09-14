from __future__ import annotations

from time import perf_counter
from typing import Sequence

from .components import CandidateFilter, CandidateSource, Hydrator, Scorer, Selector
from .models import PipelineResult, ResearchCandidate, ResearchQuery, StageMetric


class ResearchPipeline:
    """Composable research-candidate pipeline.

    The first draft keeps execution synchronous and intentionally small. Its
    purpose is to stabilize stage boundaries before adding persistence,
    asynchronous hydration, experiments, or deep-research agents.
    """

    def __init__(
        self,
        *,
        sources: Sequence[CandidateSource],
        hydrators: Sequence[Hydrator] = (),
        filters: Sequence[CandidateFilter] = (),
        scorers: Sequence[Scorer] = (),
        selector: Selector,
    ) -> None:
        if not sources:
            raise ValueError("at least one candidate source is required")
        self._sources = tuple(sources)
        self._hydrators = tuple(hydrators)
        self._filters = tuple(filters)
        self._scorers = tuple(scorers)
        self._selector = selector

    def run(self, query: ResearchQuery) -> PipelineResult:
        metrics: list[StageMetric] = []
        warnings: list[str] = []

        retrieved: list[ResearchCandidate] = []
        seen_ids: set[str] = set()
        for source in self._sources:
            started = perf_counter()
            try:
                fetched = list(source.fetch(query))
            except Exception as exc:
                warnings.append(f"source {source.name} failed: {exc}")
                fetched = []

            unique: list[ResearchCandidate] = []
            for candidate in fetched:
                if candidate.candidate_id in seen_ids:
                    continue
                seen_ids.add(candidate.candidate_id)
                unique.append(candidate)

            retrieved.extend(unique)
            metrics.append(
                StageMetric(
                    stage="source",
                    component=source.name,
                    input_count=0,
                    output_count=len(unique),
                    removed_count=len(fetched) - len(unique),
                    latency_ms=(perf_counter() - started) * 1000.0,
                )
            )

        candidates = [candidate.copy() for candidate in retrieved]

        for hydrator in self._hydrators:
            started = perf_counter()
            before = len(candidates)
            candidates = list(hydrator.hydrate(query, candidates))
            metrics.append(
                StageMetric(
                    stage="hydrator",
                    component=hydrator.name,
                    input_count=before,
                    output_count=len(candidates),
                    removed_count=max(before - len(candidates), 0),
                    latency_ms=(perf_counter() - started) * 1000.0,
                )
            )

        filtered_out: list[ResearchCandidate] = []
        for candidate_filter in self._filters:
            started = perf_counter()
            before = len(candidates)
            kept: list[ResearchCandidate] = []
            removed: list[ResearchCandidate] = []
            for candidate in candidates:
                if candidate_filter.keep(query, candidate):
                    kept.append(candidate)
                else:
                    removed.append(candidate)
            candidates = kept
            filtered_out.extend(removed)
            metrics.append(
                StageMetric(
                    stage="filter",
                    component=candidate_filter.name,
                    input_count=before,
                    output_count=len(kept),
                    removed_count=len(removed),
                    latency_ms=(perf_counter() - started) * 1000.0,
                )
            )

        for scorer in self._scorers:
            started = perf_counter()
            for candidate in candidates:
                candidate.scores.update(
                    {
                        key: float(value)
                        for key, value in scorer.score(query, candidate).items()
                    }
                )
            metrics.append(
                StageMetric(
                    stage="scorer",
                    component=scorer.name,
                    input_count=len(candidates),
                    output_count=len(candidates),
                    latency_ms=(perf_counter() - started) * 1000.0,
                )
            )

        started = perf_counter()
        selected = list(self._selector.select(query, candidates))
        metrics.append(
            StageMetric(
                stage="selector",
                component=self._selector.name,
                input_count=len(candidates),
                output_count=len(selected),
                removed_count=max(len(candidates) - len(selected), 0),
                latency_ms=(perf_counter() - started) * 1000.0,
            )
        )

        return PipelineResult(
            retrieved=tuple(retrieved),
            filtered=tuple(filtered_out),
            selected=tuple(selected),
            metrics=tuple(metrics),
            warnings=tuple(warnings),
        )

from __future__ import annotations

from research_pipeline import (
    DiversitySelector,
    ResearchCandidate,
    ResearchPipeline,
    ResearchQuery,
    TopKSelector,
)


class StaticSource:
    name = "static"

    def __init__(self, candidates):
        self._candidates = candidates

    def fetch(self, query):
        return self._candidates


class PositiveScoreFilter:
    name = "positive_score"

    def keep(self, query, candidate):
        return candidate.payload["raw_score"] > 0


class RawScore:
    name = "raw_score"

    def score(self, query, candidate):
        return {"priority": candidate.payload["raw_score"]}


def candidate(candidate_id, score, embedding=None):
    return ResearchCandidate(
        candidate_id=candidate_id,
        title=candidate_id,
        source="test",
        payload={"raw_score": score},
        embedding=embedding,
    )


def test_pipeline_deduplicates_filters_scores_and_selects():
    pipeline = ResearchPipeline(
        sources=[
            StaticSource(
                [
                    candidate("a", 10),
                    candidate("a", 10),
                    candidate("b", -1),
                    candidate("c", 5),
                ]
            )
        ],
        filters=[PositiveScoreFilter()],
        scorers=[RawScore()],
        selector=TopKSelector(score_key="priority"),
    )

    result = pipeline.run(ResearchQuery(limit=1))

    assert [item.candidate_id for item in result.retrieved] == ["a", "b", "c"]
    assert [item.candidate_id for item in result.filtered] == ["b"]
    assert [item.candidate_id for item in result.selected] == ["a"]
    assert any(metric.stage == "filter" and metric.removed_count == 1 for metric in result.metrics)


def test_source_failure_is_visible_without_blocking_other_sources():
    class BrokenSource:
        name = "broken"

        def fetch(self, query):
            raise RuntimeError("boom")

    pipeline = ResearchPipeline(
        sources=[BrokenSource(), StaticSource([candidate("a", 1)])],
        scorers=[RawScore()],
        selector=TopKSelector(score_key="priority"),
    )

    result = pipeline.run(ResearchQuery(limit=1))

    assert [item.candidate_id for item in result.selected] == ["a"]
    assert result.warnings == ("source broken failed: boom",)


def test_diversity_selector_can_trade_small_relevance_for_novelty():
    candidates = [
        ResearchCandidate(
            candidate_id="a",
            title="a",
            source="test",
            scores={"priority": 100},
            embedding=(1.0, 0.0),
        ),
        ResearchCandidate(
            candidate_id="b",
            title="b",
            source="test",
            scores={"priority": 99},
            embedding=(0.99, 0.01),
        ),
        ResearchCandidate(
            candidate_id="c",
            title="c",
            source="test",
            scores={"priority": 90},
            embedding=(0.0, 1.0),
        ),
    ]

    selector = DiversitySelector(score_key="priority", relevance_weight=0.4)
    selected = selector.select(ResearchQuery(limit=2), candidates)

    assert [item.candidate_id for item in selected] == ["a", "c"]

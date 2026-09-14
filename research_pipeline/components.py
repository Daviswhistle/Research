from __future__ import annotations

from typing import Mapping, Protocol, Sequence

from .models import ResearchCandidate, ResearchQuery


class CandidateSource(Protocol):
    name: str

    def fetch(self, query: ResearchQuery) -> Sequence[ResearchCandidate]:
        ...


class Hydrator(Protocol):
    name: str

    def hydrate(
        self,
        query: ResearchQuery,
        candidates: Sequence[ResearchCandidate],
    ) -> Sequence[ResearchCandidate]:
        ...


class CandidateFilter(Protocol):
    name: str

    def keep(self, query: ResearchQuery, candidate: ResearchCandidate) -> bool:
        ...


class Scorer(Protocol):
    name: str

    def score(
        self,
        query: ResearchQuery,
        candidate: ResearchCandidate,
    ) -> Mapping[str, float]:
        ...


class Selector(Protocol):
    name: str

    def select(
        self,
        query: ResearchQuery,
        candidates: Sequence[ResearchCandidate],
    ) -> Sequence[ResearchCandidate]:
        ...

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from .dart_client import DartClient
from .enrichment import DisclosureEnricher
from .models import EventCategory, RelatedEvent, ScoredCandidate
from .scoring import score_candidate
from .store import ScannerStore
from .taxonomy import classify_report


@dataclass(frozen=True)
class ScanResult:
    candidates: tuple[ScoredCandidate, ...]
    total_disclosures: int
    classified_disclosures: int
    skipped_seen: int
    skipped_unlisted: int


def _minus_days(ymd: str, days: int) -> str:
    parsed = datetime.strptime(str(ymd), "%Y%m%d")
    return (parsed - timedelta(days=int(days))).strftime("%Y%m%d")


class TransformationPipeline:
    def __init__(
        self,
        *,
        client: DartClient,
        store: ScannerStore | None,
        include_documents: bool = False,
        cluster_lookback_days: int = 30,
    ) -> None:
        self._client = client
        self._store = store
        self._enricher = DisclosureEnricher(
            client=client,
            include_documents=include_documents,
        )
        self._cluster_lookback_days = max(int(cluster_lookback_days), 0)

    def scan(
        self,
        *,
        start_date: str,
        end_date: str,
        corp_classes: Iterable[str] = ("Y", "K"),
        public_types: Iterable[str] = ("B", "C", "E", "I"),
        include_seen: bool = False,
        persist: bool = True,
    ) -> ScanResult:
        disclosures = self._client.list_disclosures(
            start_date=start_date,
            end_date=end_date,
            corp_classes=corp_classes,
            public_types=public_types,
        )
        candidates: list[ScoredCandidate] = []
        in_memory_events: dict[str, list[RelatedEvent]] = {}
        skipped_seen = 0
        skipped_unlisted = 0
        classified_count = 0

        for disclosure in disclosures:
            if not disclosure.stock_code:
                skipped_unlisted += 1
                continue

            classification = classify_report(disclosure.report_nm)
            if classification.category == EventCategory.OTHER:
                continue
            classified_count += 1

            if (
                self._store is not None
                and not include_seen
                and self._store.seen_receipt(disclosure.rcept_no)
            ):
                skipped_seen += 1
                continue

            related_by_receipt: dict[str, RelatedEvent] = {}
            cluster_start = _minus_days(disclosure.rcept_dt, self._cluster_lookback_days)
            if self._store is not None:
                for event in self._store.recent_events(
                    corp_code=disclosure.corp_code,
                    start_date=cluster_start,
                    end_date=disclosure.rcept_dt,
                    exclude_receipt=disclosure.rcept_no,
                ):
                    related_by_receipt[event.rcept_no] = event
            for event in in_memory_events.get(disclosure.corp_code, []):
                if cluster_start <= event.rcept_dt <= disclosure.rcept_dt:
                    related_by_receipt[event.rcept_no] = event

            enrichment = self._enricher.enrich(
                disclosure=disclosure,
                classification=classification,
            )
            candidate = score_candidate(
                disclosure=disclosure,
                classification=classification,
                facts=enrichment.facts,
                related_events=tuple(related_by_receipt.values()),
                warnings=enrichment.warnings,
            )
            candidates.append(candidate)

            event = RelatedEvent(
                rcept_no=disclosure.rcept_no,
                rcept_dt=disclosure.rcept_dt,
                category=classification.category,
                subtype=classification.subtype,
                facts=dict(candidate.facts),
            )
            in_memory_events.setdefault(disclosure.corp_code, []).append(event)

            if self._store is not None and persist:
                self._store.save_candidate(candidate)

        candidates.sort(
            key=lambda item: (
                item.attention_score,
                item.financing_risk_score,
                item.disclosure.rcept_dt,
                item.disclosure.rcept_no,
            ),
            reverse=True,
        )
        return ScanResult(
            candidates=tuple(candidates),
            total_disclosures=len(disclosures),
            classified_disclosures=classified_count,
            skipped_seen=skipped_seen,
            skipped_unlisted=skipped_unlisted,
        )

from __future__ import annotations

from typing import Any

from transformation_scanner.pipeline import TransformationPipeline

from ..models import ResearchCandidate, ResearchQuery


class TransformationScannerSource:
    """Expose the existing DART scanner as a generic candidate source."""

    name = "transformation_scanner"

    def __init__(self, pipeline: TransformationPipeline) -> None:
        self._pipeline = pipeline

    def fetch(self, query: ResearchQuery) -> list[ResearchCandidate]:
        context = dict(query.context)
        start_date = _required(context, "start_date")
        end_date = _required(context, "end_date")

        result = self._pipeline.scan(
            start_date=start_date,
            end_date=end_date,
            corp_classes=context.get("corp_classes", ("Y", "K")),
            public_types=context.get("public_types", ("B", "C", "E", "I")),
            include_seen=bool(context.get("include_seen", False)),
            persist=bool(context.get("persist", True)),
        )

        converted: list[ResearchCandidate] = []
        for item in result.candidates:
            converted.append(
                ResearchCandidate(
                    candidate_id=f"dart:{item.disclosure.rcept_no}",
                    title=f"{item.disclosure.corp_name} — {item.disclosure.report_nm}",
                    source=self.name,
                    payload=item.to_dict(),
                    scores={
                        "attention_score": item.attention_score,
                        "financing_risk_score": item.financing_risk_score,
                    },
                    tags={
                        item.classification.category.value,
                        item.band.value,
                        *item.cluster_tags,
                    },
                )
            )
        return converted


def _required(context: dict[str, Any], key: str) -> str:
    value = str(context.get(key) or "").strip()
    if not value:
        raise ValueError(f"query.context[{key!r}] is required")
    return value

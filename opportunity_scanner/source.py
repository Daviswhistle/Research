from __future__ import annotations

import json
from pathlib import Path

from research_pipeline.models import ResearchCandidate, ResearchQuery


class JsonlOpportunitySource:
    """Load explicitly sourced opportunity hypotheses from JSONL."""

    name = "opportunity_jsonl"

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def fetch(self, query: ResearchQuery):
        del query
        candidates: list[ResearchCandidate] = []
        with self._path.open("r", encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, start=1):
                raw = raw.strip()
                if not raw or raw.startswith("#"):
                    continue
                record = json.loads(raw)
                candidate_id = str(record.get("id", "")).strip()
                title = str(record.get("title", "")).strip()
                if not candidate_id or not title:
                    raise ValueError(
                        f"{self._path}:{line_number}: id and title are required"
                    )
                candidates.append(
                    ResearchCandidate(
                        candidate_id=candidate_id,
                        title=title,
                        source=self.name,
                        payload=record,
                        tags={str(record.get("mechanism", "unknown"))},
                    )
                )
        return candidates

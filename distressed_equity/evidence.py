from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class EvidenceRecord:
    claim: str
    source: str
    published_on: date
    evidence_type: str
    event_on: date | None = None
    locator: str | None = None
    notes: str | None = None


def validate_point_in_time(records: tuple[EvidenceRecord, ...], analysis_date: date) -> tuple[str, ...]:
    """Return violations that would contaminate a historical point-in-time replay."""

    violations: list[str] = []
    for record in records:
        if record.published_on > analysis_date:
            violations.append(
                f"future information: {record.claim!r} was published {record.published_on.isoformat()} "
                f"after analysis date {analysis_date.isoformat()}"
            )
    return tuple(violations)

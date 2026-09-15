from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from .sec_debt import CapitalStackPacket, CapitalStackSnippet


@dataclass(frozen=True)
class CapitalStackChangeCandidate:
    from_accession: str
    to_accession: str
    from_filing_date: date
    to_filing_date: date
    category: str
    change_types: tuple[str, ...]
    added_amounts: tuple[float, ...] = ()
    removed_amounts: tuple[float, ...] = ()
    added_years: tuple[int, ...] = ()
    removed_years: tuple[int, ...] = ()
    added_percentages: tuple[float, ...] = ()
    removed_percentages: tuple[float, ...] = ()
    from_examples: tuple[str, ...] = ()
    to_examples: tuple[str, ...] = ()
    confidence: str = "candidate"


@dataclass(frozen=True)
class CapitalStackDiffReport:
    company_name: str
    analysis_date: date
    comparisons: tuple[dict[str, Any], ...]
    changes: tuple[CapitalStackChangeCandidate, ...]
    warnings: tuple[str, ...]


def _snippet_signals(snippets: list[CapitalStackSnippet]) -> tuple[set[float], set[int], set[float]]:
    amounts = {candidate.amount for snippet in snippets for candidate in snippet.amount_candidates}
    years = {year for snippet in snippets for year in snippet.year_candidates}
    percentages = {value for snippet in snippets for value in snippet.percentage_candidates}
    return amounts, years, percentages


def _examples(snippets: list[CapitalStackSnippet], limit: int = 2) -> tuple[str, ...]:
    return tuple(snippet.text[:500] for snippet in snippets[:limit])


def diff_capital_stack_packet(packet: CapitalStackPacket) -> CapitalStackDiffReport:
    """Compare adjacent point-in-time filings without asserting an amendment occurred.

    Regex signals are noisy. A change candidate only tells the auditor where the
    filing language/numbers moved; the source filing still controls.
    """

    filing_meta = {
        str(item.get("accession_number")): item
        for item in packet.source_filings
        if item.get("accession_number") and item.get("filing_date")
    }
    by_accession: dict[str, list[CapitalStackSnippet]] = {}
    for snippet in packet.snippets:
        by_accession.setdefault(snippet.accession_number, []).append(snippet)

    ordered = sorted(
        filing_meta.items(),
        key=lambda item: (date.fromisoformat(str(item[1]["filing_date"])[:10]), item[0]),
    )
    changes: list[CapitalStackChangeCandidate] = []
    comparisons: list[dict[str, Any]] = []

    for (from_acc, from_meta), (to_acc, to_meta) in zip(ordered, ordered[1:]):
        from_date = date.fromisoformat(str(from_meta["filing_date"])[:10])
        to_date = date.fromisoformat(str(to_meta["filing_date"])[:10])
        comparisons.append(
            {
                "from_accession": from_acc,
                "to_accession": to_acc,
                "from_filing_date": from_date.isoformat(),
                "to_filing_date": to_date.isoformat(),
            }
        )
        from_snippets = by_accession.get(from_acc, [])
        to_snippets = by_accession.get(to_acc, [])
        categories = sorted({s.category for s in from_snippets} | {s.category for s in to_snippets})
        for category in categories:
            left = [s for s in from_snippets if s.category == category]
            right = [s for s in to_snippets if s.category == category]
            left_amounts, left_years, left_pct = _snippet_signals(left)
            right_amounts, right_years, right_pct = _snippet_signals(right)
            added_amounts = tuple(sorted(right_amounts - left_amounts))
            removed_amounts = tuple(sorted(left_amounts - right_amounts))
            added_years = tuple(sorted(right_years - left_years))
            removed_years = tuple(sorted(left_years - right_years))
            added_pct = tuple(sorted(right_pct - left_pct))
            removed_pct = tuple(sorted(left_pct - right_pct))
            change_types: list[str] = []
            if added_amounts or removed_amounts:
                change_types.append("amount_signal_changed")
            if added_years or removed_years:
                change_types.append("maturity_or_date_signal_changed")
            if added_pct or removed_pct:
                change_types.append("rate_or_ratio_signal_changed")
            if bool(left) != bool(right):
                change_types.append("category_appeared_or_disappeared")
            if not change_types:
                continue
            changes.append(
                CapitalStackChangeCandidate(
                    from_accession=from_acc,
                    to_accession=to_acc,
                    from_filing_date=from_date,
                    to_filing_date=to_date,
                    category=category,
                    change_types=tuple(change_types),
                    added_amounts=added_amounts,
                    removed_amounts=removed_amounts,
                    added_years=added_years,
                    removed_years=removed_years,
                    added_percentages=added_pct,
                    removed_percentages=removed_pct,
                    from_examples=_examples(left),
                    to_examples=_examples(right),
                )
            )

    return CapitalStackDiffReport(
        company_name=packet.company_name,
        analysis_date=packet.analysis_date,
        comparisons=tuple(comparisons),
        changes=tuple(changes),
        warnings=(
            "All changes are navigation candidates, not confirmed amendments.",
            "Dollar/year/percentage signals can belong to unrelated instruments in the same filing section.",
            "Confirm each material change against the source filing or credit-agreement exhibit before modeling it.",
        ),
    )


def capital_stack_diff_to_dict(report: CapitalStackDiffReport) -> dict[str, Any]:
    def convert(value: Any) -> Any:
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, tuple):
            return [convert(item) for item in value]
        if isinstance(value, list):
            return [convert(item) for item in value]
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        return value

    return convert(asdict(report))

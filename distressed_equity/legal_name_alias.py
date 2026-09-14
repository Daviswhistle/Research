from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Iterable

from .contract_parties import normalize_party_name
from .sec import SEC_DATA_BASE, SecClient, normalize_cik


@dataclass(frozen=True)
class LegalNameRecord:
    name: str
    normalized_name: str
    valid_from: date | None
    valid_to: date | None
    source_kind: str
    source: str
    evidence_on: date | None = None


@dataclass(frozen=True)
class LegalNameTransition:
    from_name: str
    to_name: str
    effective_on: date
    source_kind: str
    source: str


@dataclass(frozen=True)
class LegalNameAliasGraph:
    cik: str
    analysis_date: date
    canonical_name_as_of: str | None
    records: tuple[LegalNameRecord, ...]
    transitions: tuple[LegalNameTransition, ...]
    warnings: tuple[str, ...]

    @property
    def alias_names(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.normalized_name for item in self.records if item.normalized_name))


def _parse_sec_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def build_legal_name_alias_graph_from_submissions(
    payload: dict[str, Any],
    *,
    cik: str | int,
    analysis_date: date,
) -> LegalNameAliasGraph:
    """Build a point-in-time legal-name graph from SEC submissions metadata.

    SEC submissions metadata is read today, so rename events after the historical
    cutoff are never exposed to the historical graph. An active former-name
    interval is treated as the legal name at the cutoff. If metadata shows only
    future name-history relative to the cutoff, the canonical name is left unknown
    rather than backfilling today's name.
    """

    cik10 = normalize_cik(cik)
    source = f"{SEC_DATA_BASE}/submissions/CIK{cik10}.json"
    current_name = str(payload.get("name") or "").strip() or None
    warnings: list[str] = []
    raw_former = payload.get("formerNames") or []
    parsed: list[tuple[str, date | None, date | None]] = []
    if not isinstance(raw_former, list):
        warnings.append("SEC submissions formerNames was not a list")
        raw_former = []

    for row in raw_former:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        valid_from = _parse_sec_date(row.get("from"))
        valid_to = _parse_sec_date(row.get("to"))
        if valid_from and valid_to and valid_to < valid_from:
            warnings.append(f"former-name interval reversed for {name}")
            continue
        parsed.append((name, valid_from, valid_to))

    parsed.sort(key=lambda item: (item[1] or date.min, item[2] or date.max, item[0]))

    active_former = [
        item for item in parsed
        if (item[1] is None or item[1] <= analysis_date)
        and (item[2] is None or analysis_date < item[2])
    ]
    future_history_exists = any(
        (start is not None and start > analysis_date)
        or (end is not None and end > analysis_date)
        for _name, start, end in parsed
    )
    if active_former:
        canonical_name = max(active_former, key=lambda item: item[1] or date.min)[0]
    elif future_history_exists:
        canonical_name = None
        warnings.append(
            "current SEC name may post-date the analysis cutoff and was withheld because future name-history exists"
        )
    else:
        canonical_name = current_name

    records: list[LegalNameRecord] = []
    seen_records: set[tuple[str, date | None, date | None]] = set()
    for name, valid_from, valid_to in parsed:
        if valid_from and valid_from > analysis_date:
            continue
        visible_to = valid_to if valid_to is not None and valid_to <= analysis_date else None
        key = (normalize_party_name(name), valid_from, visible_to)
        if key in seen_records:
            continue
        seen_records.add(key)
        records.append(
            LegalNameRecord(
                name=name,
                normalized_name=key[0],
                valid_from=valid_from,
                valid_to=visible_to,
                source_kind="sec_submissions_former_name",
                source=source,
                evidence_on=visible_to,
            )
        )

    if canonical_name and current_name and normalize_party_name(canonical_name) == normalize_party_name(current_name):
        completed = [end for _name, _start, end in parsed if end is not None and end <= analysis_date]
        current_from = max(completed) if completed else None
        key = (normalize_party_name(current_name), current_from, None)
        if key not in seen_records:
            records.append(
                LegalNameRecord(
                    name=current_name,
                    normalized_name=key[0],
                    valid_from=current_from,
                    valid_to=None,
                    source_kind="sec_submissions_current_name",
                    source=source,
                    evidence_on=current_from,
                )
            )

    transitions: list[LegalNameTransition] = []
    for index, (name, _start, end) in enumerate(parsed):
        if end is None or end > analysis_date:
            continue
        successor: str | None = None
        for next_name, next_start, _next_end in parsed[index + 1 :]:
            if next_start is not None and next_start <= analysis_date and next_start >= end:
                successor = next_name
                break
        if successor is None and current_name and canonical_name and normalize_party_name(canonical_name) == normalize_party_name(current_name):
            successor = current_name
        if not successor or normalize_party_name(successor) == normalize_party_name(name):
            continue
        transitions.append(
            LegalNameTransition(
                from_name=name,
                to_name=successor,
                effective_on=end,
                source_kind="sec_submissions_former_name",
                source=source,
            )
        )

    if current_name and canonical_name and normalize_party_name(current_name) != normalize_party_name(canonical_name):
        warnings.append(
            "current SEC name post-dates the analysis cutoff and was withheld from the alias set"
        )

    return LegalNameAliasGraph(
        cik=cik10,
        analysis_date=analysis_date,
        canonical_name_as_of=canonical_name,
        records=tuple(records),
        transitions=tuple(transitions),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def build_legal_name_alias_graph(
    client: SecClient,
    *,
    cik: str | int,
    analysis_date: date,
) -> LegalNameAliasGraph:
    return build_legal_name_alias_graph_from_submissions(
        client.submissions(cik),
        cik=cik,
        analysis_date=analysis_date,
    )


def build_legal_name_alias_graphs(
    client: SecClient,
    *,
    ciks: Iterable[str | int],
    analysis_date: date,
    existing: Iterable[LegalNameAliasGraph] = (),
) -> tuple[tuple[LegalNameAliasGraph, ...], tuple[str, ...]]:
    """Collect alias graphs only for already-identified CIKs.

    This helper never resolves an entity name to a CIK. Callers must supply CIKs
    obtained from deterministic SEC identity evidence such as the root filing CIK
    or explicit cross-CIK SEC locators. Lookup failures become warnings so an
    auxiliary alias lookup cannot invalidate otherwise source-backed navigation.
    """

    known = {graph.cik: graph for graph in existing}
    warnings: list[str] = []
    for value in sorted({normalize_cik(cik) for cik in ciks}):
        if value in known:
            continue
        try:
            graph = build_legal_name_alias_graph(
                client,
                cik=value,
                analysis_date=analysis_date,
            )
        except Exception as exc:
            warnings.append(f"failed legal-name alias lookup for CIK {value}: {exc}")
            continue
        known[value] = graph
        warnings.extend(f"CIK {value}: {item}" for item in graph.warnings)
    return tuple(known[key] for key in sorted(known)), tuple(dict.fromkeys(warnings))


def legal_name_alias_graph_to_dict(graph: LegalNameAliasGraph) -> dict[str, Any]:
    def convert(value: Any) -> Any:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, tuple):
            return [convert(item) for item in value]
        if isinstance(value, list):
            return [convert(item) for item in value]
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        if hasattr(value, "__dataclass_fields__"):
            return convert(asdict(value))
        return value

    return convert(graph)


def alias_groups_from_graphs(graphs: Iterable[LegalNameAliasGraph]) -> tuple[frozenset[str], ...]:
    groups: list[frozenset[str]] = []
    for graph in graphs:
        group = frozenset(graph.alias_names)
        if len(group) > 1 and group not in groups:
            groups.append(group)
    return tuple(groups)

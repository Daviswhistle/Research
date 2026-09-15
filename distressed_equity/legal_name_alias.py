from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from typing import Any, Iterable

from .contract_parties import normalize_party_name
from .sec import SEC_DATA_BASE, SecClient, normalize_cik


HEADER_FORMS = (
    "10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A",
    "S-1", "S-1/A", "S-3", "S-3/A", "S-4", "S-4/A",
)
DEFAULT_HEADER_RECENT_LIMIT = 3
DEFAULT_HEADER_MAX_FILINGS = 12


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


def _evidence_aliases(evidence) -> set[str]:
    aliases: set[str] = set()
    for item in evidence:
        if item.normalized_current_name:
            aliases.add(item.normalized_current_name)
        aliases.update(former.normalized_name for former in item.former_names if former.normalized_name)
    return aliases


def _evidence_current_names(evidence) -> set[str]:
    return {item.normalized_current_name for item in evidence if item.normalized_current_name}


def _temporal_probe_indices(total: int, recent_count: int) -> tuple[int, ...]:
    """Return broad historical anchors: oldest first, then recursive midpoints."""
    if total <= recent_count:
        return ()
    left = max(recent_count - 1, 0)
    right = total - 1
    order: list[int] = [right]
    queue: list[tuple[int, int]] = [(left, right)]
    seen = {right}
    while queue:
        lo, hi = queue.pop(0)
        if hi - lo <= 1:
            continue
        mid = (lo + hi) // 2
        if mid >= recent_count and mid not in seen:
            order.append(mid)
            seen.add(mid)
        queue.append((lo, mid))
        queue.append((mid, hi))
    return tuple(order)


def _recent_headers_sufficient(
    base: LegalNameAliasGraph,
    evidence,
    *,
    older_exists: bool,
    required_aliases: set[str],
) -> bool:
    aliases = _evidence_aliases(evidence)
    combined = aliases | set(base.alias_names)
    if required_aliases:
        return required_aliases.issubset(combined)
    if not older_exists:
        return True
    if not evidence:
        return False
    current_names = _evidence_current_names(evidence)
    if len(current_names) > 1:
        return False
    base_aliases = set(base.alias_names)
    header_only = aliases - base_aliases
    if header_only:
        return False
    # If submissions already carries a real rename history and recent headers
    # corroborate every known alias, deeper requests add little identity value.
    if len(base_aliases) > 1 and base_aliases.issubset(aliases):
        return True
    # A current-name-only submissions graph cannot prove that a much older alias
    # never existed. Probe at least the oldest available filing before stopping.
    return False


def _adaptive_complete_header_evidence(
    client: SecClient,
    *,
    cik10: str,
    analysis_date: date,
    base: LegalNameAliasGraph,
    filings,
    required_aliases: set[str],
    recent_limit: int,
    max_filings: int,
    deep_scan: bool,
):
    from .legal_name_header import fetch_complete_submission_name_evidence

    candidates = tuple(
        sorted(
            (
                filing for filing in filings
                if normalize_cik(filing.cik) == cik10 and filing.filing_date <= analysis_date
            ),
            key=lambda filing: (filing.filing_date, filing.accession_number),
            reverse=True,
        )
    )
    if not candidates or max_filings == 0:
        return (), (), "no_header_scan", len(candidates)

    effective_recent = min(recent_limit, max_filings, len(candidates))
    evidence = []
    warnings: list[str] = []
    attempted: set[str] = set()

    def fetch(filing) -> None:
        if filing.accession_number in attempted:
            return
        attempted.add(filing.accession_number)
        try:
            evidence.append(
                fetch_complete_submission_name_evidence(
                    client,
                    filing=filing,
                    target_cik=cik10,
                )
            )
        except Exception as exc:
            warnings.append(
                f"failed complete-submission legal-name header lookup for {filing.accession_number}: {exc}"
            )

    for filing in candidates[:effective_recent]:
        fetch(filing)

    combined = _evidence_aliases(evidence) | set(base.alias_names)
    if required_aliases and required_aliases.issubset(combined):
        return tuple(evidence), tuple(warnings), "target_alias_satisfied_recent", len(candidates)

    older_exists = len(candidates) > effective_recent
    if not deep_scan or _recent_headers_sufficient(
        base,
        evidence,
        older_exists=older_exists,
        required_aliases=required_aliases,
    ):
        status = "recent_only_sufficient" if deep_scan else "recent_only_deep_scan_disabled"
        return tuple(evidence), tuple(warnings), status, len(candidates)

    recent_aliases = _evidence_aliases(evidence) | set(base.alias_names)
    recent_currents = _evidence_current_names(evidence)
    probes = _temporal_probe_indices(len(candidates), effective_recent)
    if not probes:
        return tuple(evidence), tuple(warnings), "recent_only_complete", len(candidates)

    # First fetch the oldest available filing. For an untargeted scan, equal
    # endpoint identity with no new aliases is a conservative low-cost stop.
    fetch(candidates[probes[0]])
    combined = _evidence_aliases(evidence) | set(base.alias_names)
    if required_aliases and required_aliases.issubset(combined):
        return tuple(evidence), tuple(warnings), "target_alias_found_deep", len(candidates)
    oldest_evidence = next(
        (item for item in evidence if item.accession_number == candidates[probes[0]].accession_number),
        None,
    )
    if not required_aliases and oldest_evidence is not None:
        oldest_current = oldest_evidence.normalized_current_name
        no_new_alias = combined.issubset(recent_aliases)
        same_endpoint = bool(oldest_current and recent_currents and oldest_current in recent_currents)
        if no_new_alias and same_endpoint:
            return tuple(evidence), tuple(warnings), "deep_probe_stable_endpoints", len(candidates)

    # Evidence changed, or a specifically required historical alias remains
    # missing. Spread the remaining request budget over the whole filing history
    # using recursive temporal midpoints rather than walking quarter-by-quarter.
    for index in probes[1:]:
        if len(attempted) >= max_filings:
            break
        fetch(candidates[index])
        combined = _evidence_aliases(evidence) | set(base.alias_names)
        if required_aliases and required_aliases.issubset(combined):
            return tuple(evidence), tuple(warnings), "target_alias_found_deep", len(candidates)

    complete = len(attempted) >= len(candidates)
    if required_aliases and not required_aliases.issubset(
        _evidence_aliases(evidence) | set(base.alias_names)
    ):
        missing = ", ".join(sorted(required_aliases - (_evidence_aliases(evidence) | set(base.alias_names))))
        warnings.append(
            f"required historical legal-name aliases were not confirmed by the bounded header scan: {missing}"
        )
    if complete:
        return tuple(evidence), tuple(warnings), "deep_scan_complete", len(candidates)
    warnings.append(
        f"historical legal-name header scan reached max_header_filings={max_filings}; older/intermediate alias history may remain incomplete"
    )
    return tuple(evidence), tuple(warnings), "deep_scan_bounded", len(candidates)


def build_legal_name_alias_graph(
    client: SecClient,
    *,
    cik: str | int,
    analysis_date: date,
    required_aliases: Iterable[str] = (),
    header_recent_limit: int = DEFAULT_HEADER_RECENT_LIMIT,
    header_max_filings: int = DEFAULT_HEADER_MAX_FILINGS,
    deep_scan_headers: bool = True,
):
    """Build submissions metadata and reconcile it with historical SEC headers.

    The latest headers are fetched first. If they do not sufficiently corroborate
    known submissions history—or a caller requires a specific historical alias
    that is still unconfirmed—the builder performs a bounded temporal deep scan.
    The deep scan probes the oldest filing first and then recursive temporal
    midpoints, avoiding an expensive quarter-by-quarter walk through long histories.
    """

    if header_recent_limit < 0:
        raise ValueError("header_recent_limit cannot be negative")
    if header_max_filings < 0:
        raise ValueError("header_max_filings cannot be negative")

    cik10 = normalize_cik(cik)
    base = build_legal_name_alias_graph_from_submissions(
        client.submissions(cik10),
        cik=cik10,
        analysis_date=analysis_date,
    )
    try:
        filings = client.filings_as_of(
            cik10,
            analysis_date,
            forms=HEADER_FORMS,
        )
    except Exception as exc:
        return replace(
            base,
            warnings=tuple(dict.fromkeys(base.warnings + (f"failed historical filing lookup for legal-name headers: {exc}",))),
        )

    from .legal_name_reconcile import reconcile_legal_name_sources

    required = {
        normalized
        for value in required_aliases
        if (normalized := normalize_party_name(str(value)))
    }
    evidence, lookup_warnings, scan_status, considered = _adaptive_complete_header_evidence(
        client,
        cik10=cik10,
        analysis_date=analysis_date,
        base=base,
        filings=filings,
        required_aliases=required,
        recent_limit=header_recent_limit,
        max_filings=header_max_filings,
        deep_scan=deep_scan_headers,
    )
    reconciled = reconcile_legal_name_sources(base, evidence)
    warnings = tuple(dict.fromkeys(list(reconciled.warnings) + list(lookup_warnings)))
    return replace(
        reconciled,
        warnings=warnings,
        header_scan_status=scan_status,
        header_scan_filings_considered=considered,
        header_scan_filings_fetched=len(evidence),
        header_scan_oldest_filing_on=min((item.filing_date for item in evidence), default=None),
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

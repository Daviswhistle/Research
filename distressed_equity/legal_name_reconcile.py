from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Iterable

from .contract_parties import normalize_party_name
from .legal_name_alias import LegalNameAliasGraph, LegalNameRecord, LegalNameTransition, build_legal_name_alias_graph_from_submissions
from .legal_name_header import CompleteSubmissionNameEvidence, fetch_complete_submission_name_evidence
from .sec import SecClient, SecFiling, normalize_cik


HEADER_FORMS = ("10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A", "S-1", "S-1/A", "S-3", "S-3/A", "S-4", "S-4/A")


@dataclass(frozen=True)
class LegalNameSourceComparison:
    name: str
    normalized_name: str
    submissions_boundaries: tuple[date, ...]
    header_boundaries: tuple[date, ...]
    status: str


@dataclass(frozen=True)
class ReconciledLegalNameAliasGraph:
    cik: str
    analysis_date: date
    canonical_name_as_of: str | None
    canonical_status: str
    records: tuple[LegalNameRecord, ...]
    transitions: tuple[LegalNameTransition, ...]
    comparisons: tuple[LegalNameSourceComparison, ...]
    header_evidence: tuple[CompleteSubmissionNameEvidence, ...]
    warnings: tuple[str, ...]
    header_scan_status: str = "unspecified"
    header_scan_filings_considered: int = 0
    header_scan_filings_fetched: int = 0
    header_scan_oldest_filing_on: date | None = None

    @property
    def alias_names(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.normalized_name for item in self.records if item.normalized_name))


def _header_semantics(evidence: tuple[CompleteSubmissionNameEvidence, ...]):
    records: dict[tuple, LegalNameRecord] = {}
    transitions: dict[tuple, LegalNameTransition] = {}
    boundaries: dict[str, set[date]] = {}
    names: dict[str, str] = {}
    for item in evidence:
        dated = sorted((x for x in item.former_names if x.change_on), key=lambda x: x.change_on or date.min)
        previous = None
        for index, former in enumerate(dated):
            boundary = former.change_on
            assert boundary is not None
            names.setdefault(former.normalized_name, former.name)
            boundaries.setdefault(former.normalized_name, set()).add(boundary)
            record = LegalNameRecord(former.name, former.normalized_name, previous, boundary, "sec_complete_submission_header_former_name", item.source_url, item.filing_date)
            records[(record.normalized_name, record.valid_from, record.valid_to, record.source_kind)] = record
            successor = dated[index + 1].name if index + 1 < len(dated) else item.current_name
            if successor and normalize_party_name(successor) != former.normalized_name:
                transition = LegalNameTransition(former.name, successor, boundary, "sec_complete_submission_header", item.source_url)
                transitions[(former.normalized_name, normalize_party_name(successor), boundary)] = transition
            previous = boundary
        for former in item.former_names:
            names.setdefault(former.normalized_name, former.name)
            if former.change_on is None:
                record = LegalNameRecord(former.name, former.normalized_name, None, None, "sec_complete_submission_header_former_name_without_boundary", item.source_url, item.filing_date)
                records[(record.normalized_name, None, None, record.source_kind)] = record
        if item.current_name and item.normalized_current_name:
            names.setdefault(item.normalized_current_name, item.current_name)
            current_from = max((x.change_on for x in dated if x.change_on), default=None)
            record = LegalNameRecord(item.current_name, item.normalized_current_name, current_from, None, "sec_complete_submission_header_current_name", item.source_url, item.filing_date)
            records[(record.normalized_name, record.valid_from, None, record.source_kind)] = record
    return records, transitions, boundaries, names


def _submissions_maps(graph: LegalNameAliasGraph):
    boundaries: dict[str, set[date]] = {}
    names: dict[str, str] = {}
    for record in graph.records:
        names.setdefault(record.normalized_name, record.name)
        if record.valid_to:
            boundaries.setdefault(record.normalized_name, set()).add(record.valid_to)
    for transition in graph.transitions:
        normalized = normalize_party_name(transition.from_name)
        names.setdefault(normalized, transition.from_name)
        boundaries.setdefault(normalized, set()).add(transition.effective_on)
    return boundaries, names


def _canonical(graph: LegalNameAliasGraph, evidence: tuple[CompleteSubmissionNameEvidence, ...]):
    latest = max((x for x in evidence if x.current_name), key=lambda x: (x.filing_date, x.accession_number), default=None)
    sub = graph.canonical_name_as_of
    header = latest.current_name if latest else None
    if not header:
        return sub, "submissions_only", latest, ()
    if not sub:
        return header, "header_only_fallback", latest, ()
    if normalize_party_name(sub) == normalize_party_name(header):
        return header, "confirmed_by_header_and_submissions", latest, ()
    sub_norm = normalize_party_name(sub)
    current = [x for x in graph.records if x.normalized_name == sub_norm and x.valid_to is None]
    current_from = max((x.valid_from for x in current if x.valid_from), default=None)
    if latest and current_from and latest.filing_date < current_from <= graph.analysis_date:
        return sub, "submissions_newer_than_latest_header", latest, ("submissions rename boundary post-dates latest sampled header; submissions canonical name used",)
    return header, "conflict_header_preferred", latest, ("submissions canonical name disagreed with historical complete-submission header; header preferred to avoid current-name look-ahead",)


def reconcile_legal_name_sources(graph: LegalNameAliasGraph, header_evidence: Iterable[CompleteSubmissionNameEvidence]) -> ReconciledLegalNameAliasGraph:
    evidence = tuple(sorted((x for x in header_evidence if x.filing_date <= graph.analysis_date), key=lambda x: (x.filing_date, x.accession_number)))
    header_records, header_transitions, header_boundaries, header_names = _header_semantics(evidence)
    sub_boundaries, sub_names = _submissions_maps(graph)
    canonical, canonical_status, latest, canonical_warnings = _canonical(graph, evidence)
    canonical_norm = normalize_party_name(canonical) if canonical else None

    records = dict(header_records)
    for record in graph.records:
        if record.source_kind == "sec_submissions_current_name" and canonical_norm and record.normalized_name != canonical_norm:
            safe_newer = bool(record.valid_from and latest and latest.filing_date < record.valid_from <= graph.analysis_date)
            if not safe_newer:
                continue
        records[(record.normalized_name, record.valid_from, record.valid_to, record.source_kind)] = record

    transitions = dict(header_transitions)
    for item in graph.transitions:
        if item.effective_on <= graph.analysis_date:
            transitions[(normalize_party_name(item.from_name), normalize_party_name(item.to_name), item.effective_on, item.source_kind)] = item

    comparisons = []
    for normalized in sorted(set(sub_names) | set(header_names)):
        sub_dates = tuple(sorted(sub_boundaries.get(normalized, set())))
        hdr_dates = tuple(sorted(header_boundaries.get(normalized, set())))
        if normalized in sub_names and normalized in header_names:
            status = "confirmed_same_boundary" if sub_dates and hdr_dates and sub_dates == hdr_dates else ("boundary_conflict" if sub_dates and hdr_dates else "confirmed_name_boundary_unavailable")
        else:
            status = "header_only" if normalized in header_names else "submissions_only"
        comparisons.append(LegalNameSourceComparison(header_names.get(normalized) or sub_names[normalized], normalized, sub_dates, hdr_dates, status))

    warnings = list(graph.warnings) + list(canonical_warnings)
    for item in evidence:
        warnings.extend(f"{item.accession_number}: {warning}" for warning in item.warnings)
    for item in comparisons:
        if item.status == "boundary_conflict":
            warnings.append(f"legal-name boundary conflict for {item.name}: submissions={item.submissions_boundaries}, headers={item.header_boundaries}")

    return ReconciledLegalNameAliasGraph(
        cik=graph.cik,
        analysis_date=graph.analysis_date,
        canonical_name_as_of=canonical,
        canonical_status=canonical_status,
        records=tuple(sorted(records.values(), key=lambda x: (x.valid_from or date.min, x.valid_to or date.max, x.normalized_name, x.source_kind))),
        transitions=tuple(sorted(transitions.values(), key=lambda x: (x.effective_on, normalize_party_name(x.from_name), x.source_kind))),
        comparisons=tuple(comparisons),
        header_evidence=evidence,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def build_reconciled_legal_name_alias_graph(client: SecClient, *, cik: str | int, analysis_date: date, filings: Iterable[SecFiling] | None = None, header_filing_limit: int = 3) -> ReconciledLegalNameAliasGraph:
    """Build the explicit fixed-limit reconciliation helper without double-reconciling.

    The public `build_legal_name_alias_graph` adds adaptive historical scanning.
    This helper retains the older fixed-limit behavior for callers/tests that need
    an exact sample size.
    """
    if header_filing_limit < 0:
        raise ValueError("header_filing_limit cannot be negative")
    cik10 = normalize_cik(cik)
    graph = build_legal_name_alias_graph_from_submissions(
        client.submissions(cik10),
        cik=cik10,
        analysis_date=analysis_date,
    )
    if header_filing_limit == 0:
        return reconcile_legal_name_sources(graph, ())
    candidates = tuple(filings) if filings is not None else client.filings_as_of(cik10, analysis_date, forms=HEADER_FORMS)
    selected = sorted((x for x in candidates if normalize_cik(x.cik) == cik10 and x.filing_date <= analysis_date), key=lambda x: (x.filing_date, x.accession_number), reverse=True)[:header_filing_limit]
    evidence = []
    warnings = []
    for filing in selected:
        try:
            evidence.append(fetch_complete_submission_name_evidence(client, filing=filing, target_cik=cik10))
        except Exception as exc:
            warnings.append(f"failed complete-submission legal-name header lookup for {filing.accession_number}: {exc}")
    reconciled = reconcile_legal_name_sources(graph, evidence)
    status = "fixed_limit_complete" if len(selected) == len(candidates) else "fixed_limit_sample"
    return ReconciledLegalNameAliasGraph(
        reconciled.cik,
        reconciled.analysis_date,
        reconciled.canonical_name_as_of,
        reconciled.canonical_status,
        reconciled.records,
        reconciled.transitions,
        reconciled.comparisons,
        reconciled.header_evidence,
        tuple(dict.fromkeys(list(reconciled.warnings) + warnings)),
        status,
        len(candidates),
        len(evidence),
        min((item.filing_date for item in evidence), default=None),
    )


def build_reconciled_legal_name_alias_graphs(client: SecClient, *, ciks: Iterable[str | int], analysis_date: date, existing: Iterable[ReconciledLegalNameAliasGraph] = (), header_filing_limit: int = 3):
    known = {x.cik: x for x in existing}
    warnings = []
    for cik in sorted({normalize_cik(x) for x in ciks}):
        if cik in known:
            continue
        try:
            graph = build_reconciled_legal_name_alias_graph(client, cik=cik, analysis_date=analysis_date, header_filing_limit=header_filing_limit)
        except Exception as exc:
            warnings.append(f"failed reconciled legal-name lookup for CIK {cik}: {exc}")
            continue
        known[cik] = graph
        warnings.extend(f"CIK {cik}: {item}" for item in graph.warnings)
    return tuple(known[key] for key in sorted(known)), tuple(dict.fromkeys(warnings))


def reconciled_legal_name_graph_to_dict(graph: ReconciledLegalNameAliasGraph) -> dict[str, Any]:
    def convert(value: Any) -> Any:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, tuple):
            return [convert(x) for x in value]
        if isinstance(value, list):
            return [convert(x) for x in value]
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        if hasattr(value, "__dataclass_fields__"):
            return convert(asdict(value))
        return value
    return convert(graph)

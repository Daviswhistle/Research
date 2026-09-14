from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from typing import Any, Iterable, Protocol

from .contract_identity import ContractIdentity, extract_contract_identities, reverse_search_contract_identity
from .contract_parties import ContractParty, normalize_party_name
from .cross_cik import cik_from_accession
from .legal_name_alias import build_legal_name_alias_graph
from .sec import SecClient, normalize_cik
from .sec_cik_lookup import SecCikLookupMatch, fetch_cik_lookup_matches
from .sec_instruments import FilingDocument, SecInstrumentPacket, _get_text, extract_source_candidates
from .source_graph import REFERENCE_FORMS, SourceGraphNode


class LegalNameGraphLike(Protocol):
    cik: str

    @property
    def alias_names(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class ExternalEntityCandidate:
    normalized_name: str
    cik: str
    registry_title: str | None
    ticker: str | None
    source_kind: str


@dataclass(frozen=True)
class NamedEntityContractResolution:
    resolution_id: str
    source_node_id: str
    source_cik: str
    source_accession: str
    source_published_on: date
    source_depth: int
    contract_kind: str
    execution_date: date
    party_role: str
    party_name: str
    normalized_party_name: str
    candidate_ciks: tuple[str, ...]
    confirmed_ciks: tuple[str, ...]
    status: str
    reason: str
    target_cik: str | None = None
    target_accession: str | None = None
    target_document_url: str | None = None


@dataclass(frozen=True)
class NamedEntityContractGraph:
    analysis_date: date
    candidates: tuple[ExternalEntityCandidate, ...]
    resolutions: tuple[NamedEntityContractResolution, ...]
    resolved_documents: tuple[FilingDocument, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class NamedEntityContractExpansion:
    packet: SecInstrumentPacket
    graph: NamedEntityContractGraph


def _candidate_key(item: ExternalEntityCandidate) -> tuple[str, str, str]:
    return (item.normalized_name, item.cik, item.source_kind)


def _registry_candidates(
    client: SecClient,
    party: ContractParty,
    known_legal_name_graphs: Iterable[LegalNameGraphLike],
    all_cik_matches: dict[str, tuple[SecCikLookupMatch, ...]] | None = None,
) -> tuple[ExternalEntityCandidate, ...]:
    target = party.normalized_name
    candidates: list[ExternalEntityCandidate] = []
    seen: set[tuple[str, str, str]] = set()

    for graph in known_legal_name_graphs:
        if target not in set(graph.alias_names):
            continue
        item = ExternalEntityCandidate(
            normalized_name=target,
            cik=normalize_cik(graph.cik),
            registry_title=None,
            ticker=None,
            source_kind="known_sec_legal_name_graph",
        )
        if _candidate_key(item) not in seen:
            candidates.append(item)
            seen.add(_candidate_key(item))

    try:
        ticker_map = client.ticker_map()
    except Exception:
        ticker_map = {}
    for ticker, value in ticker_map.items():
        if not isinstance(value, tuple) or len(value) < 2:
            continue
        cik, title = value[0], value[1]
        if normalize_party_name(str(title)) != target:
            continue
        item = ExternalEntityCandidate(
            normalized_name=target,
            cik=normalize_cik(cik),
            registry_title=str(title),
            ticker=str(ticker),
            source_kind="sec_company_tickers_candidate_only",
        )
        if _candidate_key(item) not in seen:
            candidates.append(item)
            seen.add(_candidate_key(item))

    for match in (all_cik_matches or {}).get(target, ()):
        item = ExternalEntityCandidate(
            normalized_name=target,
            cik=normalize_cik(match.cik),
            registry_title=match.name,
            ticker=None,
            source_kind="sec_cik_lookup_data_candidate_only",
        )
        if _candidate_key(item) not in seen:
            candidates.append(item)
            seen.add(_candidate_key(item))

    return tuple(candidates)


def _parties_to_resolve(identity: ContractIdentity) -> tuple[ContractParty, ...]:
    primary = tuple(item for item in identity.parties if item.role in {"borrower", "issuer"})
    if primary:
        return primary
    return tuple(item for item in identity.parties if item.role == "guarantor")


def _identity_key(source_node_id: str, identity: ContractIdentity, party: ContractParty) -> tuple:
    return (
        source_node_id,
        identity.kind,
        identity.execution_date,
        party.role,
        party.normalized_name,
    )


def resolve_named_entity_contracts(
    client: SecClient,
    *,
    packet: SecInstrumentPacket,
    source_nodes: Iterable[SourceGraphNode],
    root_cik: str,
    known_legal_name_graphs: Iterable[LegalNameGraphLike] = (),
    max_contract_search_filings: int = 40,
    contract_days_before_execution: int = 30,
    contract_days_after_execution: int = 550,
    max_candidates_per_exhibit: int = 20,
) -> NamedEntityContractExpansion:
    """Resolve name-only external contract parties only after SEC-backed confirmation.

    Current ticker metadata and the SEC's historically cumulative all-CIK/name list
    are candidate generation only. A CIK is usable only if that CIK had a filing by
    the source date and its cutoff-safe SEC legal-name graph contains the exact
    normalized party name. The contract is then reverse-searched inside every
    confirmed CIK. Automatic resolution occurs only when exactly one
    (CIK, exhibit URL) survives all checks.
    """

    root_cik = normalize_cik(root_cik)
    known_legal_name_graphs = tuple(known_legal_name_graphs)
    warnings: list[str] = []
    all_candidates: list[ExternalEntityCandidate] = []
    resolutions: list[NamedEntityContractResolution] = []
    resolved_documents: dict[str, FilingDocument] = {}
    text_cache: dict[str, str] = {}
    index_cache: dict[str, tuple[FilingDocument, ...]] = {}
    confirmation_cache: dict[tuple[str, date, str], tuple[bool, tuple[str, ...], tuple]] = {}
    seen_identity_keys: set[tuple] = set()
    work_items: list[tuple[SourceGraphNode, str, ContractIdentity, ContractParty]] = []

    # First collect all role-labelled contract parties so the large cumulative CIK
    # file can be scanned once for the entire research packet rather than once per
    # party occurrence.
    for source in sorted(source_nodes, key=lambda item: (item.depth, item.node_id)):
        if source.filing_date > packet.analysis_date:
            continue
        source_cik = cik_from_accession(source.accession_number) or root_cik
        try:
            source_text = text_cache.get(source.url)
            if source_text is None:
                source_text = _get_text(client, source.url)
                text_cache[source.url] = source_text
        except Exception as exc:
            warnings.append(f"failed named-entity source fetch {source.url}: {exc}")
            continue

        for identity in extract_contract_identities(source_text):
            for party in _parties_to_resolve(identity):
                identity_key = _identity_key(source.node_id, identity, party)
                if identity_key in seen_identity_keys:
                    continue
                seen_identity_keys.add(identity_key)
                work_items.append((source, source_cik, identity, party))

    requested_names = {party.normalized_name for _source, _source_cik, _identity, party in work_items}
    try:
        all_cik_matches = fetch_cik_lookup_matches(client, requested_names)
    except Exception as exc:
        all_cik_matches = {}
        if requested_names:
            warnings.append(f"failed SEC cumulative CIK/name candidate lookup: {exc}")

    for source, source_cik, identity, party in work_items:
        candidates = _registry_candidates(
            client,
            party,
            known_legal_name_graphs,
            all_cik_matches=all_cik_matches,
        )
        all_candidates.extend(candidates)
        candidate_ciks = tuple(sorted({item.cik for item in candidates if item.cik != source_cik}))
        confirmed: list[tuple[str, tuple[str, ...], tuple]] = []

        for candidate_cik in candidate_ciks:
            cache_key = (candidate_cik, source.filing_date, party.normalized_name)
            cached = confirmation_cache.get(cache_key)
            if cached is None:
                try:
                    existence_filings = tuple(
                        item
                        for item in client.filings_as_of(candidate_cik, source.filing_date, forms=())
                        if item.filing_date <= source.filing_date
                    )
                except Exception as exc:
                    warnings.append(
                        f"failed historical existence check for named entity CIK {candidate_cik}: {exc}"
                    )
                    cached = (False, (), ())
                    confirmation_cache[cache_key] = cached
                    continue
                if not existence_filings:
                    cached = (False, (), ())
                    confirmation_cache[cache_key] = cached
                    continue
                try:
                    name_graph = build_legal_name_alias_graph(
                        client,
                        cik=candidate_cik,
                        analysis_date=source.filing_date,
                    )
                except Exception as exc:
                    warnings.append(
                        f"failed source-date legal-name confirmation for CIK {candidate_cik}: {exc}"
                    )
                    cached = (False, (), existence_filings)
                    confirmation_cache[cache_key] = cached
                    continue
                alias_names = tuple(name_graph.alias_names)
                confirmed_name = party.normalized_name in set(alias_names)
                cached = (confirmed_name, alias_names, existence_filings)
                confirmation_cache[cache_key] = cached
            if cached[0]:
                confirmed.append((candidate_cik, cached[1], cached[2]))

        matches: dict[tuple[str, str], FilingDocument] = {}
        confirmed_ciks: list[str] = []
        search_warnings: list[str] = []
        for candidate_cik, alias_names, _existence_filings in confirmed:
            confirmed_ciks.append(candidate_cik)
            try:
                filings = client.filings_as_of(
                    candidate_cik,
                    source.filing_date,
                    forms=REFERENCE_FORMS,
                )
            except Exception as exc:
                search_warnings.append(
                    f"failed contract filing lookup for named entity CIK {candidate_cik}: {exc}"
                )
                continue
            search = reverse_search_contract_identity(
                client,
                identity=identity,
                filings=filings,
                source_published_on=source.filing_date,
                max_filings=max_contract_search_filings,
                days_before_execution=contract_days_before_execution,
                days_after_execution=contract_days_after_execution,
                index_cache=index_cache,
                text_cache=text_cache,
                alias_groups=(alias_names,) if len(alias_names) > 1 else (),
            )
            search_warnings.extend(search.warnings)
            for document in search.candidates:
                matches[(candidate_cik, document.url)] = document
        warnings.extend(search_warnings)

        status: str
        reason: str
        target_cik: str | None = None
        target_accession: str | None = None
        target_document_url: str | None = None
        if not candidate_ciks:
            status = "no_candidate_cik"
            reason = "no exact normalized SEC registry/legal-name candidate"
        elif not confirmed_ciks:
            status = "name_not_confirmed_at_source_date"
            reason = "candidate CIKs lacked source-date SEC name evidence"
        elif not matches:
            status = "contract_not_found_in_confirmed_cik"
            reason = "source-date name-confirmed CIKs had no matching historical contract exhibit"
        elif len(matches) > 1:
            status = "ambiguous_named_entity_contract"
            reason = f"{len(matches)} (CIK, exhibit) matches survived; no automatic selection"
        else:
            (target_cik, target_document_url), document = next(iter(matches.items()))
            target_accession = document.source_accession
            resolved_documents[document.url] = document
            status = "resolved_named_entity_contract"
            reason = "unique source-date SEC name confirmation plus unique contract identity match"

        resolutions.append(
            NamedEntityContractResolution(
                resolution_id=f"named-entity-resolution:{len(resolutions) + 1}",
                source_node_id=source.node_id,
                source_cik=source_cik,
                source_accession=source.accession_number,
                source_published_on=source.filing_date,
                source_depth=source.depth,
                contract_kind=identity.kind,
                execution_date=identity.execution_date,
                party_role=party.role,
                party_name=party.name,
                normalized_party_name=party.normalized_name,
                candidate_ciks=candidate_ciks,
                confirmed_ciks=tuple(sorted(set(confirmed_ciks))),
                status=status,
                reason=reason,
                target_cik=target_cik,
                target_accession=target_accession,
                target_document_url=target_document_url,
            )
        )

    existing_urls = {item.url for item in packet.documents}
    documents = list(packet.documents)
    spans = list(packet.spans)
    debt_candidates = list(packet.candidates)
    packet_warnings = list(packet.warnings)
    for document in resolved_documents.values():
        if document.url in existing_urls:
            continue
        existing_urls.add(document.url)
        documents.append(document)
        try:
            document_text = text_cache.get(document.url)
            if document_text is None:
                document_text = _get_text(client, document.url)
                text_cache[document.url] = document_text
            new_spans, new_candidates = extract_source_candidates(
                document_text,
                document,
                max_candidates=max_candidates_per_exhibit,
            )
            spans.extend(new_spans)
            debt_candidates.extend(new_candidates)
        except Exception as exc:
            packet_warnings.append(
                f"failed to extract named-entity resolved document {document.document}: {exc}"
            )

    candidate_map = {_candidate_key(item): item for item in all_candidates}
    graph = NamedEntityContractGraph(
        analysis_date=packet.analysis_date,
        candidates=tuple(candidate_map.values()),
        resolutions=tuple(resolutions),
        resolved_documents=tuple(resolved_documents.values()),
        warnings=tuple(dict.fromkeys(warnings)),
    )
    expanded_packet = replace(
        packet,
        documents=tuple(documents),
        spans=tuple(spans),
        candidates=tuple(debt_candidates),
        warnings=tuple(dict.fromkeys(packet_warnings + warnings)),
    )
    return NamedEntityContractExpansion(packet=expanded_packet, graph=graph)


def named_entity_contract_graph_to_dict(graph: NamedEntityContractGraph) -> dict[str, Any]:
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

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Iterable

from .debt_instruments import DebtInstrumentLedger, DebtInstrumentSnapshot, DebtInstrumentVersion


LINEAGE_EVENT_TYPES = frozenset(
    {
        "amendment",
        "exchange",
        "refinancing",
        "redemption",
        "repurchase",
        "conversion",
        "issuance",
        "termination",
        "other",
    }
)
LINEAGE_STATUSES = frozenset({"candidate", "verified"})
ACCOUNTING_TREATMENTS = frozenset(
    {
        "undetermined",
        "modification",
        "extinguishment",
        "partial_extinguishment",
        "not_applicable",
    }
)
ACCOUNTING_BASES = frozenset({"undetermined", "issuer_disclosed", "manual_analysis"})


@dataclass(frozen=True)
class DebtLineageParticipation:
    stable_id: str
    amount: float | None = None
    source_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class DebtAccountingAssessment:
    treatment: str = "undetermined"
    standard: str | None = None
    basis: str = "undetermined"
    quantitative_test_pct: float | None = None
    source_refs: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DebtLineageEvent:
    event_id: str
    effective_date: date
    event_type: str
    status: str
    predecessors: tuple[DebtLineageParticipation, ...]
    successors: tuple[DebtLineageParticipation, ...]
    source_refs: tuple[str, ...]
    source_accessions: tuple[str, ...] = ()
    cash_paid: float | None = None
    fees_paid: float | None = None
    equity_issued_shares: float | None = None
    equity_issued_value: float | None = None
    accounting: DebtAccountingAssessment = DebtAccountingAssessment()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DebtLineageEdge:
    from_node: str
    to_node: str
    event_id: str
    event_status: str
    role: str
    amount: float | None = None


@dataclass(frozen=True)
class DebtLineageImpact:
    event_id: str
    event_status: str
    predecessor_principal: float | None
    successor_principal: float | None
    principal_delta: float | None
    nearest_maturity_before: str | None
    nearest_maturity_after: str | None
    maturity_effect: str
    secured_principal_before: float | None
    secured_principal_after: float | None
    secured_principal_delta: float | None
    cash_paid: float | None
    fees_paid: float | None
    equity_issued_shares: float | None
    equity_issued_value: float | None
    accounting_treatment: str
    accounting_standard: str | None
    accounting_basis: str
    signals: tuple[str, ...]
    unresolved: tuple[str, ...]


@dataclass(frozen=True)
class DebtLineageGraph:
    events: tuple[DebtLineageEvent, ...]
    edges: tuple[DebtLineageEdge, ...]
    impacts: tuple[DebtLineageImpact, ...]
    root_instruments: tuple[str, ...]
    terminal_instruments: tuple[str, ...]
    unresolved_events: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class DebtLineageSourceValidation:
    valid: bool
    events: tuple[DebtLineageEvent, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def _parse_date(value: Any, field: str) -> date:
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be YYYY-MM-DD")
    return date.fromisoformat(value[:10])


def _optional_nonnegative_float(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    numeric = float(value)
    if numeric < 0:
        raise ValueError(f"{field} must be non-negative")
    return numeric


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a list of strings")
    return tuple(item.strip() for item in value if item.strip())


def _participation_from_dict(raw: Any, field: str) -> DebtLineageParticipation:
    if isinstance(raw, str):
        stable_id = raw.strip()
        if not stable_id:
            raise ValueError(f"{field}.stable_id is required")
        return DebtLineageParticipation(stable_id=stable_id)
    if not isinstance(raw, dict):
        raise ValueError(f"{field} must be a stable-id string or object")
    stable_id = str(raw.get("stable_id") or "").strip()
    if not stable_id:
        raise ValueError(f"{field}.stable_id is required")
    return DebtLineageParticipation(
        stable_id=stable_id,
        amount=_optional_nonnegative_float(raw.get("amount"), f"{field}.amount"),
        source_refs=_string_tuple(raw.get("source_refs", []), f"{field}.source_refs"),
    )


def _accounting_from_dict(raw: Any, event_id: str) -> DebtAccountingAssessment:
    if raw is None:
        return DebtAccountingAssessment()
    if not isinstance(raw, dict):
        raise ValueError(f"{event_id}.accounting must be an object")
    treatment = str(raw.get("treatment") or "undetermined").strip().lower()
    basis = str(raw.get("basis") or "undetermined").strip().lower()
    if treatment not in ACCOUNTING_TREATMENTS:
        raise ValueError(
            f"{event_id}.accounting.treatment must be one of {sorted(ACCOUNTING_TREATMENTS)}"
        )
    if basis not in ACCOUNTING_BASES:
        raise ValueError(f"{event_id}.accounting.basis must be one of {sorted(ACCOUNTING_BASES)}")
    source_refs = _string_tuple(raw.get("source_refs", []), f"{event_id}.accounting.source_refs")
    test_pct = _optional_nonnegative_float(
        raw.get("quantitative_test_pct"), f"{event_id}.accounting.quantitative_test_pct"
    )
    if treatment not in {"undetermined", "not_applicable"} and not source_refs:
        raise ValueError(
            f"{event_id}.accounting.source_refs are required when accounting treatment is asserted"
        )
    if treatment not in {"undetermined", "not_applicable"} and basis == "undetermined":
        raise ValueError(
            f"{event_id}.accounting.basis is required when accounting treatment is asserted"
        )
    if test_pct is not None and (basis == "undetermined" or not source_refs):
        raise ValueError(
            f"{event_id}.accounting quantitative test requires basis and source_refs"
        )
    return DebtAccountingAssessment(
        treatment=treatment,
        standard=(str(raw["standard"]).strip() if raw.get("standard") else None),
        basis=basis,
        quantitative_test_pct=test_pct,
        source_refs=source_refs,
        notes=_string_tuple(raw.get("notes", []), f"{event_id}.accounting.notes"),
    )


def _event_from_dict(raw: dict[str, Any]) -> DebtLineageEvent:
    event_id = str(raw.get("event_id") or "").strip()
    if not event_id:
        raise ValueError("lineage event_id is required")
    event_type = str(raw.get("event_type") or "").strip().lower()
    if event_type not in LINEAGE_EVENT_TYPES:
        raise ValueError(f"{event_id}.event_type must be one of {sorted(LINEAGE_EVENT_TYPES)}")
    status = str(raw.get("status") or "candidate").strip().lower()
    if status not in LINEAGE_STATUSES:
        raise ValueError(f"{event_id}.status must be one of {sorted(LINEAGE_STATUSES)}")

    predecessors_raw = raw.get("predecessors", [])
    successors_raw = raw.get("successors", [])
    if not isinstance(predecessors_raw, list) or not isinstance(successors_raw, list):
        raise ValueError(f"{event_id}.predecessors/successors must be lists")
    predecessors = tuple(
        _participation_from_dict(item, f"{event_id}.predecessors[{idx}]")
        for idx, item in enumerate(predecessors_raw)
    )
    successors = tuple(
        _participation_from_dict(item, f"{event_id}.successors[{idx}]")
        for idx, item in enumerate(successors_raw)
    )

    source_accessions = _string_tuple(raw.get("source_accessions", []), f"{event_id}.source_accessions")
    single_accession = str(raw.get("source_accession") or "").strip()
    if single_accession:
        source_accessions = tuple(dict.fromkeys((*source_accessions, single_accession)))

    event = DebtLineageEvent(
        event_id=event_id,
        effective_date=_parse_date(raw.get("effective_date"), f"{event_id}.effective_date"),
        event_type=event_type,
        status=status,
        predecessors=predecessors,
        successors=successors,
        source_refs=_string_tuple(raw.get("source_refs", []), f"{event_id}.source_refs"),
        source_accessions=source_accessions,
        cash_paid=_optional_nonnegative_float(raw.get("cash_paid"), f"{event_id}.cash_paid"),
        fees_paid=_optional_nonnegative_float(raw.get("fees_paid"), f"{event_id}.fees_paid"),
        equity_issued_shares=_optional_nonnegative_float(
            raw.get("equity_issued_shares"), f"{event_id}.equity_issued_shares"
        ),
        equity_issued_value=_optional_nonnegative_float(
            raw.get("equity_issued_value"), f"{event_id}.equity_issued_value"
        ),
        accounting=_accounting_from_dict(raw.get("accounting"), event_id),
        notes=_string_tuple(raw.get("notes", []), f"{event_id}.notes"),
    )
    _validate_event_shape(event)
    return event


def _validate_event_shape(event: DebtLineageEvent) -> None:
    predecessor_ids = [item.stable_id for item in event.predecessors]
    successor_ids = [item.stable_id for item in event.successors]
    predecessor_set = set(predecessor_ids)
    successor_set = set(successor_ids)
    if len(predecessor_ids) != len(predecessor_set):
        raise ValueError(f"{event.event_id}: duplicate predecessor stable_id")
    if len(successor_ids) != len(successor_set):
        raise ValueError(f"{event.event_id}: duplicate successor stable_id")
    if not event.source_refs:
        raise ValueError(f"{event.event_id}: source_refs are required")

    if event.event_type == "amendment":
        if len(event.predecessors) != 1 or len(event.successors) != 1:
            raise ValueError(f"{event.event_id}: amendment requires exactly one predecessor and successor")
        if event.predecessors[0].stable_id != event.successors[0].stable_id:
            raise ValueError(
                f"{event.event_id}: amendment must preserve stable_id; use exchange/refinancing for cross-ID lineage"
            )
    elif event.event_type in {"exchange", "refinancing", "conversion"}:
        if not event.predecessors or not event.successors:
            raise ValueError(f"{event.event_id}: {event.event_type} requires predecessor and successor instruments")
        overlap = sorted(predecessor_set & successor_set)
        if overlap:
            raise ValueError(
                f"{event.event_id}: {event.event_type} predecessor/successor stable IDs must be disjoint; "
                f"unchanged residual debt stays outside the event: {', '.join(overlap)}"
            )
    elif event.event_type in {"redemption", "repurchase", "termination"}:
        if not event.predecessors:
            raise ValueError(f"{event.event_id}: {event.event_type} requires predecessor instruments")
        if event.successors:
            raise ValueError(
                f"{event.event_id}: {event.event_type} must not have successor instruments; "
                "use refinancing/exchange when debt is replaced"
            )
    elif event.event_type == "issuance":
        if event.predecessors or not event.successors:
            raise ValueError(f"{event.event_id}: issuance requires successors and no predecessors")
    elif not event.predecessors and not event.successors:
        raise ValueError(f"{event.event_id}: lineage event must reference at least one instrument")

    event_ref_set = set(event.source_refs)
    for participant in (*event.predecessors, *event.successors):
        unknown = set(participant.source_refs) - event_ref_set
        if unknown:
            raise ValueError(
                f"{event.event_id}: participant source_refs must be included in event source_refs: {sorted(unknown)}"
            )
    accounting_unknown = set(event.accounting.source_refs) - event_ref_set
    if accounting_unknown:
        raise ValueError(
            f"{event.event_id}: accounting source_refs must be included in event source_refs: {sorted(accounting_unknown)}"
        )


def debt_lineage_events_from_dict(raw: dict[str, Any] | list[dict[str, Any]]) -> tuple[DebtLineageEvent, ...]:
    rows = raw if isinstance(raw, list) else raw.get("events", [])
    if not isinstance(rows, list):
        raise ValueError("events must be a list")
    events = tuple(_event_from_dict(item) for item in rows)
    ids = [event.event_id for event in events]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate lineage event_id values")
    return events


def _versions_by_id(ledger: DebtInstrumentLedger) -> dict[str, tuple[DebtInstrumentVersion, ...]]:
    grouped: dict[str, list[DebtInstrumentVersion]] = {}
    for version in ledger.versions:
        grouped.setdefault(version.stable_id, []).append(version)
    return {
        stable_id: tuple(sorted(rows, key=lambda item: (item.snapshot.as_of_date, item.snapshot.source_accession)))
        for stable_id, rows in grouped.items()
    }


def _boundary_snapshot(
    versions: tuple[DebtInstrumentVersion, ...],
    event_date: date,
    *,
    predecessor: bool,
) -> tuple[DebtInstrumentSnapshot, str | None]:
    if predecessor:
        eligible = [item.snapshot for item in versions if item.snapshot.as_of_date <= event_date]
        if eligible:
            snapshot = eligible[-1]
            warning = None
            if snapshot.as_of_date != event_date:
                warning = (
                    f"predecessor observation date {snapshot.as_of_date.isoformat()} differs from "
                    f"event effective date {event_date.isoformat()}"
                )
            return snapshot, warning
        snapshot = versions[0].snapshot
        return (
            snapshot,
            f"predecessor first observed {snapshot.as_of_date.isoformat()} after event effective date "
            f"{event_date.isoformat()}",
        )
    eligible = [item.snapshot for item in versions if item.snapshot.as_of_date >= event_date]
    if eligible:
        snapshot = eligible[0]
        warning = None
        if snapshot.as_of_date != event_date:
            warning = (
                f"successor observation date {snapshot.as_of_date.isoformat()} differs from "
                f"event effective date {event_date.isoformat()}"
            )
        return snapshot, warning
    snapshot = versions[-1].snapshot
    return (
        snapshot,
        f"successor last observed {snapshot.as_of_date.isoformat()} before event effective date "
        f"{event_date.isoformat()}",
    )


def _participant_amount(
    participant: DebtLineageParticipation,
    snapshot: DebtInstrumentSnapshot,
) -> float | None:
    return participant.amount if participant.amount is not None else snapshot.principal


def _amount_exceeds_snapshot(
    participant: DebtLineageParticipation,
    snapshot: DebtInstrumentSnapshot,
) -> bool:
    if participant.amount is None or snapshot.principal is None:
        return False
    tolerance = max(1e-9, abs(snapshot.principal) * 1e-9)
    return participant.amount > snapshot.principal + tolerance


def _amount_observation_warning(
    participant: DebtLineageParticipation,
    snapshot: DebtInstrumentSnapshot,
    event_date: date,
    *,
    role: str,
) -> str | None:
    if not _amount_exceeds_snapshot(participant, snapshot):
        return None
    if snapshot.as_of_date == event_date:
        return None
    return (
        f"{role} event amount {participant.amount} for {participant.stable_id} exceeds nearest observed "
        f"principal {snapshot.principal} on {snapshot.as_of_date.isoformat()}; observation is not on the "
        "event date, so it is not used as a hard ceiling"
    )


def _sum_known(values: Iterable[float | None]) -> float | None:
    rows = tuple(values)
    if any(value is None for value in rows):
        return None
    return sum(float(value) for value in rows if value is not None)


def _maturity_key(snapshot: DebtInstrumentSnapshot) -> tuple[int, int, int] | None:
    if snapshot.maturity_date is not None:
        return snapshot.maturity_date.year, snapshot.maturity_date.month, snapshot.maturity_date.day
    if snapshot.maturity_year is not None:
        return snapshot.maturity_year, 1, 1
    return None


def _maturity_label(snapshot: DebtInstrumentSnapshot) -> str | None:
    if snapshot.maturity_date is not None:
        return snapshot.maturity_date.isoformat()
    if snapshot.maturity_year is not None:
        return str(snapshot.maturity_year)
    return None


def _nearest_maturity(snapshots: Iterable[DebtInstrumentSnapshot]) -> tuple[tuple[int, int, int] | None, str | None]:
    candidates = [(key, _maturity_label(snapshot)) for snapshot in snapshots if (key := _maturity_key(snapshot))]
    if not candidates:
        return None, None
    return min(candidates, key=lambda item: item[0])


def _secured_principal(
    participations: tuple[DebtLineageParticipation, ...],
    snapshots: tuple[DebtInstrumentSnapshot, ...],
) -> float | None:
    values: list[float] = []
    for participant, snapshot in zip(participations, snapshots):
        amount = _participant_amount(participant, snapshot)
        if amount is None or snapshot.secured is None:
            return None
        if snapshot.secured:
            values.append(amount)
    return sum(values)


def _impact_for_event(
    event: DebtLineageEvent,
    versions_by_id: dict[str, tuple[DebtInstrumentVersion, ...]],
) -> DebtLineageImpact:
    unresolved: list[str] = []
    predecessor_snapshots: list[DebtInstrumentSnapshot] = []
    successor_snapshots: list[DebtInstrumentSnapshot] = []

    for participant in event.predecessors:
        snapshot, warning = _boundary_snapshot(
            versions_by_id[participant.stable_id], event.effective_date, predecessor=True
        )
        predecessor_snapshots.append(snapshot)
        if warning:
            unresolved.append(f"{participant.stable_id}: {warning}")
        amount_warning = _amount_observation_warning(
            participant, snapshot, event.effective_date, role="predecessor"
        )
        if amount_warning:
            unresolved.append(amount_warning)
    for participant in event.successors:
        snapshot, warning = _boundary_snapshot(
            versions_by_id[participant.stable_id], event.effective_date, predecessor=False
        )
        successor_snapshots.append(snapshot)
        if warning:
            unresolved.append(f"{participant.stable_id}: {warning}")
        amount_warning = _amount_observation_warning(
            participant, snapshot, event.effective_date, role="successor"
        )
        if amount_warning:
            unresolved.append(amount_warning)

    predecessor_principal = _sum_known(
        _participant_amount(participant, snapshot)
        for participant, snapshot in zip(event.predecessors, predecessor_snapshots)
    )
    successor_principal = _sum_known(
        _participant_amount(participant, snapshot)
        for participant, snapshot in zip(event.successors, successor_snapshots)
    )
    principal_delta = (
        successor_principal - predecessor_principal
        if predecessor_principal is not None and successor_principal is not None
        else None
    )
    if event.predecessors and predecessor_principal is None:
        unresolved.append("predecessor participating principal is incomplete")
    if event.successors and successor_principal is None:
        unresolved.append("successor participating principal is incomplete")

    before_key, before_label = _nearest_maturity(predecessor_snapshots)
    after_key, after_label = _nearest_maturity(successor_snapshots)
    if before_key is None or after_key is None:
        maturity_effect = "not_comparable"
    elif after_key > before_key:
        maturity_effect = "extended"
    elif after_key < before_key:
        maturity_effect = "accelerated"
    else:
        maturity_effect = "unchanged"

    secured_before = _secured_principal(event.predecessors, tuple(predecessor_snapshots))
    secured_after = _secured_principal(event.successors, tuple(successor_snapshots))
    secured_delta = (
        secured_after - secured_before
        if secured_before is not None and secured_after is not None
        else None
    )

    signals: list[str] = []
    if principal_delta is not None:
        if principal_delta < 0:
            signals.append("principal_reduced")
        elif principal_delta > 0:
            signals.append("principal_increased")
    if maturity_effect == "extended":
        signals.append("nearest_maturity_extended")
    elif maturity_effect == "accelerated":
        signals.append("nearest_maturity_accelerated")
    if secured_delta is not None:
        if secured_delta > 0:
            signals.append("secured_principal_increased")
        elif secured_delta < 0:
            signals.append("secured_principal_decreased")
    if event.cash_paid and event.cash_paid > 0:
        signals.append("cash_outflow_to_creditors")
    if event.fees_paid and event.fees_paid > 0:
        signals.append("transaction_fee_outflow")
    if (event.equity_issued_shares and event.equity_issued_shares > 0) or (
        event.equity_issued_value and event.equity_issued_value > 0
    ):
        signals.append("existing_common_dilution")
    if event.event_type in {"redemption", "repurchase", "termination"} and not event.successors:
        signals.append("debt_terminated_without_successor")
    if event.event_type == "issuance" and not event.predecessors:
        signals.append("new_debt_issued_without_predecessor")
    if event.accounting.treatment not in {"undetermined", "not_applicable"}:
        signals.append(f"accounting_{event.accounting.treatment}")
    if event.status != "verified":
        unresolved.append("event is candidate-only and must not be treated as confirmed lineage")

    return DebtLineageImpact(
        event_id=event.event_id,
        event_status=event.status,
        predecessor_principal=predecessor_principal,
        successor_principal=successor_principal,
        principal_delta=principal_delta,
        nearest_maturity_before=before_label,
        nearest_maturity_after=after_label,
        maturity_effect=maturity_effect,
        secured_principal_before=secured_before,
        secured_principal_after=secured_after,
        secured_principal_delta=secured_delta,
        cash_paid=event.cash_paid,
        fees_paid=event.fees_paid,
        equity_issued_shares=event.equity_issued_shares,
        equity_issued_value=event.equity_issued_value,
        accounting_treatment=event.accounting.treatment,
        accounting_standard=event.accounting.standard,
        accounting_basis=event.accounting.basis,
        signals=tuple(signals),
        unresolved=tuple(dict.fromkeys(unresolved)),
    )


def build_debt_lineage_graph(
    ledger: DebtInstrumentLedger,
    events: Iterable[DebtLineageEvent],
) -> DebtLineageGraph:
    events_tuple = tuple(sorted(events, key=lambda item: (item.effective_date, item.event_id)))
    ids = [event.event_id for event in events_tuple]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate lineage event_id values")

    versions_by_id = _versions_by_id(ledger)
    stable_ids = set(versions_by_id)
    edges: list[DebtLineageEdge] = []
    impacts: list[DebtLineageImpact] = []
    warnings: list[str] = []
    unresolved_events: list[str] = []

    for event in events_tuple:
        _validate_event_shape(event)
        referenced = {item.stable_id for item in (*event.predecessors, *event.successors)}
        unknown = sorted(referenced - stable_ids)
        if unknown:
            raise ValueError(f"{event.event_id}: unknown stable_id values: {', '.join(unknown)}")

        for participant in event.predecessors:
            snapshot, _ = _boundary_snapshot(
                versions_by_id[participant.stable_id], event.effective_date, predecessor=True
            )
            amount = _participant_amount(participant, snapshot)
            if (
                _amount_exceeds_snapshot(participant, snapshot)
                and snapshot.as_of_date == event.effective_date
            ):
                raise ValueError(
                    f"{event.event_id}: predecessor amount for {participant.stable_id} exceeds observed principal on event date"
                )
            edges.append(
                DebtLineageEdge(
                    from_node=participant.stable_id,
                    to_node=f"EVENT:{event.event_id}",
                    event_id=event.event_id,
                    event_status=event.status,
                    role="predecessor",
                    amount=amount,
                )
            )
        for participant in event.successors:
            snapshot, _ = _boundary_snapshot(
                versions_by_id[participant.stable_id], event.effective_date, predecessor=False
            )
            amount = _participant_amount(participant, snapshot)
            if (
                _amount_exceeds_snapshot(participant, snapshot)
                and snapshot.as_of_date == event.effective_date
            ):
                raise ValueError(
                    f"{event.event_id}: successor amount for {participant.stable_id} exceeds observed principal on event date"
                )
            edges.append(
                DebtLineageEdge(
                    from_node=f"EVENT:{event.event_id}",
                    to_node=participant.stable_id,
                    event_id=event.event_id,
                    event_status=event.status,
                    role="successor",
                    amount=amount,
                )
            )

        impact = _impact_for_event(event, versions_by_id)
        impacts.append(impact)
        if impact.unresolved:
            unresolved_events.append(event.event_id)
            warnings.extend(f"{event.event_id}: {item}" for item in impact.unresolved)

    verified_events = [event for event in events_tuple if event.status == "verified"]
    lineage_instruments = {
        participant.stable_id
        for event in verified_events
        for participant in (*event.predecessors, *event.successors)
    }
    incoming: set[str] = set()
    outgoing: set[str] = set()
    for event in verified_events:
        predecessor_ids = {item.stable_id for item in event.predecessors}
        successor_ids = {item.stable_id for item in event.successors}
        incoming.update(successor_ids - predecessor_ids)
        outgoing.update(predecessor_ids - successor_ids)
    roots = sorted(lineage_instruments - incoming)
    terminals = sorted(lineage_instruments - outgoing)

    return DebtLineageGraph(
        events=events_tuple,
        edges=tuple(edges),
        impacts=tuple(impacts),
        root_instruments=tuple(roots),
        terminal_instruments=tuple(terminals),
        unresolved_events=tuple(dict.fromkeys(unresolved_events)),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def validate_debt_lineage_events_against_source_packet(
    source_packet: dict[str, Any],
    raw_events: dict[str, Any] | list[dict[str, Any]],
    ledger: DebtInstrumentLedger,
) -> DebtLineageSourceValidation:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        analysis_date = _parse_date(source_packet.get("analysis_date"), "source_packet.analysis_date")
    except ValueError as exc:
        return DebtLineageSourceValidation(False, (), (str(exc),), ())

    try:
        events = debt_lineage_events_from_dict(raw_events)
    except (TypeError, ValueError) as exc:
        return DebtLineageSourceValidation(False, (), (f"lineage event schema: {exc}",), ())

    spans_raw = source_packet.get("spans", [])
    if not isinstance(spans_raw, list):
        return DebtLineageSourceValidation(False, (), ("source_packet.spans must be a list",), ())
    span_map: dict[str, dict[str, Any]] = {}
    for raw in spans_raw:
        if not isinstance(raw, dict):
            continue
        span_id = str(raw.get("span_id") or "").strip()
        if span_id:
            span_map[span_id] = raw
    if not span_map:
        warnings.append("source packet contains no extracted SEC instrument spans")

    stable_ids = {version.stable_id for version in ledger.versions}
    for event in events:
        referenced_ids = {item.stable_id for item in (*event.predecessors, *event.successors)}
        unknown_ids = sorted(referenced_ids - stable_ids)
        if unknown_ids:
            errors.append(f"{event.event_id}: unknown stable_id values: {', '.join(unknown_ids)}")
        if event.effective_date > analysis_date:
            errors.append(
                f"{event.event_id}: effective_date {event.effective_date.isoformat()} is after workspace cutoff {analysis_date.isoformat()}"
            )
        unknown_refs = [ref for ref in event.source_refs if ref not in span_map]
        if unknown_refs:
            errors.append(f"{event.event_id}: unknown source_refs: {', '.join(unknown_refs)}")
            continue
        cited_accessions = {
            str(span_map[ref].get("source_accession") or "").strip()
            for ref in event.source_refs
            if str(span_map[ref].get("source_accession") or "").strip()
        }
        if event.source_accessions and set(event.source_accessions) != cited_accessions:
            errors.append(
                f"{event.event_id}: source_accessions {sorted(event.source_accessions)!r} do not match cited span accessions {sorted(cited_accessions)!r}"
            )
        for ref in event.source_refs:
            try:
                published_on = _parse_date(span_map[ref].get("published_on"), f"span {ref}.published_on")
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if published_on > analysis_date:
                errors.append(
                    f"{event.event_id}: cited source {ref} was published after workspace cutoff"
                )

    if not errors:
        try:
            graph = build_debt_lineage_graph(ledger, events)
            warnings.extend(graph.warnings)
        except ValueError as exc:
            errors.append(str(exc))

    return DebtLineageSourceValidation(
        valid=not errors,
        events=events if not errors else (),
        errors=tuple(dict.fromkeys(errors)),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def debt_lineage_graph_to_dict(graph: DebtLineageGraph) -> dict[str, Any]:
    def convert(value: Any) -> Any:
        if isinstance(value, date):
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

    payload = convert(graph)
    payload["semantics"] = {
        "graph_model": "instrument -> event -> instrument hyperedge projection",
        "candidate_events_are_confirmed": False,
        "accounting_treatment_is_auto_inferred": False,
        "participant_amount_meaning": "principal participating in the event, not necessarily full instrument outstanding",
        "snapshot_principal_is_event_ceiling_only_on_same_date": True,
    }
    return payload


def debt_lineage_event_template(ledger: DebtInstrumentLedger) -> dict[str, Any]:
    stable_ids = sorted({version.stable_id for version in ledger.versions})
    return {
        "events": [
            {
                "event_id": "",
                "effective_date": "YYYY-MM-DD",
                "event_type": "exchange",
                "status": "candidate",
                "predecessors": [{"stable_id": "", "amount": None, "source_refs": []}],
                "successors": [{"stable_id": "", "amount": None, "source_refs": []}],
                "source_refs": [],
                "source_accessions": [],
                "cash_paid": None,
                "fees_paid": None,
                "equity_issued_shares": None,
                "equity_issued_value": None,
                "accounting": {
                    "treatment": "undetermined",
                    "standard": None,
                    "basis": "undetermined",
                    "quantitative_test_pct": None,
                    "source_refs": [],
                    "notes": [],
                },
                "notes": [],
            }
        ],
        "allowed_event_types": sorted(LINEAGE_EVENT_TYPES),
        "allowed_statuses": sorted(LINEAGE_STATUSES),
        "allowed_accounting_treatments": sorted(ACCOUNTING_TREATMENTS),
        "available_stable_ids": stable_ids,
        "guardrails": [
            "Do not merge old and new CUSIPs merely to force continuity; represent an exchange as an explicit event between distinct stable IDs.",
            "Use participant amount for the principal actually tendered/exchanged/redeemed when an event is partial.",
            "For exchange/refinancing/conversion, leave unchanged residual debt outside the event instead of repeating the same stable ID on both sides.",
            "A snapshot observed on another date is context, not a hard ceiling on source-backed event principal; date mismatch remains unresolved.",
            "Do not classify modification versus extinguishment from labels alone; accounting treatment requires explicit source-backed basis.",
            "Candidate events are research leads only and must not be treated as confirmed lineage.",
        ],
    }

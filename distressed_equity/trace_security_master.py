from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
from typing import Iterable, Literal


EventKind = Literal["add", "delete", "change", "snapshot"]


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _symbol(value: str | None) -> str | None:
    clean = str(value or "").strip().upper()
    return clean or None


def _cusip(value: str | None) -> str | None:
    clean = re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()
    if not clean:
        return None
    if len(clean) != 9:
        raise ValueError(f"TRACE security identity CUSIP must contain 9 alphanumeric characters: {value!r}")
    return clean


def _parse_date(value: str | None, *, field: str, source: str, row: int) -> date:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"TRACE security identity {source} row {row}: {field} is required")
    try:
        if re.fullmatch(r"\d{8}", clean):
            return date(int(clean[:4]), int(clean[4:6]), int(clean[6:8]))
        if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", clean):
            month, day, year = (int(part) for part in clean.split("/"))
            return date(year, month, day)
        return date.fromisoformat(clean[:10])
    except ValueError as exc:
        raise ValueError(
            f"TRACE security identity {source} row {row}: {field} must be YYYYMMDD, YYYY-MM-DD, or MM/DD/YYYY"
        ) from exc


def _lookup(fieldnames: Iterable[str], aliases: Iterable[str]) -> str | None:
    normalized = {_key(name): name for name in fieldnames if name}
    for alias in aliases:
        actual = normalized.get(_key(alias))
        if actual is not None:
            return actual
    return None


def _value(raw: dict[str, str | None], field: str | None) -> str:
    if field is None:
        return ""
    return str(raw.get(field) or "").strip()


def _first_nonempty(
    raw: dict[str, str | None],
    choices: Iterable[tuple[str | None, str]],
) -> tuple[str, str] | None:
    for field, basis in choices:
        value = _value(raw, field)
        if value:
            return value, basis
    return None


def _dict_reader(path: Path) -> csv.DictReader:
    handle = path.open("r", encoding="utf-8-sig", newline="")
    sample = handle.read(4096)
    handle.seek(0)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",|\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(handle, dialect=dialect)
    reader._trace_identity_handle = handle  # type: ignore[attr-defined]
    return reader


@dataclass(frozen=True)
class TraceSecurityEvent:
    kind: EventKind
    effective_date: date
    old_symbol: str | None
    old_cusip: str | None
    new_symbol: str | None
    new_cusip: str | None
    source_ref: str
    date_basis: str = "source_effective_date"


@dataclass(frozen=True)
class TraceSecurityIdentityInterval:
    trace_symbol: str
    cusip: str
    effective_from: date
    effective_to: date | None
    source_refs: tuple[str, ...]

    def contains(self, as_of: date) -> bool:
        return self.effective_from <= as_of and (self.effective_to is None or as_of < self.effective_to)


class TraceSecurityMasterResolver:
    """Point-in-time TRACE Symbol -> CUSIP resolver.

    It never performs issuer/name/coupon/maturity matching. A mapping exists only
    when a supplied FINRA-derived event/snapshot explicitly binds the symbol and
    CUSIP for the requested date.
    """

    def __init__(self, intervals: Iterable[TraceSecurityIdentityInterval]) -> None:
        by_symbol: dict[str, list[TraceSecurityIdentityInterval]] = {}
        for interval in intervals:
            by_symbol.setdefault(interval.trace_symbol, []).append(interval)
        for symbol, rows in by_symbol.items():
            rows.sort(key=lambda item: (item.effective_from, item.effective_to or date.max, item.cusip))
            for previous, current in zip(rows, rows[1:]):
                previous_end = previous.effective_to or date.max
                if current.effective_from < previous_end:
                    raise ValueError(
                        f"TRACE security identity intervals overlap for {symbol}: "
                        f"{previous.cusip} and {current.cusip}"
                    )
        self._by_symbol = {symbol: tuple(rows) for symbol, rows in by_symbol.items()}

    @property
    def interval_count(self) -> int:
        return sum(len(rows) for rows in self._by_symbol.values())

    def resolve(self, trace_symbol: str, as_of: date) -> TraceSecurityIdentityInterval | None:
        symbol = _symbol(trace_symbol)
        if symbol is None:
            return None
        matches = [row for row in self._by_symbol.get(symbol, ()) if row.contains(as_of)]
        if not matches:
            return None
        cusips = {row.cusip for row in matches}
        if len(cusips) != 1:
            raise ValueError(
                f"TRACE Symbol {symbol} maps to multiple CUSIPs on {as_of.isoformat()}: {sorted(cusips)}"
            )
        return matches[0]


def _event_kind(value: str) -> EventKind:
    normalized = _key(value)
    if normalized in {"sa", "securityaddition", "addition", "add"} or "addition" in normalized:
        return "add"
    if normalized in {"sd", "securitydelete", "securitydeletion", "deletion", "delete"} or "delet" in normalized:
        return "delete"
    if normalized in {"sc", "securitychange", "change"} or "change" in normalized:
        return "change"
    raise ValueError(f"unsupported TRACE Daily List security event type: {value!r}")


def read_trace_daily_list_events(paths: Iterable[str | Path]) -> tuple[TraceSecurityEvent, ...]:
    """Read FINRA TRACE Corporate/Agency Daily List security exports.

    Date semantics deliberately follow the FINRA field definitions:
    - additions use Trade Report Effective Date;
    - deletions use Effective Date;
    - changes use Update Date, falling back to DL Date, because Old/New Trade
      Report Effective Date describe trade-report eligibility rather than the
      date the Symbol/CUSIP change became observable in the Data Master.

    The change boundary is therefore an availability boundary, not a claim that
    the legal/security-reference change necessarily became effective earlier.
    """

    events: list[TraceSecurityEvent] = []
    for raw_path in paths:
        path = Path(raw_path)
        reader = _dict_reader(path)
        handle = reader._trace_identity_handle  # type: ignore[attr-defined]
        try:
            fields = reader.fieldnames or []
            event_field = _lookup(fields, (
                "Daily List Event Type", "DAILY_LIST_EVENT_CD", "Event Type", "Category", "Daily List Event Code"
            ))
            if event_field is None:
                raise ValueError(f"TRACE Daily List {path.name}: event/category column is required")

            add_effective_field = _lookup(fields, (
                "Trade Report Effective Date", "TRD_RPT_EFCTV_DT"
            ))
            delete_effective_field = _lookup(fields, (
                "Effective Date", "Deletion Effective Date", "Effective Date of Deletion", "EFCTV_DT"
            ))
            update_date_field = _lookup(fields, ("Update Date", "UPDATE_DT"))
            dl_date_field = _lookup(fields, ("DL Date", "Daily List Date", "DAILY_LIST_DT"))

            old_symbol_field = _lookup(fields, ("Old Symbol", "OLD_SYM_CD", "Old TRACE Symbol"))
            new_symbol_field = _lookup(fields, ("New Symbol", "NEW_SYM_CD", "New TRACE Symbol"))
            symbol_field = _lookup(fields, ("Symbol", "SYM_CD", "TRACE Symbol"))
            old_cusip_field = _lookup(fields, ("Old CUSIP", "OLD_CUSIP"))
            new_cusip_field = _lookup(fields, ("New CUSIP", "NEW_CUSIP"))
            cusip_field = _lookup(fields, ("CUSIP",))

            for row_number, raw in enumerate(reader, 2):
                if not any(str(value or "").strip() for value in raw.values()):
                    continue
                kind = _event_kind(_value(raw, event_field))

                if kind == "add":
                    chosen_date = _first_nonempty(raw, (
                        (add_effective_field, "trade_report_effective_date"),
                    ))
                elif kind == "delete":
                    chosen_date = _first_nonempty(raw, (
                        (delete_effective_field, "deletion_effective_date"),
                    ))
                else:
                    chosen_date = _first_nonempty(raw, (
                        (update_date_field, "update_date"),
                        (dl_date_field, "daily_list_date_availability_fallback"),
                    ))
                if chosen_date is None:
                    expected = {
                        "add": "Trade Report Effective Date",
                        "delete": "Effective Date",
                        "change": "Update Date or DL Date",
                    }[kind]
                    raise ValueError(
                        f"TRACE Daily List {path.name} row {row_number}: {expected} is required for {kind} identity timing"
                    )
                raw_date, date_basis = chosen_date
                effective = _parse_date(
                    raw_date,
                    field=date_basis,
                    source=path.name,
                    row=row_number,
                )
                source_ref = f"{path.name}:row{row_number}:{date_basis}"

                if kind == "change":
                    old_symbol = _symbol(_value(raw, old_symbol_field) or _value(raw, symbol_field))
                    new_symbol = _symbol(_value(raw, new_symbol_field) or _value(raw, symbol_field))
                    old_cusip = _cusip(_value(raw, old_cusip_field) or _value(raw, cusip_field))
                    new_cusip = _cusip(_value(raw, new_cusip_field) or _value(raw, cusip_field))
                    if not old_symbol and not new_symbol:
                        continue
                    events.append(
                        TraceSecurityEvent(
                            kind, effective, old_symbol, old_cusip, new_symbol, new_cusip, source_ref, date_basis
                        )
                    )
                    continue

                symbol = _symbol(_value(raw, symbol_field))
                cusip = _cusip(_value(raw, cusip_field))
                if symbol is None:
                    continue
                if kind == "add" and cusip is None:
                    # A CUSIP-unlicensed Daily List cannot establish identity.
                    continue
                if kind == "add":
                    events.append(
                        TraceSecurityEvent(kind, effective, None, None, symbol, cusip, source_ref, date_basis)
                    )
                else:
                    events.append(
                        TraceSecurityEvent(kind, effective, symbol, cusip, None, None, source_ref, date_basis)
                    )
        finally:
            handle.close()
    return tuple(events)


def read_trace_security_master_snapshot(
    paths: Iterable[str | Path],
    *,
    as_of: date,
) -> tuple[TraceSecurityEvent, ...]:
    """Read one or more CUSIP-licensed security-master snapshots.

    The caller-supplied `as_of` is the earliest date on which the snapshot may be
    used. It is deliberately not backfilled to earlier transactions.
    """

    events: list[TraceSecurityEvent] = []
    for raw_path in paths:
        path = Path(raw_path)
        reader = _dict_reader(path)
        handle = reader._trace_identity_handle  # type: ignore[attr-defined]
        try:
            fields = reader.fieldnames or []
            symbol_field = _lookup(fields, ("Symbol", "SYM_CD", "TRACE Symbol"))
            cusip_field = _lookup(fields, ("CUSIP",))
            if symbol_field is None or cusip_field is None:
                raise ValueError(f"TRACE Security Master {path.name}: Symbol and CUSIP columns are required")
            for row_number, raw in enumerate(reader, 2):
                symbol = _symbol(_value(raw, symbol_field))
                if symbol is None:
                    continue
                cusip = _cusip(_value(raw, cusip_field))
                if cusip is None:
                    raise ValueError(
                        f"TRACE Security Master {path.name} row {row_number}: CUSIP is blank; use a CUSIP-licensed export"
                    )
                events.append(
                    TraceSecurityEvent(
                        "snapshot",
                        as_of,
                        None,
                        None,
                        symbol,
                        cusip,
                        f"{path.name}:row{row_number}:security_master_as_of",
                        "security_master_as_of",
                    )
                )
        finally:
            handle.close()
    return tuple(events)


def build_trace_security_master_resolver(
    events: Iterable[TraceSecurityEvent],
) -> TraceSecurityMasterResolver:
    """Convert source-backed identity events into non-overlapping intervals.

    Daily List events are applied in source/input order before Security Master
    snapshots on the same date. This preserves transition chains such as
    A->B->C and prevents a same-day snapshot from reopening a symbol that the
    Daily List already closed, unless a later explicit Daily List start reopened
    that symbol first. A later source reconfirming an unchanged Symbol/CUSIP
    splits provenance at that source's availability date rather than attaching
    future evidence retroactively.
    """

    by_date: dict[date, list[TraceSecurityEvent]] = {}
    for event in events:
        by_date.setdefault(event.effective_date, []).append(event)

    # symbol -> (cusip, start, source refs available from start onward)
    active: dict[str, tuple[str, date, tuple[str, ...]]] = {}
    intervals: list[TraceSecurityIdentityInterval] = []

    def close_symbol(symbol: str, expected_cusip: str | None, effective_date: date) -> None:
        current = active.get(symbol)
        if current is None:
            return
        current_cusip, started, refs = current
        if expected_cusip is not None and current_cusip != expected_cusip:
            raise ValueError(
                f"TRACE Daily List close for {symbol} on {effective_date.isoformat()} names "
                f"{expected_cusip} but active mapping is {current_cusip}"
            )
        if effective_date > started:
            intervals.append(
                TraceSecurityIdentityInterval(symbol, current_cusip, started, effective_date, refs)
            )
        active.pop(symbol, None)

    def start_symbol(symbol: str, cusip: str, source_ref: str, effective_date: date) -> None:
        current = active.get(symbol)
        if current is None:
            active[symbol] = (cusip, effective_date, (source_ref,))
            return

        current_cusip, started, current_refs = current
        if current_cusip != cusip:
            raise ValueError(
                f"conflicting TRACE symbol/CUSIP starts for {symbol} on {effective_date.isoformat()}: "
                f"{current_cusip} vs {cusip}; explicit close/change event is required"
            )

        # A new source can confirm an identity already active. Do not attach that
        # future evidence to the earlier interval. Split at its availability date
        # and carry both prior and newly available provenance only from then on.
        if effective_date > started:
            intervals.append(
                TraceSecurityIdentityInterval(symbol, current_cusip, started, effective_date, current_refs)
            )
            active[symbol] = (
                current_cusip,
                effective_date,
                tuple(dict.fromkeys((*current_refs, source_ref))),
            )
            return

        # Multiple same-day sources for the same identity have the same daily
        # availability boundary and can share provenance without backfilling.
        active[symbol] = (
            current_cusip,
            started,
            tuple(dict.fromkeys((*current_refs, source_ref))),
        )

    for effective_date in sorted(by_date):
        day_events = by_date[effective_date]
        # Security Master snapshots are snapshot-state evidence. Apply explicit
        # Daily List transitions first so a stale same-day snapshot cannot undo a
        # deletion/change that already closed the symbol on that date.
        ordered_events = tuple(event for event in day_events if event.kind != "snapshot") + tuple(
            event for event in day_events if event.kind == "snapshot"
        )
        closed_symbols: set[str] = set()
        for event in ordered_events:
            if event.kind in {"delete", "change"} and event.old_symbol:
                close_symbol(event.old_symbol, event.old_cusip, effective_date)
                closed_symbols.add(event.old_symbol)
            if event.kind in {"add", "change", "snapshot"} and event.new_symbol and event.new_cusip:
                if event.kind == "snapshot" and event.new_symbol in closed_symbols and event.new_symbol not in active:
                    raise ValueError(
                        f"TRACE Security Master snapshot on {effective_date.isoformat()} reopens symbol "
                        f"{event.new_symbol} after a same-day Daily List close; sources are contradictory"
                    )
                start_symbol(event.new_symbol, event.new_cusip, event.source_ref, effective_date)
                if event.kind != "snapshot":
                    # A later explicit Daily List start can intentionally reopen a
                    # symbol; snapshots may then corroborate that active state.
                    closed_symbols.discard(event.new_symbol)

    for symbol, (cusip, started, refs) in active.items():
        intervals.append(TraceSecurityIdentityInterval(symbol, cusip, started, None, refs))

    # Preserve provenance boundaries. Adjacent intervals are coalesced only when
    # both identity and evidence set are exactly unchanged.
    coalesced: list[TraceSecurityIdentityInterval] = []
    for row in sorted(intervals, key=lambda item: (item.trace_symbol, item.effective_from, item.effective_to or date.max)):
        if coalesced:
            previous = coalesced[-1]
            if (
                previous.trace_symbol == row.trace_symbol
                and previous.cusip == row.cusip
                and previous.source_refs == row.source_refs
                and previous.effective_to == row.effective_from
            ):
                coalesced[-1] = TraceSecurityIdentityInterval(
                    previous.trace_symbol,
                    previous.cusip,
                    previous.effective_from,
                    row.effective_to,
                    previous.source_refs,
                )
                continue
        coalesced.append(row)
    return TraceSecurityMasterResolver(coalesced)

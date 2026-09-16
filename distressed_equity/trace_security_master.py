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

    Supported layouts include user-guide/UI-style column names and compact API
    aliases. Effective dates are mandatory; Daily List publication date is never
    substituted for identity effectiveness.
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
            effective_field = _lookup(fields, (
                "Effective Date", "EFCTV_DT", "Trade Report Effective Date", "New Trade Report Effective Date"
            ))
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
                effective = _parse_date(
                    _value(raw, effective_field),
                    field="Effective Date",
                    source=path.name,
                    row=row_number,
                )
                source_ref = f"{path.name}:row{row_number}"
                if kind == "change":
                    old_symbol = _symbol(_value(raw, old_symbol_field) or _value(raw, symbol_field))
                    new_symbol = _symbol(_value(raw, new_symbol_field) or _value(raw, symbol_field))
                    old_cusip = _cusip(_value(raw, old_cusip_field) or _value(raw, cusip_field))
                    new_cusip = _cusip(_value(raw, new_cusip_field) or _value(raw, cusip_field))
                    if not old_symbol and not new_symbol:
                        continue
                    events.append(
                        TraceSecurityEvent(kind, effective, old_symbol, old_cusip, new_symbol, new_cusip, source_ref)
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
                    events.append(TraceSecurityEvent(kind, effective, None, None, symbol, cusip, source_ref))
                else:
                    events.append(TraceSecurityEvent(kind, effective, symbol, cusip, None, None, source_ref))
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
                    TraceSecurityEvent("snapshot", as_of, None, None, symbol, cusip, f"{path.name}:row{row_number}")
                )
        finally:
            handle.close()
    return tuple(events)


def build_trace_security_master_resolver(
    events: Iterable[TraceSecurityEvent],
) -> TraceSecurityMasterResolver:
    """Convert source-backed identity events into non-overlapping intervals."""

    by_date: dict[date, list[TraceSecurityEvent]] = {}
    for event in events:
        by_date.setdefault(event.effective_date, []).append(event)

    # symbol -> (cusip, start, source refs)
    active: dict[str, tuple[str, date, tuple[str, ...]]] = {}
    intervals: list[TraceSecurityIdentityInterval] = []

    for effective_date in sorted(by_date):
        day_events = sorted(by_date[effective_date], key=lambda item: item.source_ref)
        ends: dict[str, list[tuple[str | None, str]]] = {}
        starts: dict[str, list[tuple[str, str]]] = {}
        for event in day_events:
            if event.kind in {"delete", "change"} and event.old_symbol:
                ends.setdefault(event.old_symbol, []).append((event.old_cusip, event.source_ref))
            if event.kind in {"add", "change", "snapshot"} and event.new_symbol and event.new_cusip:
                starts.setdefault(event.new_symbol, []).append((event.new_cusip, event.source_ref))

        for symbol, entries in ends.items():
            explicit_cusips = {cusip for cusip, _ in entries if cusip is not None}
            if len(explicit_cusips) > 1:
                raise ValueError(
                    f"conflicting TRACE Daily List old CUSIPs for {symbol} on {effective_date.isoformat()}: "
                    f"{sorted(explicit_cusips)}"
                )
            current = active.get(symbol)
            if current is None:
                continue
            current_cusip, started, refs = current
            if explicit_cusips and current_cusip not in explicit_cusips:
                raise ValueError(
                    f"TRACE Daily List close for {symbol} on {effective_date.isoformat()} names "
                    f"{sorted(explicit_cusips)} but active mapping is {current_cusip}"
                )
            if effective_date > started:
                intervals.append(
                    TraceSecurityIdentityInterval(symbol, current_cusip, started, effective_date, refs)
                )
            active.pop(symbol, None)

        for symbol, entries in starts.items():
            cusips = {cusip for cusip, _ in entries}
            if len(cusips) != 1:
                raise ValueError(
                    f"conflicting TRACE symbol/CUSIP starts for {symbol} on {effective_date.isoformat()}: {sorted(cusips)}"
                )
            cusip = next(iter(cusips))
            refs = tuple(sorted({ref for _, ref in entries}))
            current = active.get(symbol)
            if current is not None:
                current_cusip, started, current_refs = current
                if current_cusip != cusip:
                    raise ValueError(
                        f"TRACE Symbol {symbol} changes from {current_cusip} to {cusip} on "
                        f"{effective_date.isoformat()} without an explicit close/change event"
                    )
                active[symbol] = (current_cusip, started, tuple(sorted(set(current_refs) | set(refs))))
                continue
            active[symbol] = (cusip, effective_date, refs)

    for symbol, (cusip, started, refs) in active.items():
        intervals.append(TraceSecurityIdentityInterval(symbol, cusip, started, None, refs))

    # Coalesce adjacent intervals only when the source-backed identity is unchanged.
    coalesced: list[TraceSecurityIdentityInterval] = []
    for row in sorted(intervals, key=lambda item: (item.trace_symbol, item.effective_from, item.effective_to or date.max)):
        if coalesced:
            previous = coalesced[-1]
            if (
                previous.trace_symbol == row.trace_symbol
                and previous.cusip == row.cusip
                and previous.effective_to == row.effective_from
            ):
                coalesced[-1] = TraceSecurityIdentityInterval(
                    previous.trace_symbol,
                    previous.cusip,
                    previous.effective_from,
                    row.effective_to,
                    tuple(sorted(set(previous.source_refs) | set(row.source_refs))),
                )
                continue
        coalesced.append(row)
    return TraceSecurityMasterResolver(coalesced)

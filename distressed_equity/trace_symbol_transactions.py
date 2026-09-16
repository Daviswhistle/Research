from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .trace_security_master import TraceSecurityMasterResolver
from .trace_transactions import (
    TraceTransaction,
    _decimal,
    _field_lookup,
    _identifier,
    _parse_date,
    _parse_time,
    _signed_yield,
    _value,
)


@dataclass(frozen=True)
class TraceIdentityReadResult:
    transactions: tuple[TraceTransaction, ...]
    symbol_resolved_record_count: int
    raw_cusip_record_count: int
    resolver_interval_count: int


def read_trace_enhanced_files_with_security_master(
    paths: Iterable[str | Path],
    resolver: TraceSecurityMasterResolver,
) -> TraceIdentityReadResult:
    """Read TRACE Enhanced Historical data with point-in-time symbol resolution.

    Raw CUSIP remains authoritative only when it does not conflict with the
    supplied FINRA-derived point-in-time symbol map. A missing CUSIP is filled
    only from an exact TRACE Symbol interval covering the execution date.
    """

    transactions: list[TraceTransaction] = []
    symbol_resolved = 0
    raw_cusip = 0
    for raw_path in paths:
        path = Path(raw_path)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="|")
            if not reader.fieldnames:
                raise ValueError(f"TRACE {path.name}: missing header row")
            lookup = _field_lookup(reader.fieldnames)
            required_fields = {
                "reference_number",
                "trade_status",
                "quantity",
                "price",
                "execution_date",
                "execution_time",
                "trade_report_date",
                "trade_report_time",
                "dissemination_flag",
            }
            missing = sorted(required_fields - set(lookup))
            if missing:
                raise ValueError(
                    f"TRACE {path.name}: missing required official fields: {', '.join(missing)}"
                )
            if "cusip" not in lookup and "trace_symbol" not in lookup:
                raise ValueError(
                    f"TRACE {path.name}: CUSIP or TRACE Symbol is required for security identity"
                )

            for row_number, raw in enumerate(reader, 2):
                status = _value(raw, lookup, "trade_status").upper()
                if not status:
                    first = str(next(iter(raw.values()), "") or "").strip()
                    if first and first.isdigit():
                        continue
                    if not any(str(item or "").strip() for item in raw.values()):
                        continue
                    raise ValueError(f"TRACE {path.name} row {row_number}: Trade Status is required")
                if status not in {"T", "X", "C", "R", "Y"}:
                    raise ValueError(f"TRACE {path.name} row {row_number}: unsupported Trade Status {status!r}")

                execution_date = _parse_date(
                    _value(raw, lookup, "execution_date"),
                    field="Execution Date",
                    source=path.name,
                    row=row_number,
                )
                trade_report_date = _parse_date(
                    _value(raw, lookup, "trade_report_date"),
                    field="Trade Report Date",
                    source=path.name,
                    row=row_number,
                )
                assert execution_date is not None and trade_report_date is not None

                trace_symbol = (_value(raw, lookup, "trace_symbol") or "").strip().upper() or None
                row_cusip = _identifier(_value(raw, lookup, "cusip"))
                mapped = resolver.resolve(trace_symbol, execution_date) if trace_symbol else None
                if row_cusip:
                    raw_cusip += 1
                    if mapped is not None and mapped.cusip != row_cusip:
                        raise ValueError(
                            f"TRACE {path.name} row {row_number}: raw CUSIP {row_cusip} conflicts with "
                            f"point-in-time TRACE Symbol {trace_symbol} -> {mapped.cusip} on {execution_date.isoformat()}"
                        )
                    cusip = row_cusip
                else:
                    if trace_symbol is None:
                        raise ValueError(
                            f"TRACE {path.name} row {row_number}: CUSIP is blank and TRACE Symbol is unavailable"
                        )
                    if mapped is None:
                        raise ValueError(
                            f"TRACE {path.name} row {row_number}: CUSIP is blank and no source-backed point-in-time "
                            f"mapping exists for TRACE Symbol {trace_symbol} on {execution_date.isoformat()}"
                        )
                    cusip = mapped.cusip
                    symbol_resolved += 1

                quantity = _decimal(
                    _value(raw, lookup, "quantity"), field="Quantity", source=path.name, row=row_number
                )
                price = _decimal(
                    _value(raw, lookup, "price"), field="Price", source=path.name, row=row_number
                )
                assert quantity is not None and price is not None
                if quantity <= 0:
                    raise ValueError(f"TRACE {path.name} row {row_number}: Quantity must be positive")
                if price < 0:
                    raise ValueError(f"TRACE {path.name} row {row_number}: Price cannot be negative")

                reference = _value(raw, lookup, "reference_number")
                if not reference:
                    raise ValueError(f"TRACE {path.name} row {row_number}: Reference Number is required")
                transactions.append(
                    TraceTransaction(
                        source_file=path.name,
                        source_row=row_number,
                        reference_number=reference,
                        trade_status=status,
                        trace_symbol=trace_symbol,
                        cusip=cusip,
                        quantity=quantity,
                        price=price,
                        yield_pct=_signed_yield(
                            _value(raw, lookup, "yield"),
                            _value(raw, lookup, "yield_direction"),
                            source=path.name,
                            row=row_number,
                        ),
                        as_of_indicator=_value(raw, lookup, "as_of_indicator").upper() or None,
                        execution_date=execution_date,
                        execution_time=_parse_time(
                            _value(raw, lookup, "execution_time"),
                            field="Execution Time",
                            source=path.name,
                            row=row_number,
                        ),
                        trade_report_date=trade_report_date,
                        trade_report_time=_parse_time(
                            _value(raw, lookup, "trade_report_time"),
                            field="Trade Report Time",
                            source=path.name,
                            row=row_number,
                        ),
                        trade_modifier_3=_value(raw, lookup, "trade_modifier_3").upper() or None,
                        trade_modifier_4=_value(raw, lookup, "trade_modifier_4").upper() or None,
                        buy_sell_indicator=_value(raw, lookup, "buy_sell_indicator").upper() or None,
                        contra_party_indicator=_value(raw, lookup, "contra_party_indicator").upper() or None,
                        special_price_indicator=_value(raw, lookup, "special_price_indicator").upper() or None,
                        trading_market_indicator=_value(raw, lookup, "trading_market_indicator").upper() or None,
                        dissemination_flag=_value(raw, lookup, "dissemination_flag").upper() or None,
                        prior_trade_report_date=_parse_date(
                            _value(raw, lookup, "prior_trade_report_date"),
                            field="Prior Trade Report Date",
                            source=path.name,
                            row=row_number,
                            required=False,
                        ),
                        prior_reference_number=_value(raw, lookup, "prior_reference_number") or None,
                    )
                )

    return TraceIdentityReadResult(
        transactions=tuple(transactions),
        symbol_resolved_record_count=symbol_resolved,
        raw_cusip_record_count=raw_cusip,
        resolver_interval_count=resolver.interval_count,
    )

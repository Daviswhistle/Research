from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from typing import Iterable, Literal

from .bond_market import BondMarketObservation


AggregationPolicy = Literal["vwap", "last"]
UnresolvedCorrectionPolicy = Literal["error", "warn"]
DisseminationPolicy = Literal["disseminated", "all"]


@dataclass(frozen=True)
class TraceTransaction:
    source_file: str
    source_row: int
    reference_number: str
    trade_status: str
    trace_symbol: str | None
    cusip: str
    quantity: Decimal
    price: Decimal
    yield_pct: Decimal | None
    as_of_indicator: str | None
    execution_date: date
    execution_time: str
    trade_report_date: date
    trade_report_time: str
    trade_modifier_3: str | None
    trade_modifier_4: str | None
    buy_sell_indicator: str | None
    contra_party_indicator: str | None
    special_price_indicator: str | None
    trading_market_indicator: str | None
    dissemination_flag: str | None
    prior_trade_report_date: date | None
    prior_reference_number: str | None

    @property
    def report_key(self) -> tuple[date, str]:
        return self.trade_report_date, self.reference_number

    @property
    def reversal_fingerprint(self) -> tuple[object, ...]:
        return (
            self.cusip,
            self.quantity,
            self.price,
            self.execution_date,
            self.execution_time,
            self.buy_sell_indicator,
            self.contra_party_indicator,
        )

    @property
    def semantic_key(self) -> tuple[object, ...]:
        """Report semantics excluding source-file/row provenance.

        FINRA users can legitimately supply overlapping daily/monthly extracts.
        Repeated bytes from two files must not double volume, while different
        economics under one report key remain a hard conflict in lifecycle logic.
        """

        return (
            self.reference_number,
            self.trade_status,
            self.trace_symbol,
            self.cusip,
            self.quantity,
            self.price,
            self.yield_pct,
            self.as_of_indicator,
            self.execution_date,
            self.execution_time,
            self.trade_report_date,
            self.trade_report_time,
            self.trade_modifier_3,
            self.trade_modifier_4,
            self.buy_sell_indicator,
            self.contra_party_indicator,
            self.special_price_indicator,
            self.trading_market_indicator,
            self.dissemination_flag,
            self.prior_trade_report_date,
            self.prior_reference_number,
        )


@dataclass(frozen=True)
class TraceAggregationConfig:
    aggregation: AggregationPolicy = "vwap"
    dissemination_policy: DisseminationPolicy = "disseminated"
    unresolved_correction_policy: UnresolvedCorrectionPolicy = "error"
    include_weighted_average_price: bool = False
    include_special_price: bool = False
    secondary_market_only: bool = True

    def __post_init__(self) -> None:
        if self.aggregation not in {"vwap", "last"}:
            raise ValueError("TRACE aggregation must be 'vwap' or 'last'")
        if self.dissemination_policy not in {"disseminated", "all"}:
            raise ValueError("TRACE dissemination_policy must be 'disseminated' or 'all'")
        if self.unresolved_correction_policy not in {"error", "warn"}:
            raise ValueError("TRACE unresolved_correction_policy must be 'error' or 'warn'")


@dataclass(frozen=True)
class TraceDailyObservation:
    observation: BondMarketObservation
    trade_count: int
    latest_execution_time: str
    aggregation_policy: str


@dataclass(frozen=True)
class TraceNormalizationResult:
    observations: tuple[TraceDailyObservation, ...]
    parsed_record_count: int
    exact_duplicate_record_count: int
    active_record_count: int
    included_record_count: int
    cancelled_record_count: int
    unresolved_correction_count: int
    warnings: tuple[str, ...]


_CANONICAL_FIELDS: dict[str, tuple[str, ...]] = {
    "reference_number": ("reference number", "reference_number", "referencenumber"),
    "trade_status": ("trade status", "trade_status", "tradestatus"),
    "trace_symbol": ("trace symbol", "trace_symbol", "tracesymbol"),
    "cusip": ("cusip",),
    "quantity": ("quantity",),
    "price": ("price",),
    "yield_direction": ("yield direction", "yield_direction", "yielddirection"),
    "yield": ("yield",),
    "as_of_indicator": ("as of indicator", "as_of_indicator", "asofindicator"),
    "execution_date": ("execution date", "execution_date", "executiondate"),
    "execution_time": ("execution time", "execution_time", "executiontime"),
    "trade_report_date": ("trade report date", "trade_report_date", "tradereportdate"),
    "trade_report_time": ("trade report time", "trade_report_time", "tradereporttime"),
    "trade_modifier_3": ("trade modifier 3", "trade_modifier_3", "trademodifier3"),
    "trade_modifier_4": ("trade modifier 4", "trade_modifier_4", "trademodifier4"),
    "buy_sell_indicator": ("buy/sell indicator", "buy sell indicator", "buy_sell_indicator", "buysellindicator"),
    "contra_party_indicator": ("contra party indicator", "contra_party_indicator", "contrapartyindicator"),
    "special_price_indicator": ("special price indicator", "special_price_indicator", "specialpriceindicator"),
    "trading_market_indicator": ("trading market indicator", "trading_market_indicator", "tradingmarketindicator"),
    "dissemination_flag": ("dissemination flag", "dissemination_flag", "disseminationflag"),
    "prior_trade_report_date": ("prior trade report date", "prior_trade_report_date", "priortradereportdate"),
    "prior_reference_number": ("prior reference number", "prior_reference_number", "priorreferencenumber"),
}


def _header_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _field_lookup(fieldnames: Iterable[str]) -> dict[str, str]:
    normalized = {_header_key(name): name for name in fieldnames if name}
    output: dict[str, str] = {}
    for canonical, aliases in _CANONICAL_FIELDS.items():
        for alias in aliases:
            actual = normalized.get(_header_key(alias))
            if actual is not None:
                output[canonical] = actual
                break
    return output


def _value(raw: dict[str, str | None], lookup: dict[str, str], field: str) -> str:
    actual = lookup.get(field)
    if actual is None:
        return ""
    return str(raw.get(actual) or "").strip()


def _identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", value).upper()


def _decimal(value: str, *, field: str, source: str, row: int, required: bool = True) -> Decimal | None:
    clean = value.strip().replace(",", "")
    if not clean:
        if required:
            raise ValueError(f"TRACE {source} row {row}: {field} is required")
        return None
    try:
        parsed = Decimal(clean)
    except InvalidOperation as exc:
        raise ValueError(f"TRACE {source} row {row}: {field} must be numeric") from exc
    if not parsed.is_finite():
        raise ValueError(f"TRACE {source} row {row}: {field} must be finite")
    return parsed


def _parse_date(value: str, *, field: str, source: str, row: int, required: bool = True) -> date | None:
    clean = value.strip()
    if not clean:
        if required:
            raise ValueError(f"TRACE {source} row {row}: {field} is required")
        return None
    try:
        if re.fullmatch(r"\d{8}", clean):
            return date(int(clean[:4]), int(clean[4:6]), int(clean[6:8]))
        return date.fromisoformat(clean[:10])
    except ValueError as exc:
        raise ValueError(f"TRACE {source} row {row}: {field} must be YYYYMMDD or YYYY-MM-DD") from exc


def _parse_time(value: str, *, field: str, source: str, row: int) -> str:
    clean = re.sub(r"[^0-9]", "", value)
    if len(clean) != 6:
        raise ValueError(f"TRACE {source} row {row}: {field} must be HHMMSS")
    hour, minute, second = int(clean[:2]), int(clean[2:4]), int(clean[4:6])
    if hour > 23 or minute > 59 or second > 59:
        raise ValueError(f"TRACE {source} row {row}: {field} must be a valid HHMMSS time")
    return clean


def _signed_yield(value: str, direction: str, *, source: str, row: int) -> Decimal | None:
    parsed = _decimal(value, field="Yield", source=source, row=row, required=False)
    if parsed is None:
        return None
    marker = direction.strip()
    if marker not in {"", "-"}:
        raise ValueError(f"TRACE {source} row {row}: Yield Direction must be '-' or blank")
    return -abs(parsed) if marker == "-" else parsed


def read_trace_enhanced_files(paths: Iterable[str | Path]) -> tuple[TraceTransaction, ...]:
    """Read official post-2012 FINRA Enhanced Historical pipe-delimited files.

    The CUSIP-bearing version is required because downstream stable debt identity
    intentionally does not fuzzy-match TRACE symbols to instruments.
    """

    transactions: list[TraceTransaction] = []
    saw_cusip_field = False
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
                "cusip",
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
            saw_cusip_field = True
            for row_number, raw in enumerate(reader, 2):
                status = _value(raw, lookup, "trade_status").upper()
                if not status:
                    # FINRA files end with a one-field numeric trailer.
                    first = str(next(iter(raw.values()), "") or "").strip()
                    if first and first.isdigit():
                        continue
                    if not any(str(item or "").strip() for item in raw.values()):
                        continue
                    raise ValueError(f"TRACE {path.name} row {row_number}: Trade Status is required")
                if status not in {"T", "X", "C", "R", "Y"}:
                    raise ValueError(f"TRACE {path.name} row {row_number}: unsupported Trade Status {status!r}")

                cusip = _identifier(_value(raw, lookup, "cusip"))
                if not cusip:
                    raise ValueError(
                        f"TRACE {path.name} row {row_number}: CUSIP is blank; use the CUSIP-bearing historical file"
                    )
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
                transactions.append(
                    TraceTransaction(
                        source_file=path.name,
                        source_row=row_number,
                        reference_number=reference,
                        trade_status=status,
                        trace_symbol=_value(raw, lookup, "trace_symbol") or None,
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
    if not saw_cusip_field:
        raise ValueError("TRACE input did not contain a CUSIP-bearing file")
    return tuple(transactions)


def _report_order(tx: TraceTransaction) -> tuple[date, str, str, int]:
    return tx.trade_report_date, tx.trade_report_time, tx.source_file, tx.source_row


def _deduplicate_exact_reports(
    transactions: Iterable[TraceTransaction],
) -> tuple[tuple[TraceTransaction, ...], int]:
    seen: set[tuple[object, ...]] = set()
    output: list[TraceTransaction] = []
    duplicate_count = 0
    for tx in sorted(transactions, key=_report_order):
        key = tx.semantic_key
        if key in seen:
            duplicate_count += 1
            continue
        seen.add(key)
        output.append(tx)
    return tuple(output), duplicate_count


def _apply_trade_lifecycle(
    transactions: Iterable[TraceTransaction],
    *,
    unresolved_policy: UnresolvedCorrectionPolicy,
) -> tuple[tuple[TraceTransaction, ...], int, tuple[str, ...]]:
    active: dict[tuple[date, str], TraceTransaction] = {}
    cancelled_count = 0
    warnings: list[str] = []
    for tx in sorted(transactions, key=_report_order):
        is_cancel = tx.trade_status in {"X", "C", "Y"} or tx.as_of_indicator == "R"
        if not is_cancel:
            if tx.trade_status not in {"T", "R"}:
                raise ValueError(f"unsupported active TRACE Trade Status {tx.trade_status!r}")
            prior = active.get(tx.report_key)
            if prior is not None:
                raise ValueError(
                    f"conflicting TRACE reports share key {tx.trade_report_date.isoformat()}/{tx.reference_number}"
                )
            active[tx.report_key] = tx
            continue

        target_key = None
        if tx.prior_trade_report_date is not None and tx.prior_reference_number:
            target_key = (tx.prior_trade_report_date, tx.prior_reference_number)
        removed = active.pop(target_key, None) if target_key is not None else None
        if removed is None:
            # FINRA notes that reversals can be matched by the basic trade details.
            fingerprint_matches = [
                key for key, candidate in active.items()
                if candidate.reversal_fingerprint == tx.reversal_fingerprint
            ]
            if len(fingerprint_matches) == 1:
                removed = active.pop(fingerprint_matches[0])
            elif len(fingerprint_matches) > 1:
                raise ValueError(
                    f"TRACE {tx.source_file} row {tx.source_row}: reversal/cancel matches multiple active reports"
                )
        if removed is not None:
            cancelled_count += 1
            continue
        message = (
            f"TRACE {tx.source_file} row {tx.source_row}: unresolved cancel/correction/reversal "
            f"status={tx.trade_status} prior={tx.prior_trade_report_date}/{tx.prior_reference_number}"
        )
        if unresolved_policy == "error":
            raise ValueError(message)
        warnings.append(message)

    return tuple(sorted(active.values(), key=lambda item: (item.execution_date, item.execution_time, _report_order(item)))), cancelled_count, tuple(warnings)


def _included(tx: TraceTransaction, config: TraceAggregationConfig) -> bool:
    if config.dissemination_policy == "disseminated" and tx.dissemination_flag != "Y":
        return False
    if not config.include_weighted_average_price and tx.trade_modifier_4 == "W":
        return False
    if not config.include_special_price and tx.special_price_indicator == "Y":
        return False
    if config.secondary_market_only and tx.trading_market_indicator not in {None, "", "S1"}:
        return False
    return True


def _weighted_average(rows: Iterable[TraceTransaction], field: str) -> float | None:
    numerator = Decimal("0")
    denominator = Decimal("0")
    for tx in rows:
        value = getattr(tx, field)
        if value is None:
            continue
        numerator += value * tx.quantity
        denominator += tx.quantity
    if denominator == 0:
        return None
    return float(numerator / denominator)


def normalize_trace_transactions(
    transactions: Iterable[TraceTransaction],
    *,
    config: TraceAggregationConfig = TraceAggregationConfig(),
) -> TraceNormalizationResult:
    raw = tuple(transactions)
    deduplicated, duplicate_count = _deduplicate_exact_reports(raw)
    active, cancelled_count, lifecycle_warnings = _apply_trade_lifecycle(
        deduplicated,
        unresolved_policy=config.unresolved_correction_policy,
    )
    included = tuple(tx for tx in active if _included(tx, config))
    warnings = list(lifecycle_warnings)
    if duplicate_count:
        warnings.append(
            f"collapsed {duplicate_count} exact duplicate TRACE report row(s) across supplied inputs before lifecycle processing"
        )
    if config.dissemination_policy == "all":
        warnings.append(
            "non-disseminated TRACE reports are included; inter-dealer buy/sell reports may represent two reporting sides of one trade"
        )

    grouped: dict[tuple[date, str], list[TraceTransaction]] = {}
    for tx in included:
        grouped.setdefault((tx.execution_date, tx.cusip), []).append(tx)

    daily: list[TraceDailyObservation] = []
    for (execution_date, cusip), rows in sorted(grouped.items()):
        rows.sort(key=lambda item: (item.execution_time, item.trade_report_date, item.trade_report_time, item.reference_number))
        total_volume = sum((tx.quantity for tx in rows), Decimal("0"))
        if config.aggregation == "vwap":
            price = _weighted_average(rows, "price")
            yield_pct = _weighted_average(rows, "yield_pct")
        else:
            last = rows[-1]
            price = float(last.price)
            yield_pct = float(last.yield_pct) if last.yield_pct is not None else None
        source = (
            "FINRA TRACE Enhanced Historical"
            f"; aggregation={config.aggregation}"
            f"; dissemination={config.dissemination_policy}"
            f"; trades={len(rows)}"
            f"; weighted_average_price={'included' if config.include_weighted_average_price else 'excluded'}"
            f"; special_price={'included' if config.include_special_price else 'excluded'}"
        )
        daily.append(
            TraceDailyObservation(
                observation=BondMarketObservation(
                    date=execution_date,
                    cusip=cusip,
                    price_pct_par=price,
                    yield_pct=yield_pct,
                    volume=float(total_volume),
                    source=source,
                ),
                trade_count=len(rows),
                latest_execution_time=rows[-1].execution_time,
                aggregation_policy=config.aggregation,
            )
        )

    return TraceNormalizationResult(
        observations=tuple(daily),
        parsed_record_count=len(raw),
        exact_duplicate_record_count=duplicate_count,
        active_record_count=len(active),
        included_record_count=len(included),
        cancelled_record_count=cancelled_count,
        unresolved_correction_count=len(lifecycle_warnings),
        warnings=tuple(dict.fromkeys(warnings)),
    )

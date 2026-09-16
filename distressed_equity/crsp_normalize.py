from __future__ import annotations

from bisect import bisect_right
import csv
from dataclasses import asdict, dataclass
from datetime import date
import math
from pathlib import Path
import re
from typing import Iterable, Literal


ReturnSemantics = Literal["legacy", "already_total"]


_ALIASES: dict[str, tuple[str, ...]] = {
    "permno": ("PERMNO", "permno", "Permno"),
    "namedt": ("NAMEDT", "namedt", "NameDt"),
    "nameendt": ("NAMEENDT", "nameendt", "NameEndDt"),
    "ticker": ("TICKER", "ticker", "Ticker"),
    "company_name": ("COMNAM", "comnam", "CompanyName", "SecurityNm", "securitynm"),
    "share_code": ("SHRCD", "shrcd", "ShrCd", "ShareCode"),
    "exchange_code": ("EXCHCD", "exchcd", "ExchCd", "ExchangeCode"),
    "date": ("date", "DATE", "Date", "CALDT", "caldt", "DlyCalDt", "MthCalDt"),
    "price": ("PRC", "prc", "Prc", "DlyPrc", "MthPrc"),
    "return": ("RET", "ret", "Ret", "DlyRet", "MthRet"),
    "delisting_return": ("DLRET", "dlret", "DlRet"),
}


@dataclass(frozen=True)
class CrspColumnConfig:
    stocknames_permno: str | None = None
    stocknames_start: str | None = None
    stocknames_end: str | None = None
    stocknames_ticker: str | None = None
    stocknames_name: str | None = None
    stocknames_share_code: str | None = None
    stocknames_exchange_code: str | None = None
    market_permno: str | None = None
    market_date: str | None = None
    market_price: str | None = None
    market_return: str | None = None
    market_delisting_return: str | None = None
    market_shares_outstanding: str | None = None


@dataclass(frozen=True)
class CrspNormalizeConfig:
    return_semantics: ReturnSemantics
    share_codes: tuple[int, ...] | None = (10, 11)
    missing_return_codes: tuple[float, ...] = ()
    shares_outstanding_scale: float | None = None
    columns: CrspColumnConfig = CrspColumnConfig()

    def __post_init__(self) -> None:
        if self.return_semantics not in {"legacy", "already_total"}:
            raise ValueError("return_semantics must be 'legacy' or 'already_total'")
        if self.share_codes is not None:
            if not self.share_codes:
                raise ValueError("share_codes cannot be empty; use None to include all share codes")
            if len(set(self.share_codes)) != len(self.share_codes):
                raise ValueError("share_codes must be unique")
        if self.shares_outstanding_scale is not None:
            if not math.isfinite(self.shares_outstanding_scale) or self.shares_outstanding_scale <= 0:
                raise ValueError("shares_outstanding_scale must be finite and positive")
        if any(not math.isfinite(value) for value in self.missing_return_codes):
            raise ValueError("missing_return_codes must be finite")


@dataclass(frozen=True)
class CrspSecurityInterval:
    security_id: str
    permno: str
    symbol: str
    name: str
    exchange: str | None
    start_date: date
    end_date: date | None
    share_code: int | None
    exchange_code: int | None

    def contains(self, value: date) -> bool:
        return self.start_date <= value and (self.end_date is None or value <= self.end_date)


@dataclass(frozen=True)
class CrspNormalizationResult:
    stocknames_input_rows: int
    security_interval_count: int
    filtered_stocknames_rows: int
    market_input_rows: int
    output_price_rows: int
    market_rows_outside_security_intervals: int
    rows_without_usable_price: int
    negative_price_rows: int
    missing_return_rows: int
    delisting_return_rows_applied: int
    return_chain_break_count: int
    adjusted_output_rows: int
    unadjusted_barrier_rows: int
    resolved_columns: dict[str, str | None]
    warnings: tuple[str, ...]


def _header_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _resolve_column(
    fieldnames: Iterable[str],
    logical: str,
    *,
    override: str | None = None,
    required: bool = True,
) -> str | None:
    fields = tuple(name for name in fieldnames if name)
    if override:
        exact = [name for name in fields if name == override]
        if exact:
            return exact[0]
        normalized_override = _header_key(override)
        matches = [name for name in fields if _header_key(name) == normalized_override]
        if len(matches) == 1:
            return matches[0]
        raise ValueError(f"CRSP column override {override!r} for {logical} was not found uniquely")

    aliases = {_header_key(alias) for alias in _ALIASES.get(logical, ())}
    matches = [name for name in fields if _header_key(name) in aliases]
    if len(matches) > 1:
        raise ValueError(
            f"CRSP export has multiple candidate columns for {logical}: {matches}; pass an explicit column override"
        )
    if matches:
        return matches[0]
    if required:
        raise ValueError(
            f"CRSP export is missing a recognized {logical} column; pass an explicit column override"
        )
    return None


def _parse_date(value: str | None, *, field: str, row: int, required: bool = True) -> date | None:
    clean = str(value or "").strip()
    if not clean:
        if required:
            raise ValueError(f"CRSP row {row}: {field} is required")
        return None
    try:
        if re.fullmatch(r"\d{8}", clean):
            return date(int(clean[:4]), int(clean[4:6]), int(clean[6:8]))
        if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", clean):
            month, day, year = (int(part) for part in clean.split("/"))
            return date(year, month, day)
        return date.fromisoformat(clean[:10])
    except ValueError as exc:
        raise ValueError(f"CRSP row {row}: {field} must be a supported calendar date") from exc


def _integral(value: str | None, *, field: str, row: int, required: bool = True) -> int | None:
    clean = str(value or "").strip()
    if not clean:
        if required:
            raise ValueError(f"CRSP row {row}: {field} is required")
        return None
    try:
        numeric = float(clean)
    except ValueError as exc:
        raise ValueError(f"CRSP row {row}: {field} must be integral") from exc
    if not math.isfinite(numeric) or not numeric.is_integer():
        raise ValueError(f"CRSP row {row}: {field} must be integral")
    return int(numeric)


def _finite_float(value: str | None, *, field: str, row: int, required: bool = False) -> float | None:
    clean = str(value or "").strip()
    if not clean:
        if required:
            raise ValueError(f"CRSP row {row}: {field} is required")
        return None
    try:
        numeric = float(clean.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"CRSP row {row}: {field} must be numeric") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"CRSP row {row}: {field} must be finite")
    return numeric


def _return_value(
    value: str | None,
    *,
    field: str,
    row: int,
    missing_codes: set[float],
) -> float | None:
    clean = str(value or "").strip()
    if not clean or clean in {".", "NA", "N/A", "NULL", "null"}:
        return None
    try:
        numeric = float(clean)
    except ValueError:
        # CRSP-family exports can preserve alphabetic missing-value markers.
        if re.fullmatch(r"\.?[A-Za-z]+", clean):
            return None
        raise ValueError(f"CRSP row {row}: {field} must be numeric or an explicit missing marker")
    if not math.isfinite(numeric):
        raise ValueError(f"CRSP row {row}: {field} must be finite")
    if numeric in missing_codes:
        return None
    if numeric < -1.0:
        raise ValueError(
            f"CRSP row {row}: {field}={numeric} is below -1; if this is a vendor missing-value code, pass it explicitly as a missing return code"
        )
    return numeric


def _permno(value: str | None, *, row: int) -> str:
    parsed = _integral(value, field="PERMNO", row=row)
    assert parsed is not None
    if parsed <= 0:
        raise ValueError(f"CRSP row {row}: PERMNO must be positive")
    return str(parsed)


def _exchange_label(code: int | None) -> str | None:
    if code is None:
        return None
    standard = {1: "NYSE", 2: "AMEX", 3: "NASDAQ"}
    return standard.get(code, f"CRSP_EXCHCD_{code}")


def _security_id(permno: str) -> str:
    return f"CRSP_PERMNO:{permno}"


class _IntervalIndex:
    def __init__(self, intervals: Iterable[CrspSecurityInterval]) -> None:
        grouped: dict[str, list[CrspSecurityInterval]] = {}
        for interval in intervals:
            grouped.setdefault(interval.permno, []).append(interval)
        self._rows: dict[str, tuple[CrspSecurityInterval, ...]] = {}
        self._starts: dict[str, tuple[date, ...]] = {}
        for permno, rows in grouped.items():
            ordered = sorted(rows, key=lambda item: (item.start_date, item.end_date or date.max, item.symbol))
            for previous, current in zip(ordered, ordered[1:]):
                if previous.end_date is None or current.start_date <= previous.end_date:
                    raise ValueError(
                        f"CRSP stock-name intervals overlap for PERMNO {permno}: "
                        f"{previous.start_date}..{previous.end_date} and {current.start_date}..{current.end_date}"
                    )
            self._rows[permno] = tuple(ordered)
            self._starts[permno] = tuple(item.start_date for item in ordered)

    def resolve(self, permno: str, value: date) -> CrspSecurityInterval | None:
        rows = self._rows.get(permno)
        if not rows:
            return None
        starts = self._starts[permno]
        index = bisect_right(starts, value) - 1
        if index < 0:
            return None
        candidate = rows[index]
        return candidate if candidate.contains(value) else None


def _load_security_intervals(
    path: Path,
    config: CrspNormalizeConfig,
) -> tuple[tuple[CrspSecurityInterval, ...], int, int, dict[str, str | None]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        columns = config.columns
        permno_col = _resolve_column(fields, "permno", override=columns.stocknames_permno)
        start_col = _resolve_column(fields, "namedt", override=columns.stocknames_start)
        end_col = _resolve_column(fields, "nameendt", override=columns.stocknames_end, required=False)
        ticker_col = _resolve_column(fields, "ticker", override=columns.stocknames_ticker, required=False)
        name_col = _resolve_column(fields, "company_name", override=columns.stocknames_name, required=False)
        share_col = _resolve_column(
            fields,
            "share_code",
            override=columns.stocknames_share_code,
            required=config.share_codes is not None,
        )
        exchange_col = _resolve_column(
            fields,
            "exchange_code",
            override=columns.stocknames_exchange_code,
            required=False,
        )
        resolved = {
            "stocknames_permno": permno_col,
            "stocknames_start": start_col,
            "stocknames_end": end_col,
            "stocknames_ticker": ticker_col,
            "stocknames_name": name_col,
            "stocknames_share_code": share_col,
            "stocknames_exchange_code": exchange_col,
        }
        rows: list[CrspSecurityInterval] = []
        seen_exact: set[tuple[object, ...]] = set()
        input_count = 0
        filtered = 0
        for row_number, raw in enumerate(reader, 2):
            input_count += 1
            permno = _permno(raw.get(permno_col), row=row_number)
            start = _parse_date(raw.get(start_col), field=start_col or "NAMEDT", row=row_number)
            end = _parse_date(
                raw.get(end_col) if end_col else None,
                field=end_col or "NAMEENDT",
                row=row_number,
                required=False,
            )
            assert start is not None
            if end is not None and end < start:
                raise ValueError(f"CRSP row {row_number}: NAMEENDT precedes NAMEDT")
            share_code = _integral(
                raw.get(share_col) if share_col else None,
                field=share_col or "SHRCD",
                row=row_number,
                required=False,
            )
            if config.share_codes is not None and share_code not in set(config.share_codes):
                filtered += 1
                continue
            exchange_code = _integral(
                raw.get(exchange_col) if exchange_col else None,
                field=exchange_col or "EXCHCD",
                row=row_number,
                required=False,
            )
            ticker = str(raw.get(ticker_col) or "").strip().upper() if ticker_col else ""
            symbol = ticker or f"PERMNO_{permno}"
            name = str(raw.get(name_col) or "").strip() if name_col else ""
            name = name or symbol
            interval = CrspSecurityInterval(
                security_id=_security_id(permno),
                permno=permno,
                symbol=symbol,
                name=name,
                exchange=_exchange_label(exchange_code),
                start_date=start,
                end_date=end,
                share_code=share_code,
                exchange_code=exchange_code,
            )
            exact = (
                interval.permno,
                interval.symbol,
                interval.name,
                interval.exchange,
                interval.start_date,
                interval.end_date,
                interval.share_code,
                interval.exchange_code,
            )
            if exact in seen_exact:
                continue
            seen_exact.add(exact)
            rows.append(interval)
    index = _IntervalIndex(rows)
    # Constructing the index is the overlap validation boundary.
    del index
    return tuple(sorted(rows, key=lambda item: (int(item.permno), item.start_date))), input_count, filtered, resolved


def _write_securities(path: Path, intervals: Iterable[CrspSecurityInterval]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "security_id",
        "symbol",
        "name",
        "exchange",
        "asset_type",
        "start_date",
        "end_date",
        "status",
        "crsp_permno",
        "crsp_share_code",
        "crsp_exchange_code",
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in intervals:
            status_parts = []
            if item.share_code is not None:
                status_parts.append(f"SHRCD={item.share_code}")
            if item.exchange_code is not None:
                status_parts.append(f"EXCHCD={item.exchange_code}")
            writer.writerow(
                {
                    "security_id": item.security_id,
                    "symbol": item.symbol,
                    "name": item.name,
                    "exchange": item.exchange or "",
                    "asset_type": "stock",
                    "start_date": item.start_date.isoformat(),
                    "end_date": item.end_date.isoformat() if item.end_date else "",
                    "status": ";".join(status_parts),
                    "crsp_permno": item.permno,
                    "crsp_share_code": "" if item.share_code is None else item.share_code,
                    "crsp_exchange_code": "" if item.exchange_code is None else item.exchange_code,
                }
            )


def normalize_crsp_exports(
    stocknames_csv: str | Path,
    market_csv: str | Path,
    securities_output: str | Path,
    prices_output: str | Path,
    *,
    config: CrspNormalizeConfig,
) -> CrspNormalizationResult:
    stocknames_path = Path(stocknames_csv)
    market_path = Path(market_csv)
    securities_path = Path(securities_output)
    prices_path = Path(prices_output)

    intervals, stocknames_count, filtered_count, resolved = _load_security_intervals(stocknames_path, config)
    interval_index = _IntervalIndex(intervals)
    _write_securities(securities_path, intervals)

    prices_path.parent.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    missing_codes = set(config.missing_return_codes)
    market_count = 0
    output_count = 0
    outside_count = 0
    no_price_count = 0
    negative_price_count = 0
    missing_return_count = 0
    dlret_count = 0
    break_count = 0
    adjusted_count = 0
    barrier_count = 0

    with market_path.open("r", encoding="utf-8-sig", newline="") as source, prices_path.open(
        "w", encoding="utf-8", newline=""
    ) as target:
        reader = csv.DictReader(source)
        fields = reader.fieldnames or []
        columns = config.columns
        permno_col = _resolve_column(fields, "permno", override=columns.market_permno)
        date_col = _resolve_column(fields, "date", override=columns.market_date)
        price_col = _resolve_column(fields, "price", override=columns.market_price)
        return_col = _resolve_column(fields, "return", override=columns.market_return)
        dlret_col = None
        if config.return_semantics == "legacy":
            dlret_col = _resolve_column(
                fields,
                "delisting_return",
                override=columns.market_delisting_return,
                required=False,
            )
            if dlret_col is None:
                warnings.append(
                    "legacy return semantics selected but no delisting-return column was found; delisting-period total returns may be incomplete"
                )
        elif columns.market_delisting_return:
            raise ValueError(
                "market_delisting_return override is incompatible with return_semantics='already_total'; refusing possible double counting"
            )

        shares_col = None
        if columns.market_shares_outstanding:
            if config.shares_outstanding_scale is None:
                raise ValueError(
                    "shares_outstanding_scale is required when market_shares_outstanding is supplied"
                )
            shares_col = _resolve_column(
                fields,
                "shares_outstanding",
                override=columns.market_shares_outstanding,
            )
        elif config.shares_outstanding_scale is not None:
            raise ValueError(
                "market_shares_outstanding column override is required when shares_outstanding_scale is supplied"
            )

        resolved.update(
            {
                "market_permno": permno_col,
                "market_date": date_col,
                "market_price": price_col,
                "market_return": return_col,
                "market_delisting_return": dlret_col,
                "market_shares_outstanding": shares_col,
            }
        )

        writer = csv.DictWriter(
            target,
            fieldnames=(
                "security_id",
                "symbol",
                "date",
                "close",
                "adjusted_close",
                "volume",
                "shares_outstanding",
                "crsp_permno",
                "crsp_prc_was_negative",
                "crsp_return_semantics",
            ),
        )
        writer.writeheader()

        current_permno: str | None = None
        wealth: float | None = None
        needs_barrier = False
        last_key: tuple[int, date] | None = None

        for row_number, raw in enumerate(reader, 2):
            market_count += 1
            permno = _permno(raw.get(permno_col), row=row_number)
            dt = _parse_date(raw.get(date_col), field=date_col or "date", row=row_number)
            assert dt is not None
            key = (int(permno), dt)
            if last_key is not None and key <= last_key:
                raise ValueError(
                    f"CRSP market export must be strictly sorted by PERMNO,date; row {row_number} key {key} follows {last_key}"
                )
            last_key = key
            if permno != current_permno:
                current_permno = permno
                wealth = None
                needs_barrier = False

            interval = interval_index.resolve(permno, dt)
            if interval is None:
                outside_count += 1
                if wealth is not None:
                    break_count += 1
                wealth = None
                needs_barrier = True
                continue

            raw_price = _finite_float(raw.get(price_col), field=price_col or "price", row=row_number)
            economic_price = None if raw_price is None else abs(raw_price)
            if raw_price is None:
                no_price_count += 1
            elif raw_price < 0:
                negative_price_count += 1

            ret = _return_value(
                raw.get(return_col),
                field=return_col or "return",
                row=row_number,
                missing_codes=missing_codes,
            )
            dlret = None
            if dlret_col is not None:
                dlret = _return_value(
                    raw.get(dlret_col),
                    field=dlret_col,
                    row=row_number,
                    missing_codes=missing_codes,
                )
            if config.return_semantics == "legacy":
                if ret is None and dlret is None:
                    combined_return = None
                elif ret is None:
                    combined_return = dlret
                elif dlret is None:
                    combined_return = ret
                else:
                    combined_return = (1.0 + ret) * (1.0 + dlret) - 1.0
                    dlret_count += 1
            else:
                combined_return = ret

            if combined_return is not None and combined_return < -1.0 - 1e-12:
                raise ValueError(f"CRSP row {row_number}: combined total return is below -1")

            adjusted: float | None = None
            barrier_row = False
            if wealth is None:
                if economic_price is not None:
                    wealth = economic_price
                    if needs_barrier:
                        barrier_row = True
                        needs_barrier = False
                    else:
                        adjusted = wealth
            elif combined_return is None:
                missing_return_count += 1
                break_count += 1
                if economic_price is not None:
                    wealth = economic_price
                    barrier_row = True
                    needs_barrier = False
                else:
                    wealth = None
                    needs_barrier = True
            else:
                if wealth == 0.0 and economic_price is not None and economic_price > 0:
                    break_count += 1
                    wealth = economic_price
                    barrier_row = True
                else:
                    wealth *= 1.0 + combined_return
                    adjusted = wealth

            if economic_price is None:
                continue

            shares = None
            if shares_col is not None:
                raw_shares = _finite_float(
                    raw.get(shares_col),
                    field=shares_col,
                    row=row_number,
                )
                if raw_shares is not None:
                    if raw_shares < 0:
                        raise ValueError(f"CRSP row {row_number}: shares outstanding cannot be negative")
                    assert config.shares_outstanding_scale is not None
                    shares = raw_shares * config.shares_outstanding_scale

            if adjusted is not None:
                adjusted_count += 1
            if barrier_row:
                barrier_count += 1
            writer.writerow(
                {
                    "security_id": interval.security_id,
                    "symbol": interval.symbol,
                    "date": dt.isoformat(),
                    "close": economic_price,
                    "adjusted_close": "" if adjusted is None else adjusted,
                    "volume": "",
                    "shares_outstanding": "" if shares is None else shares,
                    "crsp_permno": permno,
                    "crsp_prc_was_negative": str(raw_price is not None and raw_price < 0).lower(),
                    "crsp_return_semantics": config.return_semantics,
                }
            )
            output_count += 1

    if negative_price_count:
        warnings.append(
            f"used absolute economic price for {negative_price_count} negative-signed price row(s); the original sign is retained in crsp_prc_was_negative for audit"
        )
    if barrier_count:
        warnings.append(
            f"emitted {barrier_count} unadjusted barrier row(s) after return-chain breaks; CsvMarketProvider will reject drawdown windows crossing those breaks"
        )

    return CrspNormalizationResult(
        stocknames_input_rows=stocknames_count,
        security_interval_count=len(intervals),
        filtered_stocknames_rows=filtered_count,
        market_input_rows=market_count,
        output_price_rows=output_count,
        market_rows_outside_security_intervals=outside_count,
        rows_without_usable_price=no_price_count,
        negative_price_rows=negative_price_count,
        missing_return_rows=missing_return_count,
        delisting_return_rows_applied=dlret_count,
        return_chain_break_count=break_count,
        adjusted_output_rows=adjusted_count,
        unadjusted_barrier_rows=barrier_count,
        resolved_columns=resolved,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def crsp_normalization_result_to_dict(result: CrspNormalizationResult) -> dict[str, object]:
    return {
        **asdict(result),
        "warnings": list(result.warnings),
    }

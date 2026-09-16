from __future__ import annotations

from datetime import date
import math
import os
import re
import threading
import time
from typing import Any, Callable

import requests

from .bond_market import BondBenchmarkObservation, BondMarketObservation

EODHD_API_BASE = "https://eodhd.com/api"


def _identifier(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    return cleaned or None


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _tenor_years(tenor: str) -> float | None:
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([MY])\s*", tenor.upper())
    if not match:
        return None
    value = float(match.group(1))
    return value / 12.0 if match.group(2) == "M" else value


class EodhdBondProvider:
    """Targeted US corporate-bond history using EODHD APIs.

    Existing EODHD wrappers route CUSIP/ISIN identifiers through the generic EOD
    endpoint using the `.BOND` suffix. Because the older dedicated bond docs are
    no longer published, this adapter treats that route as a compatibility
    surface and validates every in-range row strictly. A partially incompatible
    response is rejected rather than silently retaining only rows that happen to
    look bond-like.

    Treasury benchmark yields use `/ust/yield-rates` and are selected without
    looking past the bond observation date. Benchmark freshness is enforced by
    the common bond-market analytics layer.
    """

    name = "eodhd_bonds"
    bulk_safe = False

    def __init__(
        self,
        api_key: str | None = None,
        *,
        session: requests.Session | Any | None = None,
        min_interval_seconds: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = (api_key or os.getenv("EODHD_API_KEY", "")).strip()
        if not self.api_key:
            raise ValueError("EODHD_API_KEY is required")
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds cannot be negative")
        self.session = session or requests.Session()
        self.min_interval_seconds = min_interval_seconds
        self.clock = clock
        self.sleeper = sleeper
        self._last_request_at: float | None = None
        self._lock = threading.Lock()
        self._bond_cache: dict[tuple[str, date, date], tuple[BondMarketObservation, ...]] = {}
        self._treasury_year_cache: dict[int, tuple[BondBenchmarkObservation, ...]] = {}

    def _throttle(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        with self._lock:
            now = self.clock()
            if self._last_request_at is not None:
                wait = self.min_interval_seconds - (now - self._last_request_at)
                if wait > 0:
                    self.sleeper(wait)
                    now = self.clock()
            self._last_request_at = now

    def _get_json(self, path: str, **params: Any) -> Any:
        self._throttle()
        response = self.session.get(
            f"{EODHD_API_BASE}/{path.lstrip('/')}",
            params={**params, "api_token": self.api_key, "fmt": "json"},
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            if payload.get("error"):
                raise RuntimeError(f"EODHD API error: {payload}")
            status = payload.get("status")
            try:
                status_code = int(status) if status is not None else None
            except (TypeError, ValueError):
                status_code = None
            if status_code is not None and status_code >= 400:
                raise RuntimeError(f"EODHD API error: {payload}")
        return payload

    @staticmethod
    def _rows(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                return [row for row in data if isinstance(row, dict)]
        return []

    def _bond_history(self, identifier: str, start: date, end: date) -> tuple[BondMarketObservation, ...]:
        cache_key = (identifier, start, end)
        if cache_key in self._bond_cache:
            return self._bond_cache[cache_key]
        payload = self._get_json(
            f"eod/{identifier}.BOND",
            **{"from": start.isoformat(), "to": end.isoformat(), "period": "d", "order": "a"},
        )
        raw_rows = self._rows(payload)
        output: list[BondMarketObservation] = []
        malformed: list[tuple[str, tuple[str, ...]]] = []
        is_isin = len(identifier) == 12 and identifier[:2].isalpha()
        for raw in raw_rows:
            dt = _date(raw.get("date"))
            if dt is None:
                malformed.append(("invalid_or_missing_date", tuple(sorted(str(key) for key in raw))))
                continue
            if not start <= dt <= end:
                continue
            # Compatibility route must expose bond-specific price/yield fields.
            # Never reinterpret stock-like OHLC `close` as a bond price.
            price = _float(raw.get("price"))
            yield_pct = _float(raw.get("yield"))
            volume = _float(raw.get("volume"))
            if price is None or yield_pct is None:
                malformed.append((dt.isoformat(), tuple(sorted(str(key) for key in raw))))
                continue
            if price < 0 or (volume is not None and volume < 0):
                malformed.append((dt.isoformat(), tuple(sorted(str(key) for key in raw))))
                continue
            output.append(
                BondMarketObservation(
                    date=dt,
                    cusip=None if is_isin else identifier,
                    isin=identifier if is_isin else None,
                    price_pct_par=price,
                    yield_pct=yield_pct,
                    volume=volume,
                    source=f"EODHD eod/{identifier}.BOND",
                )
            )
        if malformed:
            sample = malformed[:3]
            raise RuntimeError(
                "EODHD .BOND response did not consistently expose the documented bond price/yield shape; "
                "incompatible rows were present, so partial acceptance and reinterpretation as bond data were refused "
                f"(sample={sample})"
            )
        result = tuple(sorted(output, key=lambda item: item.date))
        self._bond_cache[cache_key] = result
        return result

    def observations(
        self,
        *,
        cusip: str | None,
        isin: str | None,
        start: date,
        end: date,
    ) -> tuple[BondMarketObservation, ...]:
        if end < start:
            raise ValueError("bond observation end date cannot precede start date")
        identifiers = [item for item in (_identifier(isin), _identifier(cusip)) if item]
        if not identifiers:
            return ()
        errors: list[tuple[str, Exception]] = []
        unique_identifiers = tuple(dict.fromkeys(identifiers))
        for identifier in unique_identifiers:
            try:
                rows = self._bond_history(identifier, start, end)
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                errors.append((identifier, exc))
                continue
            if rows:
                return rows
        if errors:
            # If one explicit identifier exposed an API/compatibility failure, do
            # not hide it merely because another identifier returned an empty set.
            raise RuntimeError(
                "EODHD bond history failed for explicit identifier(s): "
                + "; ".join(f"{identifier}: {exc}" for identifier, exc in errors)
            )
        return ()

    def _treasury_year(self, year: int) -> tuple[BondBenchmarkObservation, ...]:
        if year in self._treasury_year_cache:
            return self._treasury_year_cache[year]
        payload = self._get_json("ust/yield-rates", **{"filter[year]": year})
        output: list[BondBenchmarkObservation] = []
        for raw in self._rows(payload):
            dt = _date(raw.get("date"))
            tenor = str(raw.get("tenor") or "").strip().upper()
            rate = _float(raw.get("rate"))
            if dt is None or not tenor or rate is None or _tenor_years(tenor) is None:
                continue
            output.append(
                BondBenchmarkObservation(
                    date=dt,
                    tenor=tenor,
                    yield_pct=rate,
                    source="EODHD UST yield-rates",
                )
            )
        result = tuple(sorted(output, key=lambda item: (item.date, _tenor_years(item.tenor) or 0.0)))
        self._treasury_year_cache[year] = result
        return result

    def benchmark_on_or_before(
        self,
        *,
        as_of: date,
        remaining_years: float,
    ) -> BondBenchmarkObservation | None:
        if not math.isfinite(remaining_years) or remaining_years <= 0:
            return None
        rows: list[BondBenchmarkObservation] = []
        # New-year holidays can require the last business day of the prior year.
        for year in (as_of.year, as_of.year - 1):
            rows.extend(self._treasury_year(year))
        eligible = [row for row in rows if row.date <= as_of]
        if not eligible:
            return None
        latest_date = max(row.date for row in eligible)
        curve = [row for row in eligible if row.date == latest_date]
        if not curve:
            return None
        return min(
            curve,
            key=lambda row: (
                abs((_tenor_years(row.tenor) or 0.0) - remaining_years),
                _tenor_years(row.tenor) or 0.0,
            ),
        )

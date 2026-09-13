from __future__ import annotations

import csv
from datetime import date
import io
import json
import os
import threading
import time
from typing import Any, Callable

import requests

from .market import PricePoint, SecurityIdentity

ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value[:10])


def _float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


class AlphaVantageProvider:
    """Small-batch historical provider using Alpha Vantage official APIs.

    The provider is intentionally marked `bulk_safe=False`: historical prices
    are fetched per symbol, so a thousands-of-symbol full-universe replay should
    use a bulk database/export instead. LISTING_STATUS is still useful for a
    survivorship-aware historical universe and targeted replay.
    """

    name = "alpha_vantage"
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
        self.api_key = (api_key or os.getenv("ALPHA_VANTAGE_API_KEY", "")).strip()
        if not self.api_key:
            raise ValueError("ALPHA_VANTAGE_API_KEY is required")
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds cannot be negative")
        self.session = session or requests.Session()
        self.min_interval_seconds = min_interval_seconds
        self.clock = clock
        self.sleeper = sleeper
        self._last_request_at: float | None = None
        self._lock = threading.Lock()
        self._universe_cache: dict[tuple[date, str], tuple[SecurityIdentity, ...]] = {}
        self._daily_cache: dict[str, tuple[PricePoint, ...]] = {}
        self._weekly_adjusted_cache: dict[str, tuple[PricePoint, ...]] = {}

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

    def _get_csv(self, **params: str) -> list[dict[str, str]]:
        self._throttle()
        response = self.session.get(
            ALPHA_VANTAGE_URL,
            params={**params, "apikey": self.api_key, "datatype": "csv"},
            timeout=60,
        )
        response.raise_for_status()
        text = response.text.lstrip("\ufeff").strip()
        if not text:
            raise ValueError("Alpha Vantage returned an empty response")
        if text.startswith("{"):
            payload = json.loads(text)
            message = (
                payload.get("Error Message")
                or payload.get("Information")
                or payload.get("Note")
                or payload
            )
            raise RuntimeError(f"Alpha Vantage API error/limit response: {message}")
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            raise ValueError("Alpha Vantage returned CSV without data rows")
        return [{str(k): ("" if v is None else str(v)) for k, v in row.items()} for row in rows]

    def listing_status(self, as_of: date, *, state: str = "active") -> tuple[SecurityIdentity, ...]:
        if as_of <= date(2010, 1, 1):
            raise ValueError("Alpha Vantage historical LISTING_STATUS supports dates after 2010-01-01")
        if state not in {"active", "delisted"}:
            raise ValueError("state must be active or delisted")
        key = (as_of, state)
        if key in self._universe_cache:
            return self._universe_cache[key]

        rows = self._get_csv(function="LISTING_STATUS", date=as_of.isoformat(), state=state)
        securities: list[SecurityIdentity] = []
        for row in rows:
            symbol = row.get("symbol", "").strip()
            if not symbol:
                continue
            ipo = _parse_date(row.get("ipoDate"))
            delisting = _parse_date(row.get("delistingDate"))
            security_id = f"AV:{symbol}:{ipo.isoformat() if ipo else 'unknown'}"
            securities.append(
                SecurityIdentity(
                    security_id=security_id,
                    symbol=symbol,
                    name=row.get("name", "").strip(),
                    exchange=row.get("exchange", "").strip() or None,
                    asset_type=row.get("assetType", "").strip() or None,
                    start_date=ipo,
                    end_date=delisting,
                    status=row.get("status", "").strip() or state,
                    provider=self.name,
                )
            )
        result = tuple(securities)
        self._universe_cache[key] = result
        return result

    def universe(self, as_of: date) -> tuple[SecurityIdentity, ...]:
        # `active` is evaluated at the historical date, so securities that later
        # delisted remain in the historical investable universe.
        return tuple(
            security
            for security in self.listing_status(as_of, state="active")
            if (security.asset_type or "").lower() == "stock"
        )

    def delisted_as_of(self, as_of: date) -> tuple[SecurityIdentity, ...]:
        return self.listing_status(as_of, state="delisted")

    def _daily_history(self, symbol: str) -> tuple[PricePoint, ...]:
        if symbol in self._daily_cache:
            return self._daily_cache[symbol]
        rows = self._get_csv(
            function="TIME_SERIES_DAILY",
            symbol=symbol,
            outputsize="full",
        )
        points: list[PricePoint] = []
        for row in rows:
            dt = _parse_date(row.get("timestamp"))
            close = _float(row.get("close"))
            if dt is None or close is None:
                continue
            points.append(
                PricePoint(
                    security_id=f"AV:{symbol}",
                    symbol=symbol,
                    date=dt,
                    close=close,
                    adjusted=False,
                    volume=_float(row.get("volume")),
                    source="Alpha Vantage TIME_SERIES_DAILY",
                )
            )
        points.sort(key=lambda point: point.date)
        result = tuple(points)
        self._daily_cache[symbol] = result
        return result

    def _weekly_adjusted_history(self, symbol: str) -> tuple[PricePoint, ...]:
        if symbol in self._weekly_adjusted_cache:
            return self._weekly_adjusted_cache[symbol]
        rows = self._get_csv(function="TIME_SERIES_WEEKLY_ADJUSTED", symbol=symbol)
        points: list[PricePoint] = []
        for row in rows:
            dt = _parse_date(row.get("timestamp"))
            close = _float(row.get("adjusted close"))
            if close is None:
                close = _float(row.get("adjusted_close"))
            if dt is None or close is None:
                continue
            points.append(
                PricePoint(
                    security_id=f"AV:{symbol}",
                    symbol=symbol,
                    date=dt,
                    close=close,
                    adjusted=True,
                    volume=_float(row.get("volume")),
                    source="Alpha Vantage TIME_SERIES_WEEKLY_ADJUSTED",
                )
            )
        points.sort(key=lambda point: point.date)
        result = tuple(points)
        self._weekly_adjusted_cache[symbol] = result
        return result

    def price_on_or_before(self, security: SecurityIdentity, as_of: date) -> PricePoint | None:
        points = [point for point in self._daily_history(security.symbol) if point.date <= as_of]
        if not points:
            return None
        point = max(points, key=lambda item: item.date)
        return PricePoint(
            security_id=security.security_id,
            symbol=point.symbol,
            date=point.date,
            close=point.close,
            adjusted=False,
            volume=point.volume,
            source=point.source,
        )

    def adjusted_history(
        self,
        security: SecurityIdentity,
        start: date,
        end: date,
    ) -> tuple[PricePoint, ...]:
        return tuple(
            PricePoint(
                security_id=security.security_id,
                symbol=point.symbol,
                date=point.date,
                close=point.close,
                adjusted=True,
                volume=point.volume,
                source=point.source,
            )
            for point in self._weekly_adjusted_history(security.symbol)
            if start <= point.date <= end
        )

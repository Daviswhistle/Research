from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class SecurityIdentity:
    """Point-in-time investable security identity.

    `security_id` should be permanent when the provider offers one. Tickers are
    display/routing identifiers and can be reused or changed through time.
    """

    security_id: str
    symbol: str
    name: str
    exchange: str | None = None
    asset_type: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: str | None = None
    provider: str | None = None


@dataclass(frozen=True)
class PricePoint:
    security_id: str
    symbol: str
    date: date
    close: float
    adjusted: bool
    volume: float | None = None
    shares_outstanding: float | None = None
    source: str | None = None


@dataclass(frozen=True)
class MarketSnapshot:
    security: SecurityIdentity
    analysis_date: date
    price: PricePoint | None
    adjusted_price: PricePoint | None
    peak_adjusted_price: PricePoint | None
    adjusted_drawdown_from_peak: float | None
    market_cap: float | None
    warnings: tuple[str, ...] = ()


class HistoricalMarketProvider(Protocol):
    """Provider contract for point-in-time market reconstruction."""

    name: str
    bulk_safe: bool

    def universe(self, as_of: date) -> tuple[SecurityIdentity, ...]: ...

    def price_on_or_before(
        self,
        security: SecurityIdentity,
        as_of: date,
    ) -> PricePoint | None: ...

    def adjusted_history(
        self,
        security: SecurityIdentity,
        start: date,
        end: date,
    ) -> tuple[PricePoint, ...]: ...


def latest_on_or_before(points: tuple[PricePoint, ...], cutoff: date) -> PricePoint | None:
    eligible = [point for point in points if point.date <= cutoff]
    return max(eligible, key=lambda point: point.date) if eligible else None


def adjusted_peak_and_drawdown(
    points: tuple[PricePoint, ...],
    cutoff: date,
) -> tuple[PricePoint | None, PricePoint | None, float | None]:
    """Return latest adjusted point, prior peak and drawdown without look-ahead."""

    eligible = [point for point in points if point.date <= cutoff]
    if not eligible:
        return None, None, None
    if not all(point.adjusted for point in eligible):
        return None, None, None
    current = max(eligible, key=lambda point: point.date)
    peak = max(eligible, key=lambda point: (point.close, -point.date.toordinal()))
    if peak.close <= 0:
        return current, peak, None
    return current, peak, 1.0 - current.close / peak.close


def market_snapshot(
    provider: HistoricalMarketProvider,
    security: SecurityIdentity,
    analysis_date: date,
    *,
    history_start: date,
    shares_outstanding: float | None = None,
) -> MarketSnapshot:
    """Build a safe point-in-time market snapshot.

    Exact market capitalization uses the raw/as-traded cutoff price. Drawdown
    uses only an adjusted history. This deliberately avoids false distress from
    stock splits.
    """

    raw_price = provider.price_on_or_before(security, analysis_date)
    history = provider.adjusted_history(security, history_start, analysis_date)
    adjusted_price, peak, drawdown = adjusted_peak_and_drawdown(history, analysis_date)
    warnings: list[str] = []

    if raw_price is not None and raw_price.date > analysis_date:
        raise ValueError("provider returned a future raw price")
    if any(point.date > analysis_date for point in history):
        raise ValueError("provider returned future adjusted history")
    if history and drawdown is None:
        warnings.append("adjusted drawdown unavailable because the supplied history is not adjustment-safe")
    if raw_price is None:
        warnings.append("no raw/as-traded price was available on or before the cutoff")
    if adjusted_price is None:
        warnings.append("no adjusted price history was available on or before the cutoff")

    market_cap = None
    if raw_price is not None and shares_outstanding is not None and shares_outstanding > 0:
        market_cap = raw_price.close * shares_outstanding

    return MarketSnapshot(
        security=security,
        analysis_date=analysis_date,
        price=raw_price,
        adjusted_price=adjusted_price,
        peak_adjusted_price=peak,
        adjusted_drawdown_from_peak=drawdown,
        market_cap=market_cap,
        warnings=tuple(warnings),
    )

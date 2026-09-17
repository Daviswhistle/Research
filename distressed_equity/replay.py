from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Iterable

from .market import (
    HistoricalMarketProvider,
    PricePoint,
    SecurityIdentity,
    adjusted_peak_and_drawdown,
)


@dataclass(frozen=True)
class DistressScanConfig:
    min_adjusted_drawdown: float = 0.60
    lookback_years: int = 5
    min_raw_price: float = 0.10
    asset_types: tuple[str, ...] = ("stock",)
    exchanges: tuple[str, ...] = ()
    require_adjusted_history: bool = True


@dataclass(frozen=True)
class MarketDistressSeed:
    security: SecurityIdentity
    analysis_date: date
    raw_price: PricePoint
    adjusted_price: PricePoint
    peak_adjusted_price: PricePoint
    adjusted_drawdown_from_peak: float
    lookback_start: date


@dataclass(frozen=True)
class ReplaySkip:
    security_id: str
    symbol: str
    reason: str


@dataclass(frozen=True)
class ReplayRun:
    provider: str
    analysis_date: date
    historical_universe_size: int
    scanned_security_count: int
    candidates: tuple[MarketDistressSeed, ...]
    skipped: tuple[ReplaySkip, ...]
    survivorship_guard: str


def normalize_exchange_filters(values: Iterable[str]) -> tuple[str, ...]:
    """Canonicalize exchange filters with the same case-insensitive semantics as replay."""

    return tuple(sorted({str(item).upper() for item in values}))


def _eligible_security(
    security: SecurityIdentity,
    config: DistressScanConfig,
    symbols: set[str] | None,
) -> bool:
    if symbols is not None and security.symbol.upper() not in symbols:
        return False
    if config.asset_types and security.asset_type:
        asset_type = security.asset_type.lower()
        if asset_type not in {item.lower() for item in config.asset_types}:
            return False
    if config.exchanges:
        exchange = (security.exchange or "").upper()
        if exchange not in normalize_exchange_filters(config.exchanges):
            return False
    return True


def scan_security(
    provider: HistoricalMarketProvider,
    security: SecurityIdentity,
    analysis_date: date,
    config: DistressScanConfig | None = None,
) -> tuple[MarketDistressSeed | None, str | None]:
    config = config or DistressScanConfig()
    raw_price = provider.price_on_or_before(security, analysis_date)
    if raw_price is None:
        return None, "no cutoff-date raw price"
    if raw_price.date > analysis_date:
        raise ValueError("look-ahead violation: raw price after analysis date")
    if raw_price.close < config.min_raw_price:
        return None, "raw price below configured minimum"

    lookback_start = analysis_date - timedelta(days=365 * config.lookback_years + 2)
    history = provider.adjusted_history(security, lookback_start, analysis_date)
    if any(point.date > analysis_date for point in history):
        raise ValueError("look-ahead violation: adjusted price after analysis date")
    if not history:
        return None, "no price history in lookback window"
    if config.require_adjusted_history and not all(point.adjusted for point in history):
        return None, "history is not split/dividend adjusted"

    current, peak, drawdown = adjusted_peak_and_drawdown(history, analysis_date)
    if current is None or peak is None or drawdown is None:
        return None, "unable to compute adjustment-safe drawdown"
    if drawdown < config.min_adjusted_drawdown:
        return None, "drawdown below configured distress threshold"

    return (
        MarketDistressSeed(
            security=security,
            analysis_date=analysis_date,
            raw_price=raw_price,
            adjusted_price=current,
            peak_adjusted_price=peak,
            adjusted_drawdown_from_peak=drawdown,
            lookback_start=lookback_start,
        ),
        None,
    )


def run_historical_replay(
    provider: HistoricalMarketProvider,
    analysis_date: date,
    *,
    config: DistressScanConfig | None = None,
    symbols: Iterable[str] | None = None,
    allow_slow_full_universe: bool = False,
    allow_survivorship_unsafe_universe: bool = False,
    max_securities: int | None = None,
) -> ReplayRun:
    config = config or DistressScanConfig()
    symbol_set = {symbol.upper() for symbol in symbols} if symbols is not None else None
    if not provider.bulk_safe and symbol_set is None and not allow_slow_full_universe:
        raise ValueError(
            f"{provider.name} is not bulk-safe; pass symbols or explicitly allow a slow full-universe replay"
        )

    survivorship_safe = bool(getattr(provider, "survivorship_safe_universe", True))
    if not survivorship_safe and not allow_survivorship_unsafe_universe:
        raise ValueError(
            f"{provider.name} universe lacks point-in-time membership provenance; "
            "provide start/end membership dates or explicitly allow a survivorship-unsafe replay"
        )

    universe = provider.universe(analysis_date)
    eligible = [
        security for security in universe if _eligible_security(security, config, symbol_set)
    ]
    eligible.sort(key=lambda security: (security.symbol, security.security_id))
    if max_securities is not None:
        eligible = eligible[:max_securities]

    candidates: list[MarketDistressSeed] = []
    skipped: list[ReplaySkip] = []
    for security in eligible:
        candidate, reason = scan_security(provider, security, analysis_date, config)
        if candidate is not None:
            candidates.append(candidate)
        else:
            skipped.append(
                ReplaySkip(
                    security_id=security.security_id,
                    symbol=security.symbol,
                    reason=reason or "unknown",
                )
            )
    return ReplayRun(
        provider=provider.name,
        analysis_date=analysis_date,
        historical_universe_size=len(universe),
        scanned_security_count=len(eligible),
        candidates=tuple(candidates),
        skipped=tuple(skipped),
        survivorship_guard=(
            "point-in-time membership used; current-active security membership was not substituted"
            if survivorship_safe
            else "WARNING: provider universe lacks point-in-time membership provenance; current-survivor bias may be present"
        ),
    )


def seed_to_dict(seed: MarketDistressSeed) -> dict[str, object]:
    return {
        "security": asdict(seed.security),
        "analysis_date": seed.analysis_date.isoformat(),
        "raw_price": asdict(seed.raw_price),
        "adjusted_price": asdict(seed.adjusted_price),
        "peak_adjusted_price": asdict(seed.peak_adjusted_price),
        "adjusted_drawdown_from_peak": seed.adjusted_drawdown_from_peak,
        "lookback_start": seed.lookback_start.isoformat(),
    }


def replay_run_to_dict(run: ReplayRun) -> dict[str, object]:
    output = asdict(run)
    output["analysis_date"] = run.analysis_date.isoformat()
    output["candidates"] = [seed_to_dict(seed) for seed in run.candidates]
    return output


def clone_replay_with_candidates(
    run: ReplayRun,
    candidates: Iterable[MarketDistressSeed],
) -> ReplayRun:
    return ReplayRun(
        provider=run.provider,
        analysis_date=run.analysis_date,
        historical_universe_size=run.historical_universe_size,
        scanned_security_count=run.scanned_security_count,
        candidates=tuple(deepcopy(tuple(candidates))),
        skipped=run.skipped,
        survivorship_guard=run.survivorship_guard,
    )

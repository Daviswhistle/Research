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


def _eligible_security(
    security: SecurityIdentity,
    config: DistressScanConfig,
    symbols: set[str] | None,
) -> bool:
    if symbols is not None and security.symbol.upper() not in symbols:
        return False
    if config.asset_types:
        asset_type = (security.asset_type or "").lower()
        if asset_type not in {item.lower() for item in config.asset_types}:
            return False
    if config.exchanges:
        exchange = (security.exchange or "").upper()
        if exchange not in {item.upper() for item in config.exchanges}:
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
    max_securities: int | None = None,
) -> ReplayRun:
    config = config or DistressScanConfig()
    symbol_set = {symbol.upper() for symbol in symbols} if symbols is not None else None
    if not provider.bulk_safe and symbol_set is None and not allow_slow_full_universe:
        raise ValueError(
            f"{provider.name} is not bulk-safe; pass symbols or explicitly allow a slow full-universe replay"
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
        seed, reason = scan_security(provider, security, analysis_date, config)
        if seed is not None:
            candidates.append(seed)
        else:
            skipped.append(
                ReplaySkip(
                    security_id=security.security_id,
                    symbol=security.symbol,
                    reason=reason or "not selected",
                )
            )

    candidates.sort(
        key=lambda seed: (-seed.adjusted_drawdown_from_peak, seed.security.symbol)
    )
    return ReplayRun(
        provider=provider.name,
        analysis_date=analysis_date,
        historical_universe_size=len(universe),
        scanned_security_count=len(eligible),
        candidates=tuple(candidates),
        skipped=tuple(skipped),
        survivorship_guard=(
            "Universe was requested from the provider as of the analysis date; current-active membership was not substituted."
        ),
    )


def seed_to_dict(seed: MarketDistressSeed) -> dict[str, object]:
    payload = asdict(seed)
    payload["analysis_date"] = seed.analysis_date.isoformat()
    payload["lookback_start"] = seed.lookback_start.isoformat()
    payload["security"]["start_date"] = (
        seed.security.start_date.isoformat() if seed.security.start_date else None
    )
    payload["security"]["end_date"] = (
        seed.security.end_date.isoformat() if seed.security.end_date else None
    )
    for key in ("raw_price", "adjusted_price", "peak_adjusted_price"):
        payload[key]["date"] = payload[key]["date"].isoformat()
    return payload


def apply_market_seed_to_prefill(
    research_packet: dict[str, object],
    seed: MarketDistressSeed,
) -> dict[str, object]:
    """Merge market facts into a SEC prefill without inventing missing accounting data."""

    packet = deepcopy(research_packet)
    draft = packet.get("screening_candidate_draft")
    if not isinstance(draft, dict):
        raise ValueError("research packet lacks screening_candidate_draft")
    capital = draft.get("capital_structure")
    if not isinstance(capital, dict):
        raise ValueError("screening candidate draft lacks capital_structure")

    capital["current_price"] = seed.raw_price.close
    draft["price_drawdown_from_peak"] = seed.adjusted_drawdown_from_peak
    draft["price_drawdown_adjusted"] = True
    metadata = draft.setdefault("metadata", {})
    if isinstance(metadata, dict):
        metadata["market_replay"] = {
            "provider": seed.security.provider,
            "security_id": seed.security.security_id,
            "raw_price_date": seed.raw_price.date.isoformat(),
            "adjusted_price_date": seed.adjusted_price.date.isoformat(),
            "peak_adjusted_price": seed.peak_adjusted_price.close,
            "peak_adjusted_price_date": seed.peak_adjusted_price.date.isoformat(),
            "drawdown_basis": "split/dividend-adjusted price; not market-cap drawdown",
        }

    shares = capital.get("current_shares")
    if isinstance(shares, (int, float)) and shares > 0:
        packet.setdefault("market_context", {})
        market_context = packet["market_context"]
        if isinstance(market_context, dict):
            market_context["cutoff_market_cap"] = seed.raw_price.close * float(shares)
    packet.setdefault("market_context", {})
    market_context = packet["market_context"]
    if isinstance(market_context, dict):
        market_context.update(seed_to_dict(seed))

    unresolved = packet.get("unresolved_required_fields")
    if isinstance(unresolved, list):
        unresolved[:] = [
            item
            for item in unresolved
            if item != "point-in-time share price and peak market capitalization"
        ]
        unresolved.append(
            "prior peak market capitalization remains unresolved; adjusted price drawdown is only a distress proxy"
        )
    return packet

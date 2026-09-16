from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Iterable

from .market import HistoricalMarketProvider, PricePoint, SecurityIdentity
from .replay import MarketDistressSeed


@dataclass(frozen=True)
class MarketOutcomeConfig:
    horizon_grace_days: int = 31

    def __post_init__(self) -> None:
        if self.horizon_grace_days < 0:
            raise ValueError("horizon_grace_days cannot be negative")


@dataclass(frozen=True)
class MarketObservableOutcome:
    security_id: str
    symbol_at_distress: str
    analysis_date: date
    outcome_cutoff: date
    same_security_active_12m: bool | None
    adjusted_price_12m: PricePoint | None
    adjusted_price_multiple_12m: float | None
    same_security_active_3y: bool | None
    adjusted_price_3y: PricePoint | None
    adjusted_price_multiple_3y: float | None
    max_adjusted_price_within_3y: PricePoint | None
    max_adjusted_price_multiple_within_3y: float | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class MarketOutcomeCohort:
    provider: str
    outcome_cutoff: date
    case_count: int
    resolved_12m_membership_count: int
    active_12m_count: int
    resolved_3y_membership_count: int
    active_3y_count: int
    resolved_3y_endpoint_multiple_count: int
    resolved_3y_max_multiple_count: int
    outcomes: tuple[MarketObservableOutcome, ...]
    semantics: tuple[str, ...]


def _add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        # Feb-29 -> Feb-28 in a non-leap target year.
        return value.replace(year=value.year + years, day=28)


def _security_at(
    provider: HistoricalMarketProvider,
    security_id: str,
    as_of: date,
) -> SecurityIdentity | None:
    matches = [item for item in provider.universe(as_of) if item.security_id == security_id]
    if len(matches) > 1:
        raise ValueError(
            f"historical market provider returned duplicate security_id {security_id} on {as_of.isoformat()}"
        )
    return matches[0] if matches else None


def _first_adjusted_on_or_after(
    provider: HistoricalMarketProvider,
    security: SecurityIdentity,
    target: date,
    outcome_cutoff: date,
    grace_days: int,
) -> PricePoint | None:
    if outcome_cutoff < target:
        return None
    end = min(target + timedelta(days=grace_days), outcome_cutoff)
    rows = provider.adjusted_history(security, target, end)
    if any(not row.adjusted for row in rows):
        raise ValueError("market outcome requires split/dividend-adjusted price history")
    eligible = [row for row in rows if target <= row.date <= end]
    return min(eligible, key=lambda item: item.date) if eligible else None


def _observed_max_within(
    provider: HistoricalMarketProvider,
    security: SecurityIdentity,
    start: date,
    end: date,
) -> PricePoint | None:
    rows = provider.adjusted_history(security, start, end)
    if any(not row.adjusted for row in rows):
        raise ValueError("market outcome requires split/dividend-adjusted price history")
    eligible = [row for row in rows if start <= row.date <= end]
    if not eligible:
        return None
    return max(eligible, key=lambda item: (item.close, -item.date.toordinal()))


def label_market_outcome(
    provider: HistoricalMarketProvider,
    seed: MarketDistressSeed,
    outcome_cutoff: date,
    *,
    config: MarketOutcomeConfig | None = None,
) -> MarketObservableOutcome:
    """Label only outcomes directly observable in the historical market dataset.

    These fields deliberately do NOT mean corporate survival, bankruptcy avoidance,
    reorganization success, or legal survival of the pre-distress common stock.
    """

    config = config or MarketOutcomeConfig()
    if outcome_cutoff < seed.analysis_date:
        raise ValueError("outcome_cutoff cannot precede the distress analysis date")
    if not bool(getattr(provider, "survivorship_safe_universe", True)):
        raise ValueError(
            f"{provider.name} lacks point-in-time universe membership provenance; market outcomes would be survivorship-unsafe"
        )
    base = float(seed.adjusted_price.close)
    if base <= 0:
        raise ValueError("distress adjusted price must be positive for outcome multiples")

    target_12m = _add_years(seed.analysis_date, 1)
    target_3y = _add_years(seed.analysis_date, 3)
    warnings: list[str] = []

    active_12m: bool | None = None
    price_12m: PricePoint | None = None
    multiple_12m: float | None = None
    if outcome_cutoff >= target_12m:
        active_security = _security_at(provider, seed.security.security_id, target_12m)
        active_12m = active_security is not None
        if active_security is not None:
            price_12m = _first_adjusted_on_or_after(
                provider,
                active_security,
                target_12m,
                outcome_cutoff,
                config.horizon_grace_days,
            )
            if price_12m is not None:
                multiple_12m = float(price_12m.close) / base
            else:
                warnings.append(
                    "same security_id is active at +12m but no adjusted price was observed within the grace window"
                )
    else:
        warnings.append("+12m market outcome is not yet observable by outcome_cutoff")

    active_3y: bool | None = None
    price_3y: PricePoint | None = None
    multiple_3y: float | None = None
    max_3y: PricePoint | None = None
    max_multiple_3y: float | None = None
    if outcome_cutoff >= target_3y:
        active_security_3y = _security_at(provider, seed.security.security_id, target_3y)
        active_3y = active_security_3y is not None
        if active_security_3y is not None:
            price_3y = _first_adjusted_on_or_after(
                provider,
                active_security_3y,
                target_3y,
                outcome_cutoff,
                config.horizon_grace_days,
            )
            if price_3y is not None:
                multiple_3y = float(price_3y.close) / base
            else:
                warnings.append(
                    "same security_id is active at +3y but no adjusted price was observed within the grace window"
                )
        # Max observed multiple is a price-path fact, not a legal/common-survival label.
        max_3y = _observed_max_within(
            provider,
            seed.security,
            seed.analysis_date,
            target_3y,
        )
        if max_3y is not None:
            max_multiple_3y = float(max_3y.close) / base
    else:
        warnings.append("+3y market outcome is not yet observable by outcome_cutoff")

    return MarketObservableOutcome(
        security_id=seed.security.security_id,
        symbol_at_distress=seed.security.symbol,
        analysis_date=seed.analysis_date,
        outcome_cutoff=outcome_cutoff,
        same_security_active_12m=active_12m,
        adjusted_price_12m=price_12m,
        adjusted_price_multiple_12m=multiple_12m,
        same_security_active_3y=active_3y,
        adjusted_price_3y=price_3y,
        adjusted_price_multiple_3y=multiple_3y,
        max_adjusted_price_within_3y=max_3y,
        max_adjusted_price_multiple_within_3y=max_multiple_3y,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def label_market_outcome_cohort(
    provider: HistoricalMarketProvider,
    seeds: Iterable[MarketDistressSeed],
    outcome_cutoff: date,
    *,
    config: MarketOutcomeConfig | None = None,
) -> MarketOutcomeCohort:
    outcomes = tuple(
        label_market_outcome(provider, seed, outcome_cutoff, config=config)
        for seed in seeds
    )
    return MarketOutcomeCohort(
        provider=provider.name,
        outcome_cutoff=outcome_cutoff,
        case_count=len(outcomes),
        resolved_12m_membership_count=sum(item.same_security_active_12m is not None for item in outcomes),
        active_12m_count=sum(item.same_security_active_12m is True for item in outcomes),
        resolved_3y_membership_count=sum(item.same_security_active_3y is not None for item in outcomes),
        active_3y_count=sum(item.same_security_active_3y is True for item in outcomes),
        resolved_3y_endpoint_multiple_count=sum(item.adjusted_price_multiple_3y is not None for item in outcomes),
        resolved_3y_max_multiple_count=sum(item.max_adjusted_price_multiple_within_3y is not None for item in outcomes),
        outcomes=outcomes,
        semantics=(
            "same_security_active_* means the same permanent security_id appears in the point-in-time market universe at the horizon; it is not a corporate-survival or bankruptcy label",
            "adjusted_price_multiple_* is an observed adjusted-price ratio for the same security_id, not a guaranteed total shareholder return across cash acquisitions, cancellations, or reorganizations",
            "max_adjusted_price_multiple_within_3y is the maximum observed adjusted-price ratio before the 3y horizon and is not an endpoint return",
        ),
    )


def market_outcome_to_dict(outcome: MarketObservableOutcome) -> dict[str, object]:
    payload = asdict(outcome)
    payload["analysis_date"] = outcome.analysis_date.isoformat()
    payload["outcome_cutoff"] = outcome.outcome_cutoff.isoformat()
    for key in ("adjusted_price_12m", "adjusted_price_3y", "max_adjusted_price_within_3y"):
        point = payload[key]
        if isinstance(point, dict):
            point["date"] = point["date"].isoformat()
    return payload


def market_outcome_cohort_to_dict(cohort: MarketOutcomeCohort) -> dict[str, object]:
    return {
        "provider": cohort.provider,
        "outcome_cutoff": cohort.outcome_cutoff.isoformat(),
        "case_count": cohort.case_count,
        "resolved_12m_membership_count": cohort.resolved_12m_membership_count,
        "active_12m_count": cohort.active_12m_count,
        "resolved_3y_membership_count": cohort.resolved_3y_membership_count,
        "active_3y_count": cohort.active_3y_count,
        "resolved_3y_endpoint_multiple_count": cohort.resolved_3y_endpoint_multiple_count,
        "resolved_3y_max_multiple_count": cohort.resolved_3y_max_multiple_count,
        "semantics": list(cohort.semantics),
        "outcomes": [market_outcome_to_dict(item) for item in cohort.outcomes],
    }

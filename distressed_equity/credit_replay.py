from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import math
from pathlib import Path
import re
from typing import Iterable

from .bond_market import BondMarketObservation, HistoricalBondMarketProvider
from .replay import MarketDistressSeed, ReplayRun, seed_to_dict


@dataclass(frozen=True)
class CreditIdentityLink:
    security_id: str
    cusip: str | None
    isin: str | None
    start_date: date
    end_date: date | None
    source: str | None = None

    def active_on(self, as_of: date) -> bool:
        return self.start_date <= as_of and (self.end_date is None or as_of <= self.end_date)


@dataclass(frozen=True)
class CreditReplayConfig:
    lookback_days: int = 365
    max_staleness_days: int = 30
    price_stress_below_pct_par: float = 80.0
    yield_stress_at_or_above_pct: float = 15.0
    spread_stress_at_or_above_bps: float = 1_000.0

    def __post_init__(self) -> None:
        if self.lookback_days <= 0:
            raise ValueError("credit replay lookback_days must be positive")
        if self.max_staleness_days < 0:
            raise ValueError("credit replay max_staleness_days cannot be negative")
        for field in (
            "price_stress_below_pct_par",
            "yield_stress_at_or_above_pct",
            "spread_stress_at_or_above_bps",
        ):
            value = float(getattr(self, field))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"credit replay {field} must be finite and non-negative")


@dataclass(frozen=True)
class CreditInstrumentEvidence:
    cusip: str | None
    isin: str | None
    source_link: str | None
    observation: BondMarketObservation | None
    days_stale: int | None
    spread_bps: float | None
    fresh: bool
    signals: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class JointDistressSeed:
    equity: MarketDistressSeed
    linked_credit_instrument_count: int
    observed_credit_instrument_count: int
    fresh_credit_instrument_count: int
    stale_credit_instrument_count: int
    min_fresh_price_pct_par: float | None
    max_fresh_yield_pct: float | None
    max_fresh_spread_bps: float | None
    price_stress_instrument_count: int
    yield_stress_instrument_count: int
    spread_stress_instrument_count: int
    any_credit_stress: bool
    credit_evidence: tuple[CreditInstrumentEvidence, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class JointReplayRun:
    equity_provider: str
    credit_provider: str
    analysis_date: date
    equity_candidate_count: int
    candidates_with_credit_links: int
    candidates_with_fresh_credit: int
    candidates_with_credit_stress: int
    candidates: tuple[JointDistressSeed, ...]
    warnings: tuple[str, ...]


def _identifier(value: str | None) -> str | None:
    if not value:
        return None
    clean = re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()
    return clean or None


def _parse_date(value: str | None, field: str, row: int, *, required: bool) -> date | None:
    clean = str(value or "").strip()
    if not clean:
        if required:
            raise ValueError(f"credit links CSV row {row}: {field} is required")
        return None
    try:
        return date.fromisoformat(clean[:10])
    except ValueError as exc:
        raise ValueError(f"credit links CSV row {row}: {field} must be YYYY-MM-DD") from exc


class CsvCreditIdentityIndex:
    """Point-in-time equity security -> corporate-debt identifier links.

    This layer deliberately requires membership start/end columns and never joins
    by issuer name or ticker. `security_id` must be the same stable identifier used
    by the historical equity provider.
    """

    survivorship_safe = True

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._by_security: dict[str, tuple[CreditIdentityLink, ...]] = {}
        self._load()

    def _load(self) -> None:
        buckets: dict[str, list[CreditIdentityLink]] = {}
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            required = {"security_id", "start_date", "end_date"}
            missing = sorted(required - fields)
            if missing:
                raise ValueError(
                    "credit links CSV must include point-in-time membership columns: " + ", ".join(missing)
                )
            if not ({"cusip", "isin"} & fields):
                raise ValueError("credit links CSV must include cusip or isin")
            for row_number, raw in enumerate(reader, 2):
                security_id = str(raw.get("security_id") or "").strip()
                if not security_id:
                    continue
                cusip = _identifier(raw.get("cusip"))
                isin = _identifier(raw.get("isin"))
                if not cusip and not isin:
                    continue
                if cusip is not None and len(cusip) != 9:
                    raise ValueError(f"credit links CSV row {row_number}: CUSIP must contain 9 characters")
                if isin is not None and len(isin) != 12:
                    raise ValueError(f"credit links CSV row {row_number}: ISIN must contain 12 characters")
                start = _parse_date(raw.get("start_date"), "start_date", row_number, required=True)
                end = _parse_date(raw.get("end_date"), "end_date", row_number, required=False)
                assert start is not None
                if end is not None and end < start:
                    raise ValueError(f"credit links CSV row {row_number}: end_date precedes start_date")
                link = CreditIdentityLink(
                    security_id=security_id,
                    cusip=cusip,
                    isin=isin,
                    start_date=start,
                    end_date=end,
                    source=(str(raw.get("source") or "").strip() or None),
                )
                bucket = buckets.setdefault(security_id, [])
                if link not in bucket:
                    bucket.append(link)
        self._by_security = {
            security_id: tuple(sorted(rows, key=lambda item: (item.start_date, item.end_date or date.max, item.cusip or "", item.isin or "")))
            for security_id, rows in buckets.items()
        }

    def active_links(self, security_id: str, as_of: date) -> tuple[CreditIdentityLink, ...]:
        return tuple(
            link for link in self._by_security.get(security_id, ()) if link.active_on(as_of)
        )


def _spread_bps(observation: BondMarketObservation) -> float | None:
    if observation.spread_bps is not None and math.isfinite(float(observation.spread_bps)):
        return float(observation.spread_bps)
    if (
        observation.yield_pct is not None
        and observation.benchmark_yield_pct is not None
        and math.isfinite(float(observation.yield_pct))
        and math.isfinite(float(observation.benchmark_yield_pct))
    ):
        return (float(observation.yield_pct) - float(observation.benchmark_yield_pct)) * 100.0
    return None


def _latest_daily_observation(
    rows: Iterable[BondMarketObservation],
    *,
    analysis_date: date,
) -> BondMarketObservation | None:
    eligible = [row for row in rows if row.date <= analysis_date]
    if not eligible:
        return None
    latest_date = max(row.date for row in eligible)
    latest: list[BondMarketObservation] = []
    for row in eligible:
        if row.date != latest_date:
            continue
        if row not in latest:
            latest.append(row)
    if len(latest) > 1:
        raise ValueError(
            f"multiple distinct credit observations exist on {latest_date.isoformat()}; "
            "aggregate transaction-level bond data upstream before bulk replay"
        )
    return latest[0]


def _validate_identity(observation: BondMarketObservation, link: CreditIdentityLink) -> None:
    row_cusip = _identifier(observation.cusip)
    row_isin = _identifier(observation.isin)
    matched = False
    if link.cusip:
        if row_cusip is not None and row_cusip != link.cusip:
            raise ValueError(
                f"credit provider returned CUSIP {row_cusip} for requested link {link.cusip}"
            )
        matched = matched or row_cusip == link.cusip
    if link.isin:
        if row_isin is not None and row_isin != link.isin:
            raise ValueError(
                f"credit provider returned ISIN {row_isin} for requested link {link.isin}"
            )
        matched = matched or row_isin == link.isin
    if not matched:
        raise ValueError("credit provider observation does not match the point-in-time debt identifier link")


def _instrument_evidence(
    provider: HistoricalBondMarketProvider,
    link: CreditIdentityLink,
    analysis_date: date,
    config: CreditReplayConfig,
) -> CreditInstrumentEvidence:
    start = analysis_date - timedelta(days=config.lookback_days)
    rows = provider.observations(
        cusip=link.cusip,
        isin=link.isin,
        start=start,
        end=analysis_date,
    )
    for row in rows:
        _validate_identity(row, link)
        if row.date > analysis_date:
            raise ValueError("credit provider returned a future observation")
    current = _latest_daily_observation(rows, analysis_date=analysis_date)
    if current is None:
        return CreditInstrumentEvidence(
            cusip=link.cusip,
            isin=link.isin,
            source_link=link.source,
            observation=None,
            days_stale=None,
            spread_bps=None,
            fresh=False,
            signals=(),
            warnings=("no credit observation in configured lookback window",),
        )

    days_stale = (analysis_date - current.date).days
    fresh = days_stale <= config.max_staleness_days
    spread = _spread_bps(current)
    signals: list[str] = []
    warnings: list[str] = []
    if not fresh:
        warnings.append(
            f"latest credit observation is {days_stale} days before cutoff and excluded from current-stress gates"
        )
    else:
        if current.price_pct_par is not None and float(current.price_pct_par) < config.price_stress_below_pct_par:
            signals.append("price_credit_stress")
        if current.yield_pct is not None and float(current.yield_pct) >= config.yield_stress_at_or_above_pct:
            signals.append("yield_credit_stress")
        if spread is not None and spread >= config.spread_stress_at_or_above_bps:
            signals.append("spread_credit_stress")
    return CreditInstrumentEvidence(
        cusip=link.cusip,
        isin=link.isin,
        source_link=link.source,
        observation=current,
        days_stale=days_stale,
        spread_bps=spread,
        fresh=fresh,
        signals=tuple(signals),
        warnings=tuple(warnings),
    )


def enrich_equity_seed_with_credit(
    seed: MarketDistressSeed,
    provider: HistoricalBondMarketProvider,
    links: CsvCreditIdentityIndex,
    *,
    config: CreditReplayConfig | None = None,
) -> JointDistressSeed:
    config = config or CreditReplayConfig()
    active_links = links.active_links(seed.security.security_id, seed.analysis_date)
    evidence = tuple(
        _instrument_evidence(provider, link, seed.analysis_date, config)
        for link in active_links
    )
    observed = [item for item in evidence if item.observation is not None]
    fresh = [item for item in observed if item.fresh]
    stale = [item for item in observed if not item.fresh]

    prices = [
        float(item.observation.price_pct_par)
        for item in fresh
        if item.observation is not None and item.observation.price_pct_par is not None
    ]
    yields = [
        float(item.observation.yield_pct)
        for item in fresh
        if item.observation is not None and item.observation.yield_pct is not None
    ]
    spreads = [float(item.spread_bps) for item in fresh if item.spread_bps is not None]
    price_count = sum("price_credit_stress" in item.signals for item in fresh)
    yield_count = sum("yield_credit_stress" in item.signals for item in fresh)
    spread_count = sum("spread_credit_stress" in item.signals for item in fresh)
    warnings: list[str] = []
    if not active_links:
        warnings.append("no point-in-time debt identifier link for this equity security")
    elif not fresh:
        warnings.append("no fresh credit observation available for current-stress cross-check")

    return JointDistressSeed(
        equity=seed,
        linked_credit_instrument_count=len(active_links),
        observed_credit_instrument_count=len(observed),
        fresh_credit_instrument_count=len(fresh),
        stale_credit_instrument_count=len(stale),
        min_fresh_price_pct_par=min(prices) if prices else None,
        max_fresh_yield_pct=max(yields) if yields else None,
        max_fresh_spread_bps=max(spreads) if spreads else None,
        price_stress_instrument_count=price_count,
        yield_stress_instrument_count=yield_count,
        spread_stress_instrument_count=spread_count,
        any_credit_stress=bool(price_count or yield_count or spread_count),
        credit_evidence=evidence,
        warnings=tuple(warnings),
    )


def run_joint_credit_replay(
    equity_run: ReplayRun,
    provider: HistoricalBondMarketProvider,
    links: CsvCreditIdentityIndex,
    *,
    config: CreditReplayConfig | None = None,
) -> JointReplayRun:
    config = config or CreditReplayConfig()
    if not getattr(provider, "bulk_safe", False):
        raise ValueError(f"{provider.name} is not bulk-safe and cannot be used for cohort credit replay")
    candidates = tuple(
        enrich_equity_seed_with_credit(seed, provider, links, config=config)
        for seed in equity_run.candidates
    )
    return JointReplayRun(
        equity_provider=equity_run.provider,
        credit_provider=provider.name,
        analysis_date=equity_run.analysis_date,
        equity_candidate_count=len(equity_run.candidates),
        candidates_with_credit_links=sum(item.linked_credit_instrument_count > 0 for item in candidates),
        candidates_with_fresh_credit=sum(item.fresh_credit_instrument_count > 0 for item in candidates),
        candidates_with_credit_stress=sum(item.any_credit_stress for item in candidates),
        candidates=candidates,
        warnings=(),
    )


def joint_seed_to_dict(seed: JointDistressSeed) -> dict[str, object]:
    payload = asdict(seed)
    payload["equity"] = seed_to_dict(seed.equity)
    for item in payload["credit_evidence"]:
        observation = item.get("observation")
        if isinstance(observation, dict) and isinstance(observation.get("date"), date):
            observation["date"] = observation["date"].isoformat()
    return payload


def joint_replay_to_dict(run: JointReplayRun) -> dict[str, object]:
    return {
        "equity_provider": run.equity_provider,
        "credit_provider": run.credit_provider,
        "analysis_date": run.analysis_date.isoformat(),
        "equity_candidate_count": run.equity_candidate_count,
        "candidates_with_credit_links": run.candidates_with_credit_links,
        "candidates_with_fresh_credit": run.candidates_with_fresh_credit,
        "candidates_with_credit_stress": run.candidates_with_credit_stress,
        "warnings": list(run.warnings),
        "candidates": [joint_seed_to_dict(seed) for seed in run.candidates],
    }

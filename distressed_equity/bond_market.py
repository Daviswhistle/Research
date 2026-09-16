from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
import math
import re
from typing import Any, Iterable, Protocol

from .debt_instruments import DebtInstrumentLedger, DebtInstrumentSnapshot, DebtInstrumentVersion


@dataclass(frozen=True)
class BondMarketObservation:
    """One historical corporate-bond market observation.

    Prices are expressed as percent of par (100 = par). Yields and benchmark
    yields are percentage points (8.5 = 8.5%). `spread_bps`, when supplied by a
    provider, takes precedence over a yield-minus-benchmark derivation.
    """

    date: date
    cusip: str | None = None
    isin: str | None = None
    price_pct_par: float | None = None
    yield_pct: float | None = None
    volume: float | None = None
    benchmark_yield_pct: float | None = None
    spread_bps: float | None = None
    source: str | None = None


@dataclass(frozen=True)
class BondBenchmarkObservation:
    date: date
    tenor: str
    yield_pct: float
    source: str | None = None


@dataclass(frozen=True)
class ImpliedCreditRisk:
    recovery_rate: float
    horizon_years: float
    hazard_rate: float
    cumulative_default_probability: float


@dataclass(frozen=True)
class BondInstrumentMarketAssessment:
    stable_id: str
    instrument_name: str
    cusip: str | None
    isin: str | None
    analysis_date: date
    observation: BondMarketObservation | None
    days_stale: int | None
    lookback_observation_count: int
    price_peak_pct_par: float | None
    price_drawdown_from_peak: float | None
    yield_change_from_lookback_low_bps: float | None
    benchmark: BondBenchmarkObservation | None
    benchmark_days_stale: int | None
    spread_bps: float | None
    implied_credit_risk: tuple[ImpliedCreditRisk, ...]
    signals: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class BondMarketPacket:
    provider: str
    analysis_date: date
    lookback_days: int
    max_staleness_days: int
    max_benchmark_staleness_days: int
    probability_horizon_years: float
    recovery_rates: tuple[float, ...]
    assessments: tuple[BondInstrumentMarketAssessment, ...]
    warnings: tuple[str, ...]


class HistoricalBondMarketProvider(Protocol):
    """Vendor-neutral point-in-time corporate-bond market contract."""

    name: str
    bulk_safe: bool

    def observations(
        self,
        *,
        cusip: str | None,
        isin: str | None,
        start: date,
        end: date,
    ) -> tuple[BondMarketObservation, ...]: ...

    def benchmark_on_or_before(
        self,
        *,
        as_of: date,
        remaining_years: float,
    ) -> BondBenchmarkObservation | None: ...


def _clean_identifier(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()
    return cleaned or None


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(float(value))


def _validate_observation(observation: BondMarketObservation) -> None:
    if not (_clean_identifier(observation.cusip) or _clean_identifier(observation.isin)):
        raise ValueError("bond market observation requires CUSIP or ISIN")
    for field in (
        "price_pct_par",
        "yield_pct",
        "volume",
        "benchmark_yield_pct",
        "spread_bps",
    ):
        value = getattr(observation, field)
        if value is not None and not math.isfinite(float(value)):
            raise ValueError(f"bond market observation {field} must be finite")
    if observation.price_pct_par is not None and observation.price_pct_par < 0:
        raise ValueError("bond market price_pct_par cannot be negative")
    if observation.volume is not None and observation.volume < 0:
        raise ValueError("bond market volume cannot be negative")


def _validate_observation_identity(
    observation: BondMarketObservation,
    *,
    expected_cusip: str | None,
    expected_isin: str | None,
) -> None:
    row_cusip = _clean_identifier(observation.cusip)
    row_isin = _clean_identifier(observation.isin)
    matched = False
    if expected_cusip:
        if row_cusip is not None and row_cusip != expected_cusip:
            raise ValueError(
                f"bond market provider returned CUSIP {row_cusip} for requested CUSIP {expected_cusip}"
            )
        matched = matched or row_cusip == expected_cusip
    if expected_isin:
        if row_isin is not None and row_isin != expected_isin:
            raise ValueError(
                f"bond market provider returned ISIN {row_isin} for requested ISIN {expected_isin}"
            )
        matched = matched or row_isin == expected_isin
    if not matched:
        requested = "/".join(item for item in (expected_cusip, expected_isin) if item)
        returned = "/".join(item for item in (row_cusip, row_isin) if item)
        raise ValueError(
            f"bond market provider observation identity {returned or 'missing'} does not match requested stable debt identifier {requested}"
        )


def _daily_observations(rows: Iterable[BondMarketObservation]) -> tuple[BondMarketObservation, ...]:
    """Require a single semantic daily observation.

    Raw TRACE contains multiple executions per bond/day. This research layer does
    not choose a hidden aggregation rule. Exact duplicate rows may be collapsed,
    but distinct same-day observations must be aggregated upstream with an
    explicit policy such as last disseminated trade or VWAP.
    """

    by_date: dict[date, list[BondMarketObservation]] = {}
    for row in rows:
        candidates = by_date.setdefault(row.date, [])
        if row not in candidates:
            candidates.append(row)
    ambiguous = {dt: items for dt, items in by_date.items() if len(items) > 1}
    if ambiguous:
        dates = ", ".join(sorted(dt.isoformat() for dt in ambiguous))
        raise ValueError(
            "multiple distinct bond observations exist on the same date "
            f"({dates}); aggregate transaction-level data upstream with an explicit daily policy"
        )
    return tuple(by_date[dt][0] for dt in sorted(by_date))


def _versions_before_cutoff(
    ledger: DebtInstrumentLedger,
    analysis_date: date,
) -> dict[str, list[DebtInstrumentVersion]]:
    grouped: dict[str, list[DebtInstrumentVersion]] = {}
    for version in ledger.versions:
        if version.snapshot.as_of_date <= analysis_date:
            grouped.setdefault(version.stable_id, []).append(version)
    for versions in grouped.values():
        versions.sort(key=lambda item: (item.snapshot.as_of_date, item.snapshot.source_accession))
    return grouped


def _snapshot_by_stable_id(
    ledger: DebtInstrumentLedger,
    analysis_date: date,
) -> dict[str, DebtInstrumentSnapshot]:
    grouped = _versions_before_cutoff(ledger, analysis_date)
    output: dict[str, DebtInstrumentSnapshot] = {}
    for stable_id, versions in grouped.items():
        latest_date = versions[-1].snapshot.as_of_date
        latest = [item.snapshot for item in versions if item.snapshot.as_of_date == latest_date]
        if len(latest) != 1:
            continue
        output[stable_id] = latest[0]
    return output


def _market_identifiers_for_stable_id(
    ledger: DebtInstrumentLedger,
    *,
    stable_id: str,
    analysis_date: date,
) -> tuple[str | None, str | None]:
    """Carry verified identifiers forward only from versions known by the cutoff.

    A later filing may omit the CUSIP/ISIN while the stable legal instrument is
    unchanged. Market linkage should retain the established identity, but it must
    never use an identifier first observed after the research cutoff.
    """

    versions = [
        item for item in ledger.versions
        if item.stable_id == stable_id and item.snapshot.as_of_date <= analysis_date
    ]
    cusips = {
        cleaned for item in versions
        if (cleaned := _clean_identifier(item.snapshot.cusip)) is not None
    }
    isins = {
        cleaned for item in versions
        if (cleaned := _clean_identifier(item.snapshot.isin)) is not None
    }

    prefix, separator, raw_identifier = stable_id.partition(":")
    if separator and raw_identifier:
        cleaned_stable = _clean_identifier(raw_identifier)
        if prefix.upper() == "CUSIP" and cleaned_stable:
            cusips.add(cleaned_stable)
        elif prefix.upper() == "ISIN" and cleaned_stable:
            isins.add(cleaned_stable)

    if len(cusips) > 1:
        raise ValueError(f"stable debt ID {stable_id} has conflicting historical CUSIPs before cutoff: {sorted(cusips)}")
    if len(isins) > 1:
        raise ValueError(f"stable debt ID {stable_id} has conflicting historical ISINs before cutoff: {sorted(isins)}")
    return next(iter(cusips), None), next(iter(isins), None)


def _remaining_years(snapshot: DebtInstrumentSnapshot, as_of: date) -> tuple[float | None, str | None]:
    if snapshot.maturity_date is not None:
        days = (snapshot.maturity_date - as_of).days
        if days <= 0:
            return None, "instrument maturity is on or before the market observation date"
        return days / 365.25, None
    if snapshot.maturity_year is not None:
        years = snapshot.maturity_year - as_of.year
        if years <= 0:
            return None, "year-only maturity is on or before the market observation year"
        return float(years), "Treasury tenor uses year-only maturity precision"
    return None, "instrument maturity is unavailable; Treasury benchmark cannot be tenor-matched"


def _spread_bps(
    observation: BondMarketObservation,
    benchmark: BondBenchmarkObservation | None,
) -> float | None:
    if _finite(observation.spread_bps):
        return float(observation.spread_bps)
    benchmark_yield = observation.benchmark_yield_pct
    if benchmark_yield is None and benchmark is not None:
        benchmark_yield = benchmark.yield_pct
    if not _finite(observation.yield_pct) or not _finite(benchmark_yield):
        return None
    return (float(observation.yield_pct) - float(benchmark_yield)) * 100.0


def spread_implied_credit_risk(
    spread_bps: float,
    *,
    recovery_rate: float,
    horizon_years: float,
) -> ImpliedCreditRisk:
    if not math.isfinite(spread_bps):
        raise ValueError("spread_bps must be finite")
    if not 0 <= recovery_rate < 1:
        raise ValueError("recovery_rate must be in [0, 1)")
    if not math.isfinite(horizon_years) or horizon_years <= 0:
        raise ValueError("horizon_years must be positive and finite")
    spread_decimal = max(float(spread_bps), 0.0) / 10_000.0
    hazard = spread_decimal / (1.0 - recovery_rate)
    probability = 1.0 - math.exp(-hazard * horizon_years)
    return ImpliedCreditRisk(
        recovery_rate=float(recovery_rate),
        horizon_years=float(horizon_years),
        hazard_rate=hazard,
        cumulative_default_probability=probability,
    )


def _signals(
    observation: BondMarketObservation,
    *,
    spread_bps: float | None,
    days_stale: int,
    max_staleness_days: int,
) -> tuple[str, ...]:
    signals: list[str] = []
    price = observation.price_pct_par
    if price is not None:
        if price < 40:
            signals.append("price_below_40_pct_par")
        elif price < 60:
            signals.append("price_below_60_pct_par")
        elif price < 80:
            signals.append("price_below_80_pct_par")
    if spread_bps is not None:
        if spread_bps >= 2_000:
            signals.append("spread_at_or_above_2000_bps")
        elif spread_bps >= 1_000:
            signals.append("spread_at_or_above_1000_bps")
    if days_stale > max_staleness_days:
        signals.append("stale_bond_market_observation")
    return tuple(signals)


def _empty_assessment(
    *,
    stable_id: str,
    snapshot: DebtInstrumentSnapshot,
    analysis_date: date,
    cusip: str | None,
    isin: str | None,
    warning: str,
) -> BondInstrumentMarketAssessment:
    return BondInstrumentMarketAssessment(
        stable_id=stable_id,
        instrument_name=snapshot.name,
        cusip=cusip,
        isin=isin,
        analysis_date=analysis_date,
        observation=None,
        days_stale=None,
        lookback_observation_count=0,
        price_peak_pct_par=None,
        price_drawdown_from_peak=None,
        yield_change_from_lookback_low_bps=None,
        benchmark=None,
        benchmark_days_stale=None,
        spread_bps=None,
        implied_credit_risk=(),
        signals=(),
        warnings=(warning,),
    )


def _assessment(
    provider: HistoricalBondMarketProvider,
    *,
    stable_id: str,
    snapshot: DebtInstrumentSnapshot,
    cusip: str | None,
    isin: str | None,
    analysis_date: date,
    lookback_days: int,
    max_staleness_days: int,
    max_benchmark_staleness_days: int,
    recovery_rates: tuple[float, ...],
    probability_horizon_years: float,
) -> BondInstrumentMarketAssessment:
    warnings: list[str] = []
    latest_cusip = _clean_identifier(snapshot.cusip)
    latest_isin = _clean_identifier(snapshot.isin)
    cusip = _clean_identifier(cusip)
    isin = _clean_identifier(isin)
    if not cusip and not isin:
        return _empty_assessment(
            stable_id=stable_id,
            snapshot=snapshot,
            analysis_date=analysis_date,
            cusip=None,
            isin=None,
            warning="no explicit CUSIP/ISIN was verified by the cutoff; bond market data was not fuzzy-matched by name",
        )
    if (cusip and not latest_cusip) or (isin and not latest_isin):
        warnings.append(
            "bond market identifier was carried forward from the verified stable debt identity/history because the latest snapshot omitted it"
        )

    start = analysis_date - timedelta(days=lookback_days)
    raw_rows = provider.observations(cusip=cusip, isin=isin, start=start, end=analysis_date)
    filtered: list[BondMarketObservation] = []
    for row in raw_rows:
        _validate_observation(row)
        _validate_observation_identity(row, expected_cusip=cusip, expected_isin=isin)
        if row.date > analysis_date:
            raise ValueError("bond market provider returned a future observation")
        if start <= row.date <= analysis_date:
            filtered.append(row)
    rows = _daily_observations(filtered)
    if not rows:
        empty = _empty_assessment(
            stable_id=stable_id,
            snapshot=snapshot,
            analysis_date=analysis_date,
            cusip=cusip,
            isin=isin,
            warning="no bond market observation was available in the configured lookback window",
        )
        if not warnings:
            return empty
        return BondInstrumentMarketAssessment(
            **{**asdict(empty), "warnings": tuple(dict.fromkeys((*warnings, *empty.warnings)))}
        )

    current = rows[-1]
    days_stale = (analysis_date - current.date).days
    prices = [float(row.price_pct_par) for row in rows if _finite(row.price_pct_par)]
    peak = max(prices) if prices else None
    drawdown = None
    if peak is not None and peak > 0 and current.price_pct_par is not None:
        drawdown = 1.0 - float(current.price_pct_par) / peak

    yields = [float(row.yield_pct) for row in rows if _finite(row.yield_pct)]
    yield_change = None
    if yields and current.yield_pct is not None:
        yield_change = (float(current.yield_pct) - min(yields)) * 100.0

    remaining, maturity_warning = _remaining_years(snapshot, current.date)
    benchmark = None
    benchmark_days_stale = None
    if (
        current.spread_bps is None
        and current.benchmark_yield_pct is None
        and remaining is not None
    ):
        benchmark = provider.benchmark_on_or_before(as_of=current.date, remaining_years=remaining)
        if benchmark is not None:
            if benchmark.date > current.date:
                raise ValueError("bond benchmark provider returned a future observation")
            benchmark_days_stale = (current.date - benchmark.date).days
    if maturity_warning:
        warnings.append(maturity_warning)
    if days_stale > max_staleness_days:
        warnings.append(
            f"latest bond observation is {days_stale} days before cutoff; treat price/yield as stale"
        )

    benchmark_for_spread = benchmark
    if (
        benchmark is not None
        and benchmark_days_stale is not None
        and benchmark_days_stale > max_benchmark_staleness_days
    ):
        warnings.append(
            f"Treasury benchmark is {benchmark_days_stale} days older than the bond observation; "
            "credit spread and spread-implied probability were left unresolved"
        )
        benchmark_for_spread = None

    spread = _spread_bps(current, benchmark_for_spread)
    risk: tuple[ImpliedCreditRisk, ...] = ()
    if spread is not None:
        risk = tuple(
            spread_implied_credit_risk(
                spread,
                recovery_rate=recovery,
                horizon_years=probability_horizon_years,
            )
            for recovery in recovery_rates
        )
        warnings.append(
            "spread-implied probabilities are constant-hazard risk-neutral stress proxies, not physical default forecasts; spread can include liquidity and risk premia"
        )
    else:
        warnings.append("credit spread unavailable; no spread-implied probability proxy was calculated")

    return BondInstrumentMarketAssessment(
        stable_id=stable_id,
        instrument_name=snapshot.name,
        cusip=cusip,
        isin=isin,
        analysis_date=analysis_date,
        observation=current,
        days_stale=days_stale,
        lookback_observation_count=len(rows),
        price_peak_pct_par=peak,
        price_drawdown_from_peak=drawdown,
        yield_change_from_lookback_low_bps=yield_change,
        benchmark=benchmark,
        benchmark_days_stale=benchmark_days_stale,
        spread_bps=spread,
        implied_credit_risk=risk,
        signals=_signals(
            current,
            spread_bps=spread,
            days_stale=days_stale,
            max_staleness_days=max_staleness_days,
        ),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def build_bond_market_packet(
    provider: HistoricalBondMarketProvider,
    ledger: DebtInstrumentLedger,
    analysis_date: date,
    *,
    lookback_days: int = 365,
    max_staleness_days: int = 30,
    max_benchmark_staleness_days: int = 7,
    recovery_rates: Iterable[float] = (0.20, 0.40, 0.60),
    probability_horizon_years: float = 1.0,
) -> BondMarketPacket:
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    if max_staleness_days < 0:
        raise ValueError("max_staleness_days cannot be negative")
    if max_benchmark_staleness_days < 0:
        raise ValueError("max_benchmark_staleness_days cannot be negative")
    if not math.isfinite(probability_horizon_years) or probability_horizon_years <= 0:
        raise ValueError("probability_horizon_years must be positive and finite")
    recoveries = tuple(float(item) for item in recovery_rates)
    if not recoveries:
        raise ValueError("at least one recovery rate is required")
    for recovery in recoveries:
        if not math.isfinite(recovery) or not 0 <= recovery < 1:
            raise ValueError("recovery rates must be finite and in [0, 1)")

    snapshots = _snapshot_by_stable_id(ledger, analysis_date)
    warnings: list[str] = []
    ledger_ids = {version.stable_id for version in ledger.versions if version.snapshot.as_of_date <= analysis_date}
    ambiguous = sorted(ledger_ids - set(snapshots))
    if ambiguous:
        warnings.append(
            "latest same-date debt versions are ambiguous and were not market-linked: " + ", ".join(ambiguous)
        )

    assessments = tuple(
        _assessment(
            provider,
            stable_id=stable_id,
            snapshot=snapshot,
            cusip=_market_identifiers_for_stable_id(
                ledger, stable_id=stable_id, analysis_date=analysis_date
            )[0],
            isin=_market_identifiers_for_stable_id(
                ledger, stable_id=stable_id, analysis_date=analysis_date
            )[1],
            analysis_date=analysis_date,
            lookback_days=lookback_days,
            max_staleness_days=max_staleness_days,
            max_benchmark_staleness_days=max_benchmark_staleness_days,
            recovery_rates=recoveries,
            probability_horizon_years=probability_horizon_years,
        )
        for stable_id, snapshot in sorted(snapshots.items())
    )
    if not assessments:
        warnings.append("debt ledger contained no unambiguous instrument observation on or before the cutoff")
    return BondMarketPacket(
        provider=provider.name,
        analysis_date=analysis_date,
        lookback_days=lookback_days,
        max_staleness_days=max_staleness_days,
        max_benchmark_staleness_days=max_benchmark_staleness_days,
        probability_horizon_years=float(probability_horizon_years),
        recovery_rates=recoveries,
        assessments=assessments,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def bond_market_packet_to_dict(packet: BondMarketPacket) -> dict[str, Any]:
    def convert(value: Any) -> Any:
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, tuple):
            return [convert(item) for item in value]
        if isinstance(value, list):
            return [convert(item) for item in value]
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        if hasattr(value, "__dataclass_fields__"):
            return convert(asdict(value))
        return value

    return convert(packet)

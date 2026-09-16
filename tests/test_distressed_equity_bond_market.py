from __future__ import annotations

from datetime import date
import math

from distressed_equity.bond_market import (
    BondBenchmarkObservation,
    BondMarketObservation,
    build_bond_market_packet,
    spread_implied_credit_risk,
)
from distressed_equity.csv_bond_market import CsvBondMarketProvider
from distressed_equity.debt_instruments import (
    DebtInstrumentLedger,
    DebtInstrumentSnapshot,
    DebtInstrumentVersion,
)
from distressed_equity.eodhd_bonds import EodhdBondProvider


def ledger(*, cusip="111111AA1", isin=None, maturity_date=date(2028, 6, 15)):
    snapshot = DebtInstrumentSnapshot(
        as_of_date=date(2022, 9, 30),
        source_accession="old",
        name="5% Senior Notes",
        instrument_type="notes",
        principal=1_000_000_000.0,
        maturity_date=maturity_date,
        maturity_year=maturity_date.year if maturity_date else 2028,
        coupon_pct=5.0,
        secured=False,
        currency="USD",
        cusip=cusip,
        isin=isin,
    )
    return DebtInstrumentLedger(
        versions=(DebtInstrumentVersion("CUSIP:111111AA1", snapshot, "exact_identifier"),),
        changes=(),
        unmatched_versions=(),
        warnings=(),
    )


class FakeProvider:
    name = "fake_bonds"
    bulk_safe = True

    def observations(self, *, cusip, isin, start, end):
        return (
            BondMarketObservation(
                date=date(2022, 1, 3), cusip=cusip, price_pct_par=100.0,
                yield_pct=5.0, volume=1_000_000, source="fake",
            ),
            BondMarketObservation(
                date=date(2022, 12, 29), cusip=cusip, price_pct_par=55.0,
                yield_pct=18.0, volume=500_000, source="fake",
            ),
        )

    def benchmark_on_or_before(self, *, as_of, remaining_years):
        return BondBenchmarkObservation(
            date=date(2022, 12, 29), tenor="5Y", yield_pct=4.0, source="fake treasury"
        )


def test_spread_implied_credit_risk_is_recovery_sensitive():
    result = spread_implied_credit_risk(1200.0, recovery_rate=0.40, horizon_years=1.0)
    assert math.isclose(result.hazard_rate, 0.20)
    assert math.isclose(result.cumulative_default_probability, 1 - math.exp(-0.20))

    lower_recovery = spread_implied_credit_risk(1200.0, recovery_rate=0.20, horizon_years=1.0)
    higher_recovery = spread_implied_credit_risk(1200.0, recovery_rate=0.60, horizon_years=1.0)
    assert lower_recovery.cumulative_default_probability < higher_recovery.cumulative_default_probability


def test_bond_market_packet_uses_explicit_id_and_market_spread_not_price_as_probability():
    packet = build_bond_market_packet(
        FakeProvider(), ledger(), date(2022, 12, 31),
        recovery_rates=(0.20, 0.40, 0.60), probability_horizon_years=1.0,
    )
    assert len(packet.assessments) == 1
    item = packet.assessments[0]
    assert item.observation.price_pct_par == 55.0
    assert item.price_peak_pct_par == 100.0
    assert math.isclose(item.price_drawdown_from_peak, 0.45)
    assert item.spread_bps == 1400.0
    assert item.days_stale == 2
    assert item.signals == ("price_below_60_pct_par", "spread_at_or_above_1000_bps")
    assert len(item.implied_credit_risk) == 3
    assert all(0 <= row.cumulative_default_probability < 1 for row in item.implied_credit_risk)
    assert any("not physical default forecasts" in warning for warning in item.warnings)


def test_bond_market_does_not_fuzzy_match_instrument_without_explicit_identifier():
    snapshot = DebtInstrumentSnapshot(
        as_of_date=date(2022, 9, 30), source_accession="x", name="Senior Notes",
        maturity_year=2028,
    )
    no_id_ledger = DebtInstrumentLedger(
        versions=(DebtInstrumentVersion("DEBT:abc", snapshot, "new"),),
        changes=(), unmatched_versions=(), warnings=(),
    )
    packet = build_bond_market_packet(FakeProvider(), no_id_ledger, date(2022, 12, 31))
    item = packet.assessments[0]
    assert item.observation is None
    assert "no explicit CUSIP/ISIN" in item.warnings[0]


def test_csv_bond_provider_links_cusip_and_derives_spread_from_explicit_benchmark(tmp_path):
    csv_path = tmp_path / "bonds.csv"
    csv_path.write_text(
        "date,cusip,price_pct_par,yield_pct,volume,benchmark_yield_pct,source\n"
        "2022-12-29,111111AA1,58,16.5,250000,4.25,TRACE normalized\n",
        encoding="utf-8",
    )
    provider = CsvBondMarketProvider(csv_path)
    packet = build_bond_market_packet(provider, ledger(), date(2022, 12, 31))
    item = packet.assessments[0]
    assert item.observation.source == "TRACE normalized"
    assert item.spread_bps == 1225.0
    assert item.benchmark is None  # benchmark was carried by the normalized observation itself


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if "/eod/" in url:
            return FakeResponse([
                {"date": "2022-12-28", "price": 62.5, "yield": 15.0, "volume": 100000},
                {"date": "2022-12-30", "price": 60.0, "yield": 16.0, "volume": 120000},
            ])
        year = int(params["filter[year]"])
        if year == 2022:
            return FakeResponse({"data": [
                {"date": "2022-12-30", "tenor": "2Y", "rate": 4.4},
                {"date": "2022-12-30", "tenor": "5Y", "rate": 4.0},
                {"date": "2022-12-30", "tenor": "10Y", "rate": 3.9},
            ]})
        return FakeResponse({"data": []})


def test_eodhd_bond_provider_uses_explicit_identifier_and_tenor_matched_treasury():
    session = FakeSession()
    provider = EodhdBondProvider(api_key="test", session=session)
    packet = build_bond_market_packet(provider, ledger(), date(2022, 12, 31))
    item = packet.assessments[0]
    assert item.observation.date == date(2022, 12, 30)
    assert item.observation.price_pct_par == 60.0
    assert item.benchmark.tenor == "5Y"
    assert item.benchmark.yield_pct == 4.0
    assert item.spread_bps == 1200.0
    assert any(url.endswith("/eod/111111AA1.BOND") for url, _ in session.calls)
    assert any(url.endswith("/ust/yield-rates") for url, _ in session.calls)


def test_stale_bond_observation_is_flagged_not_silently_treated_as_cutoff_price():
    class StaleProvider(FakeProvider):
        def observations(self, *, cusip, isin, start, end):
            return (BondMarketObservation(
                date=date(2022, 10, 1), cusip=cusip, price_pct_par=50, yield_pct=20,
                benchmark_yield_pct=4, source="stale",
            ),)

    packet = build_bond_market_packet(
        StaleProvider(), ledger(), date(2022, 12, 31), max_staleness_days=30,
    )
    item = packet.assessments[0]
    assert item.days_stale == 91
    assert "stale_bond_market_observation" in item.signals
    assert any("91 days" in warning for warning in item.warnings)

from __future__ import annotations

from datetime import date

import pytest

from distressed_equity.bond_market import (
    BondBenchmarkObservation,
    BondMarketObservation,
    build_bond_market_packet,
)
from distressed_equity.debt_instruments import (
    DebtInstrumentLedger,
    DebtInstrumentSnapshot,
    DebtInstrumentVersion,
)
from distressed_equity.eodhd_bonds import EodhdBondProvider


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def ledger(*, cusip="111111AA1", isin=None):
    snapshot = DebtInstrumentSnapshot(
        as_of_date=date(2022, 9, 30),
        source_accession="acc",
        name="5% Senior Notes",
        instrument_type="notes",
        principal=1_000_000_000.0,
        maturity_date=date(2028, 6, 15),
        maturity_year=2028,
        coupon_pct=5.0,
        secured=False,
        currency="USD",
        cusip=cusip,
        isin=isin,
    )
    stable_id = f"ISIN:{isin}" if isin else f"CUSIP:{cusip}"
    return DebtInstrumentLedger(
        versions=(DebtInstrumentVersion(stable_id, snapshot, "exact_identifier"),),
        changes=(),
        unmatched_versions=(),
        warnings=(),
    )


def test_eodhd_rejects_partially_incompatible_bond_history():
    class MixedShapeSession:
        def get(self, url, params=None, timeout=None):
            if "/eod/" in url:
                return FakeResponse([
                    {"date": "2022-12-29", "price": 61.0, "yield": 15.0, "volume": 100000},
                    {
                        "date": "2022-12-30",
                        "open": 60.0,
                        "high": 62.0,
                        "low": 59.0,
                        "close": 60.5,
                        "volume": 120000,
                    },
                ])
            return FakeResponse({"data": []})

    provider = EodhdBondProvider(api_key="test", session=MixedShapeSession())
    with pytest.raises(RuntimeError) as excinfo:
        provider.observations(
            cusip="111111AA1",
            isin=None,
            start=date(2022, 12, 1),
            end=date(2022, 12, 31),
        )
    message = str(excinfo.value)
    assert "documented bond price/yield shape" in message
    assert "incompatible rows" in message
    assert "close" in message


def test_eodhd_does_not_hide_incompatible_identifier_behind_empty_fallback():
    isin = "US111111AA11"

    class FallbackSession:
        def get(self, url, params=None, timeout=None):
            if f"/eod/{isin}.BOND" in url:
                return FakeResponse([
                    {"date": "2022-12-30", "close": 60.0, "volume": 100000},
                ])
            if "/eod/111111AA1.BOND" in url:
                return FakeResponse([])
            return FakeResponse({"data": []})

    provider = EodhdBondProvider(api_key="test", session=FallbackSession())
    with pytest.raises(RuntimeError) as excinfo:
        provider.observations(
            cusip="111111AA1",
            isin=isin,
            start=date(2022, 12, 1),
            end=date(2022, 12, 31),
        )
    message = str(excinfo.value)
    assert isin in message
    assert "bond history failed for explicit identifier" in message


def test_stale_treasury_curve_leaves_spread_and_probability_unresolved():
    class StaleBenchmarkProvider:
        name = "stale_benchmark"
        bulk_safe = True

        def observations(self, *, cusip, isin, start, end):
            return (
                BondMarketObservation(
                    date=date(2022, 12, 30),
                    cusip=cusip,
                    price_pct_par=60.0,
                    yield_pct=16.0,
                    volume=100000,
                    source="bond",
                ),
            )

        def benchmark_on_or_before(self, *, as_of, remaining_years):
            return BondBenchmarkObservation(
                date=date(2022, 12, 1),
                tenor="5Y",
                yield_pct=4.0,
                source="stale treasury",
            )

    packet = build_bond_market_packet(
        StaleBenchmarkProvider(),
        ledger(),
        date(2022, 12, 31),
        max_benchmark_staleness_days=7,
    )
    item = packet.assessments[0]
    assert item.benchmark is not None
    assert item.benchmark_days_stale == 29
    assert item.spread_bps is None
    assert item.implied_credit_risk == ()
    assert any("29 days older" in warning for warning in item.warnings)
    assert any("left unresolved" in warning for warning in item.warnings)

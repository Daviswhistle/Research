from __future__ import annotations

from datetime import date

import pytest

from distressed_equity.csv_market import CsvMarketProvider
from distressed_equity.market import PricePoint, SecurityIdentity, adjusted_peak_and_drawdown
from distressed_equity.replay import (
    DistressScanConfig,
    MarketDistressSeed,
    apply_market_seed_to_prefill,
    run_historical_replay,
)


def test_adjusted_drawdown_refuses_unadjusted_history():
    points = (
        PricePoint("1", "AAA", date(2022, 1, 1), 100.0, adjusted=False),
        PricePoint("1", "AAA", date(2022, 12, 1), 20.0, adjusted=False),
    )
    current, peak, drawdown = adjusted_peak_and_drawdown(points, date(2022, 12, 31))
    assert current is None
    assert peak is None
    assert drawdown is None


def test_csv_provider_reconstructs_historical_universe_with_later_delisting(tmp_path):
    securities = tmp_path / "securities.csv"
    prices = tmp_path / "prices.csv"
    securities.write_text(
        "security_id,symbol,name,exchange,asset_type,start_date,end_date,status\n"
        "p1,ALIVE,Alive Co,NASDAQ,Stock,2019-01-01,,active\n"
        "p2,DEAD,Later Delisted Co,NYSE,Stock,2018-01-01,2023-06-30,delisted\n",
        encoding="utf-8",
    )
    prices.write_text(
        "security_id,date,close,adjusted_close,volume\n"
        "p1,2022-01-07,100,100,1000\n"
        "p1,2022-12-30,25,25,1000\n"
        "p2,2022-01-07,50,50,1000\n"
        "p2,2022-12-30,10,10,1000\n",
        encoding="utf-8",
    )
    provider = CsvMarketProvider(securities, prices)
    symbols_2022 = {security.symbol for security in provider.universe(date(2022, 12, 31))}
    symbols_2024 = {security.symbol for security in provider.universe(date(2024, 1, 1))}
    assert "DEAD" in symbols_2022
    assert "DEAD" not in symbols_2024


def test_replay_selects_large_adjusted_drawdown(tmp_path):
    securities = tmp_path / "securities.csv"
    prices = tmp_path / "prices.csv"
    securities.write_text(
        "security_id,symbol,name,exchange,asset_type,start_date,end_date,status\n"
        "p1,CRASH,Crash Co,NASDAQ,Stock,2019-01-01,,active\n"
        "p2,OK,Okay Co,NYSE,Stock,2019-01-01,,active\n",
        encoding="utf-8",
    )
    prices.write_text(
        "security_id,date,close,adjusted_close,volume\n"
        "p1,2021-01-08,100,100,1000\n"
        "p1,2022-12-30,20,20,1000\n"
        "p2,2021-01-08,100,100,1000\n"
        "p2,2022-12-30,80,80,1000\n",
        encoding="utf-8",
    )
    run = run_historical_replay(
        CsvMarketProvider(securities, prices),
        date(2022, 12, 31),
        config=DistressScanConfig(min_adjusted_drawdown=0.60),
    )
    assert [seed.security.symbol for seed in run.candidates] == ["CRASH"]
    assert run.candidates[0].adjusted_drawdown_from_peak == pytest.approx(0.80)


def test_non_bulk_provider_requires_symbols_or_explicit_override():
    class SlowProvider:
        name = "slow"
        bulk_safe = False

        def universe(self, as_of):
            return ()

        def price_on_or_before(self, security, as_of):
            return None

        def adjusted_history(self, security, start, end):
            return ()

    with pytest.raises(ValueError, match="not bulk-safe"):
        run_historical_replay(SlowProvider(), date(2022, 12, 31))


def test_market_seed_prefills_price_but_keeps_peak_market_cap_unresolved():
    security = SecurityIdentity(
        security_id="p1",
        symbol="TEST",
        name="Test Co",
        exchange="NASDAQ",
        asset_type="Stock",
        provider="csv",
    )
    seed = MarketDistressSeed(
        security=security,
        analysis_date=date(2022, 12, 31),
        raw_price=PricePoint("p1", "TEST", date(2022, 12, 30), 3.0, adjusted=False),
        adjusted_price=PricePoint("p1", "TEST", date(2022, 12, 30), 3.0, adjusted=True),
        peak_adjusted_price=PricePoint("p1", "TEST", date(2021, 11, 5), 30.0, adjusted=True),
        adjusted_drawdown_from_peak=0.90,
        lookback_start=date(2017, 12, 29),
    )
    packet = {
        "screening_candidate_draft": {
            "capital_structure": {"current_price": None, "current_shares": 100},
        },
        "unresolved_required_fields": [
            "point-in-time share price and peak market capitalization",
        ],
    }
    enriched = apply_market_seed_to_prefill(packet, seed)
    draft = enriched["screening_candidate_draft"]
    assert draft["capital_structure"]["current_price"] == 3.0
    assert draft["price_drawdown_from_peak"] == pytest.approx(0.90)
    assert enriched["market_context"]["cutoff_market_cap"] == 300.0
    assert any(
        "peak market capitalization remains unresolved" in item
        for item in enriched["unresolved_required_fields"]
    )

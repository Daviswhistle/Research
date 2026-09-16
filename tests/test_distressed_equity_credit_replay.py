from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from distressed_equity.credit_replay import (
    CreditReplayConfig,
    CsvCreditIdentityIndex,
    enrich_equity_seed_with_credit,
    run_joint_credit_replay,
)
from distressed_equity.csv_bond_market import CsvBondMarketProvider
from distressed_equity.market import PricePoint, SecurityIdentity
from distressed_equity.replay import MarketDistressSeed, ReplayRun


def _seed(*, security_id="sec-1", symbol="TEST", cutoff=date(2022, 12, 31)):
    security = SecurityIdentity(
        security_id=security_id,
        symbol=symbol,
        name="Test Co",
        exchange="NYSE",
        asset_type="stock",
        start_date=date(2010, 1, 1),
        provider="csv",
    )
    raw = PricePoint(security_id, symbol, cutoff, 5.0, False)
    current = PricePoint(security_id, symbol, cutoff, 5.0, True)
    peak = PricePoint(security_id, symbol, date(2021, 12, 31), 25.0, True)
    return MarketDistressSeed(
        security=security,
        analysis_date=cutoff,
        raw_price=raw,
        adjusted_price=current,
        peak_adjusted_price=peak,
        adjusted_drawdown_from_peak=0.80,
        lookback_start=date(2017, 12, 29),
    )


def _equity_run(seed=None):
    seed = seed or _seed()
    return ReplayRun(
        provider="csv",
        analysis_date=seed.analysis_date,
        historical_universe_size=1,
        scanned_security_count=1,
        candidates=(seed,),
        skipped=(),
        survivorship_guard="safe",
    )


def _write_links(path: Path, rows):
    fields = ["security_id", "cusip", "isin", "start_date", "end_date", "source"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(",".join(fields) + "\n")
        for row in rows:
            handle.write(",".join(str(row.get(field, "")) for field in fields) + "\n")


def _write_bonds(path: Path, rows):
    fields = ["date", "cusip", "price_pct_par", "yield_pct", "benchmark_yield_pct", "spread_bps", "source"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(",".join(fields) + "\n")
        for row in rows:
            handle.write(",".join(str(row.get(field, "")) for field in fields) + "\n")


def test_joint_replay_attaches_fresh_credit_stress_without_collapsing_signals(tmp_path):
    links_path = tmp_path / "links.csv"
    bonds_path = tmp_path / "bonds.csv"
    _write_links(links_path, [{
        "security_id": "sec-1", "cusip": "111111AA1",
        "start_date": "2020-01-01", "end_date": "", "source": "point-in-time master",
    }])
    _write_bonds(bonds_path, [{
        "date": "2022-12-30", "cusip": "111111AA1", "price_pct_par": "55",
        "yield_pct": "20", "benchmark_yield_pct": "4", "source": "TRACE normalized",
    }])
    joint = run_joint_credit_replay(
        _equity_run(),
        CsvBondMarketProvider(bonds_path),
        CsvCreditIdentityIndex(links_path),
    )
    assert joint.candidates_with_credit_links == 1
    assert joint.candidates_with_fresh_credit == 1
    assert joint.candidates_with_credit_stress == 1
    item = joint.candidates[0]
    assert item.min_fresh_price_pct_par == 55.0
    assert item.max_fresh_yield_pct == 20.0
    assert item.max_fresh_spread_bps == 1600.0
    assert item.price_stress_instrument_count == 1
    assert item.yield_stress_instrument_count == 1
    assert item.spread_stress_instrument_count == 1
    assert item.any_credit_stress is True


def test_stale_credit_is_preserved_but_excluded_from_current_stress_gate(tmp_path):
    links_path = tmp_path / "links.csv"
    bonds_path = tmp_path / "bonds.csv"
    _write_links(links_path, [{
        "security_id": "sec-1", "cusip": "111111AA1",
        "start_date": "2020-01-01", "end_date": "", "source": "master",
    }])
    _write_bonds(bonds_path, [{
        "date": "2022-10-01", "cusip": "111111AA1", "price_pct_par": "40",
        "yield_pct": "30", "benchmark_yield_pct": "4", "source": "old trade",
    }])
    item = enrich_equity_seed_with_credit(
        _seed(),
        CsvBondMarketProvider(bonds_path),
        CsvCreditIdentityIndex(links_path),
        config=CreditReplayConfig(max_staleness_days=30),
    )
    assert item.observed_credit_instrument_count == 1
    assert item.fresh_credit_instrument_count == 0
    assert item.stale_credit_instrument_count == 1
    assert item.any_credit_stress is False
    assert item.min_fresh_price_pct_par is None
    assert any("excluded from current-stress gates" in warning for warning in item.credit_evidence[0].warnings)


def test_future_credit_link_is_not_backfilled_to_earlier_equity_cutoff(tmp_path):
    links_path = tmp_path / "links.csv"
    bonds_path = tmp_path / "bonds.csv"
    _write_links(links_path, [{
        "security_id": "sec-1", "cusip": "111111AA1",
        "start_date": "2023-01-01", "end_date": "", "source": "future master",
    }])
    _write_bonds(bonds_path, [{
        "date": "2022-12-30", "cusip": "111111AA1", "price_pct_par": "50",
        "yield_pct": "20", "source": "bond",
    }])
    item = enrich_equity_seed_with_credit(
        _seed(), CsvBondMarketProvider(bonds_path), CsvCreditIdentityIndex(links_path)
    )
    assert item.linked_credit_instrument_count == 0
    assert item.credit_evidence == ()
    assert item.any_credit_stress is False
    assert any("no point-in-time debt identifier link" in warning for warning in item.warnings)


def test_credit_link_csv_requires_point_in_time_membership_columns(tmp_path):
    path = tmp_path / "links.csv"
    path.write_text("security_id,cusip\nsec-1,111111AA1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="point-in-time membership columns"):
        CsvCreditIdentityIndex(path)


def test_duplicate_distinct_same_day_bond_observations_are_rejected(tmp_path):
    links_path = tmp_path / "links.csv"
    bonds_path = tmp_path / "bonds.csv"
    _write_links(links_path, [{
        "security_id": "sec-1", "cusip": "111111AA1", "start_date": "2020-01-01", "end_date": "",
    }])
    _write_bonds(bonds_path, [
        {"date": "2022-12-30", "cusip": "111111AA1", "price_pct_par": "50", "yield_pct": "20"},
        {"date": "2022-12-30", "cusip": "111111AA1", "price_pct_par": "51", "yield_pct": "19"},
    ])
    with pytest.raises(ValueError, match="aggregate transaction-level bond data upstream"):
        enrich_equity_seed_with_credit(
            _seed(), CsvBondMarketProvider(bonds_path), CsvCreditIdentityIndex(links_path)
        )


def test_non_bulk_credit_provider_is_refused_for_cohort_replay(tmp_path):
    links_path = tmp_path / "links.csv"
    _write_links(links_path, [{
        "security_id": "sec-1", "cusip": "111111AA1", "start_date": "2020-01-01", "end_date": "",
    }])

    class SlowProvider:
        name = "slow"
        bulk_safe = False

        def observations(self, *, cusip, isin, start, end):
            return ()

        def benchmark_on_or_before(self, *, as_of, remaining_years):
            return None

    with pytest.raises(ValueError, match="not bulk-safe"):
        run_joint_credit_replay(_equity_run(), SlowProvider(), CsvCreditIdentityIndex(links_path))

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from distressed_equity.crsp_normalize import (
    CrspColumnConfig,
    CrspNormalizeConfig,
    normalize_crsp_exports,
)
from distressed_equity.crsp_normalize_cli import main as crsp_main
from distressed_equity.csv_market import CsvMarketProvider
from distressed_equity.replay import DistressScanConfig, scan_security


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_legacy_crsp_normalization_uses_permno_intervals_total_returns_and_dlret(tmp_path: Path):
    stocknames = tmp_path / "stocknames.csv"
    market = tmp_path / "market.csv"
    securities = tmp_path / "securities.csv"
    prices = tmp_path / "prices.csv"
    stocknames.write_text(
        "PERMNO,NAMEDT,NAMEENDT,TICKER,COMNAM,SHRCD,EXCHCD\n"
        "10001,2018-01-01,2018-01-03,AAA,Alpha Corp,10,1\n"
        "10002,2018-01-01,2018-12-31,BBB,Beta Fund,12,3\n",
        encoding="utf-8",
    )
    market.write_text(
        "PERMNO,date,PRC,RET,DLRET\n"
        "10001,2018-01-01,-10,,,\n"
        "10001,2018-01-02,11,0.10,\n"
        "10001,2018-01-03,5.5,-0.50,-0.20\n"
        "10001,2018-01-04,5,0.01,\n"
        "10002,2018-01-02,20,0.01,\n",
        encoding="utf-8",
    )

    result = normalize_crsp_exports(
        stocknames,
        market,
        securities,
        prices,
        config=CrspNormalizeConfig(return_semantics="legacy"),
    )
    assert result.stocknames_input_rows == 2
    assert result.security_interval_count == 1
    assert result.filtered_stocknames_rows == 1
    assert result.market_input_rows == 5
    assert result.output_price_rows == 3
    assert result.market_rows_outside_security_intervals == 2
    assert result.negative_price_rows == 1
    assert result.delisting_return_rows_applied == 1

    security_rows = _rows(securities)
    assert security_rows == [
        {
            "security_id": "CRSP_PERMNO:10001",
            "symbol": "AAA",
            "name": "Alpha Corp",
            "exchange": "NYSE",
            "asset_type": "stock",
            "start_date": "2018-01-01",
            "end_date": "2018-01-03",
            "status": "SHRCD=10;EXCHCD=1",
            "crsp_permno": "10001",
            "crsp_share_code": "10",
            "crsp_exchange_code": "1",
        }
    ]
    price_rows = _rows(prices)
    assert [row["close"] for row in price_rows] == ["10.0", "11.0", "5.5"]
    assert price_rows[0]["crsp_prc_was_negative"] == "true"
    assert float(price_rows[0]["adjusted_close"]) == 10.0
    assert float(price_rows[1]["adjusted_close"]) == pytest.approx(11.0)
    # (1 - 0.50) * (1 - 0.20) - 1 = -0.60; 11 * 0.40 = 4.4.
    assert float(price_rows[2]["adjusted_close"]) == pytest.approx(4.4)

    provider = CsvMarketProvider(securities, prices)
    universe = provider.universe(date(2018, 1, 3))
    assert len(universe) == 1
    assert universe[0].security_id == "CRSP_PERMNO:10001"
    history = provider.adjusted_history(universe[0], date(2018, 1, 1), date(2018, 1, 3))
    assert all(point.adjusted for point in history)


def test_missing_internal_return_emits_barrier_and_prevents_false_continuity(tmp_path: Path):
    stocknames = tmp_path / "stocknames.csv"
    market = tmp_path / "market.csv"
    securities = tmp_path / "securities.csv"
    prices = tmp_path / "prices.csv"
    stocknames.write_text(
        "PERMNO,NAMEDT,NAMEENDT,TICKER,COMNAM,SHRCD\n"
        "10001,2020-01-01,,AAA,Alpha Corp,10\n",
        encoding="utf-8",
    )
    market.write_text(
        "PERMNO,date,PRC,RET\n"
        "10001,2020-01-01,10,\n"
        "10001,2020-01-02,9,\n"
        "10001,2020-01-03,9.9,0.10\n",
        encoding="utf-8",
    )

    result = normalize_crsp_exports(
        stocknames,
        market,
        securities,
        prices,
        config=CrspNormalizeConfig(return_semantics="already_total"),
    )
    rows = _rows(prices)
    assert rows[0]["adjusted_close"] == "10.0"
    assert rows[1]["adjusted_close"] == ""
    assert float(rows[2]["adjusted_close"]) == pytest.approx(9.9)
    assert result.unadjusted_barrier_rows == 1
    assert result.return_chain_break_count == 1

    provider = CsvMarketProvider(securities, prices)
    security = provider.universe(date(2020, 1, 3))[0]
    history = provider.adjusted_history(security, date(2020, 1, 1), date(2020, 1, 3))
    assert [point.adjusted for point in history] == [True, False, True]
    seed, reason = scan_security(
        provider,
        security,
        date(2020, 1, 3),
        DistressScanConfig(min_adjusted_drawdown=0.0, lookback_years=1),
    )
    assert seed is None
    assert reason == "history is not split/dividend adjusted"


def test_ciz_style_column_aliases_work_only_with_explicit_return_semantics(tmp_path: Path):
    stocknames = tmp_path / "names.csv"
    market = tmp_path / "market.csv"
    securities = tmp_path / "securities.csv"
    prices = tmp_path / "prices.csv"
    stocknames.write_text(
        "Permno,NameDt,NameEndDt,Ticker,SecurityNm,ShrCd,ExchCd\n"
        "10001,2022-01-01,,AAA,Alpha Corp,11,3\n",
        encoding="utf-8",
    )
    market.write_text(
        "Permno,DlyCalDt,DlyPrc,DlyRet\n"
        "10001,2022-01-03,10,\n"
        "10001,2022-01-04,11,0.10\n",
        encoding="utf-8",
    )
    result = normalize_crsp_exports(
        stocknames,
        market,
        securities,
        prices,
        config=CrspNormalizeConfig(return_semantics="already_total"),
    )
    assert result.resolved_columns["market_date"] == "DlyCalDt"
    assert result.resolved_columns["market_price"] == "DlyPrc"
    assert result.resolved_columns["market_return"] == "DlyRet"
    assert _rows(securities)[0]["exchange"] == "NASDAQ"
    assert float(_rows(prices)[1]["adjusted_close"]) == pytest.approx(11.0)


def test_market_export_must_be_sorted_by_permno_and_date(tmp_path: Path):
    stocknames = tmp_path / "names.csv"
    market = tmp_path / "market.csv"
    stocknames.write_text(
        "PERMNO,NAMEDT,NAMEENDT,TICKER,COMNAM,SHRCD\n"
        "10001,2020-01-01,,AAA,Alpha,10\n",
        encoding="utf-8",
    )
    market.write_text(
        "PERMNO,date,PRC,RET\n"
        "10001,2020-01-03,10,\n"
        "10001,2020-01-02,9,-0.10\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="strictly sorted by PERMNO,date"):
        normalize_crsp_exports(
            stocknames,
            market,
            tmp_path / "securities.csv",
            tmp_path / "prices.csv",
            config=CrspNormalizeConfig(return_semantics="already_total"),
        )


def test_overlapping_name_intervals_are_rejected(tmp_path: Path):
    stocknames = tmp_path / "names.csv"
    market = tmp_path / "market.csv"
    stocknames.write_text(
        "PERMNO,NAMEDT,NAMEENDT,TICKER,COMNAM,SHRCD\n"
        "10001,2020-01-01,2020-06-30,AAA,Alpha,10\n"
        "10001,2020-06-30,2020-12-31,AAB,Alpha New,10\n",
        encoding="utf-8",
    )
    market.write_text("PERMNO,date,PRC,RET\n", encoding="utf-8")
    with pytest.raises(ValueError, match="stock-name intervals overlap"):
        normalize_crsp_exports(
            stocknames,
            market,
            tmp_path / "securities.csv",
            tmp_path / "prices.csv",
            config=CrspNormalizeConfig(return_semantics="already_total"),
        )


def test_shares_outstanding_requires_explicit_column_and_scale(tmp_path: Path):
    stocknames = tmp_path / "names.csv"
    market = tmp_path / "market.csv"
    stocknames.write_text(
        "PERMNO,NAMEDT,NAMEENDT,TICKER,COMNAM,SHRCD\n"
        "10001,2020-01-01,,AAA,Alpha,10\n",
        encoding="utf-8",
    )
    market.write_text(
        "PERMNO,date,PRC,RET,SHROUT\n"
        "10001,2020-01-01,10,,1234\n",
        encoding="utf-8",
    )
    config = CrspNormalizeConfig(
        return_semantics="already_total",
        shares_outstanding_scale=1000.0,
        columns=CrspColumnConfig(market_shares_outstanding="SHROUT"),
    )
    normalize_crsp_exports(
        stocknames,
        market,
        tmp_path / "securities.csv",
        tmp_path / "prices.csv",
        config=config,
    )
    row = _rows(tmp_path / "prices.csv")[0]
    assert float(row["shares_outstanding"]) == 1_234_000.0


def test_cli_writes_reproducible_metadata_and_normalized_files(tmp_path: Path):
    stocknames = tmp_path / "names.csv"
    market = tmp_path / "market.csv"
    securities = tmp_path / "securities.csv"
    prices = tmp_path / "prices.csv"
    metadata = tmp_path / "metadata.json"
    stocknames.write_text(
        "PERMNO,NAMEDT,NAMEENDT,TICKER,COMNAM,SHRCD\n"
        "10001,2020-01-01,,AAA,Alpha,10\n",
        encoding="utf-8",
    )
    market.write_text(
        "PERMNO,date,PRC,RET\n"
        "10001,2020-01-01,10,\n"
        "10001,2020-01-02,11,0.10\n",
        encoding="utf-8",
    )
    assert crsp_main([
        "--stocknames-csv", str(stocknames),
        "--market-csv", str(market),
        "--securities-output", str(securities),
        "--prices-output", str(prices),
        "--metadata-output", str(metadata),
        "--return-semantics", "already_total",
    ]) == 0
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    assert payload["config"]["return_semantics"] == "already_total"
    assert payload["security_interval_count"] == 1
    assert payload["output_price_rows"] == 2
    assert payload["resolved_columns"]["stocknames_permno"] == "PERMNO"
    assert payload["resolved_columns"]["market_return"] == "RET"
    assert securities.exists() and prices.exists()

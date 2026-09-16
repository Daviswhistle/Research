from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from distressed_equity.csv_bond_market import CsvBondMarketProvider
from distressed_equity.trace_normalize_cli import main as normalize_main
from distressed_equity.trace_transactions import (
    TraceAggregationConfig,
    normalize_trace_transactions,
    read_trace_enhanced_files,
)


FIELDS = [
    "Record Count Number",
    "Reference Number",
    "Trade Status",
    "TRACE Symbol",
    "CUSIP",
    "Sub-Product",
    "When Issued Indicator",
    "Commission Indicator",
    "Quantity",
    "Price",
    "Yield Direction",
    "Yield",
    "As Of Indicator",
    "Execution Date",
    "Execution Time",
    "Trade Report Date",
    "Trade Report Time",
    "Settlement Date",
    "Trade Modifier 3",
    "Trade Modifier 4",
    "Buy/Sell Indicator",
    "Buyer Commission",
    "Buyer Capacity",
    "Seller Commission",
    "Seller Capacity",
    "Contra Party Indicator",
    "Locked In Indicator",
    "Special Price Indicator",
    "Trading Market Indicator",
    "Dissemination Flag",
    "Prior Trade Report Date",
    "Prior Reference Number",
]


def tx(
    ref: str,
    *,
    status: str = "T",
    cusip: str = "111111AA1",
    quantity: str = "100",
    price: str = "100",
    yield_pct: str = "5",
    yield_direction: str = "",
    as_of: str = "",
    execution_date: str = "20230102",
    execution_time: str = "100000",
    report_date: str = "20230102",
    report_time: str = "100100",
    modifier4: str = "",
    side: str = "S",
    contra: str = "C",
    special: str = "",
    market: str = "S1",
    dissemination: str = "Y",
    prior_date: str = "",
    prior_ref: str = "",
):
    return {
        "Record Count Number": ref,
        "Reference Number": ref,
        "Trade Status": status,
        "TRACE Symbol": "TEST1",
        "CUSIP": cusip,
        "Sub-Product": "CORP",
        "When Issued Indicator": "N",
        "Commission Indicator": "N",
        "Quantity": quantity,
        "Price": price,
        "Yield Direction": yield_direction,
        "Yield": yield_pct,
        "As Of Indicator": as_of,
        "Execution Date": execution_date,
        "Execution Time": execution_time,
        "Trade Report Date": report_date,
        "Trade Report Time": report_time,
        "Settlement Date": "20230104",
        "Trade Modifier 3": "",
        "Trade Modifier 4": modifier4,
        "Buy/Sell Indicator": side,
        "Buyer Commission": "",
        "Buyer Capacity": "P",
        "Seller Commission": "",
        "Seller Capacity": "P",
        "Contra Party Indicator": contra,
        "Locked In Indicator": "",
        "Special Price Indicator": special,
        "Trading Market Indicator": market,
        "Dissemination Flag": dissemination,
        "Prior Trade Report Date": prior_date,
        "Prior Reference Number": prior_ref,
    }


def write_trace(path: Path, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("|".join(FIELDS) + "\n")
        for row in rows:
            handle.write("|".join(str(row.get(field, "")) for field in FIELDS) + "\n")
        # Official files contain a single-field numeric end-of-file trailer.
        handle.write("202301022359590000000123\n")


def test_default_trace_vwap_uses_disseminated_side_and_excludes_special_reports(tmp_path):
    path = tmp_path / "enhanced-time-and-sales-cusip-2023-01-02.txt"
    write_trace(
        path,
        [
            # Two reporting sides of one inter-dealer trade: only the S side is disseminated.
            tx("0000001", price="90", yield_pct="4", side="B", contra="D", dissemination="N", execution_time="090000"),
            tx("0000002", price="90", yield_pct="4", side="S", contra="D", dissemination="Y", execution_time="090000"),
            tx("0000003", quantity="300", price="110", yield_pct="6", execution_time="150000"),
            # Already-weighted and special-price reports are excluded by default.
            tx("0000004", quantity="1000", price="70", yield_pct="7", modifier4="W", execution_time="160000"),
            tx("0000005", quantity="1000", price="60", yield_pct="8", special="Y", execution_time="170000"),
        ],
    )
    records = read_trace_enhanced_files([path])
    result = normalize_trace_transactions(records)
    assert result.parsed_record_count == 5
    assert result.active_record_count == 5
    assert result.included_record_count == 2
    assert len(result.observations) == 1
    daily = result.observations[0]
    assert daily.trade_count == 2
    assert daily.observation.price_pct_par == pytest.approx(105.0)
    assert daily.observation.yield_pct == pytest.approx(5.5)
    assert daily.observation.volume == pytest.approx(400.0)
    assert daily.latest_execution_time == "150000"


def test_last_execution_policy_uses_last_price_but_preserves_daily_volume(tmp_path):
    path = tmp_path / "trace.txt"
    write_trace(
        path,
        [
            tx("0000001", quantity="100", price="90", yield_pct="4", execution_time="090000"),
            tx("0000002", quantity="300", price="110", yield_pct="6", execution_time="150000"),
        ],
    )
    result = normalize_trace_transactions(
        read_trace_enhanced_files([path]),
        config=TraceAggregationConfig(aggregation="last"),
    )
    daily = result.observations[0]
    assert daily.observation.price_pct_par == pytest.approx(110.0)
    assert daily.observation.yield_pct == pytest.approx(6.0)
    assert daily.observation.volume == pytest.approx(400.0)
    assert daily.latest_execution_time == "150000"


def test_cancelled_correction_removes_original_and_keeps_new_correction(tmp_path):
    path = tmp_path / "trace.txt"
    write_trace(
        path,
        [
            tx("0000001", price="90", report_date="20230102", report_time="100000"),
            tx(
                "0000002",
                status="C",
                price="90",
                report_date="20230103",
                report_time="090000",
                prior_date="20230102",
                prior_ref="0000001",
                dissemination="N",
            ),
            tx(
                "0000003",
                status="R",
                price="95",
                report_date="20230103",
                report_time="090100",
            ),
        ],
    )
    result = normalize_trace_transactions(read_trace_enhanced_files([path]))
    assert result.cancelled_record_count == 1
    assert result.active_record_count == 1
    assert len(result.observations) == 1
    assert result.observations[0].observation.price_pct_par == pytest.approx(95.0)


def test_reversal_in_later_file_removes_prior_report(tmp_path):
    first = tmp_path / "day1.txt"
    second = tmp_path / "day2.txt"
    write_trace(first, [tx("0000001", price="91", report_date="20230102")])
    write_trace(
        second,
        [
            tx(
                "0000002",
                status="Y",
                as_of="R",
                price="91",
                execution_date="20230102",
                execution_time="100000",
                report_date="20230215",
                report_time="120000",
                prior_date="20230102",
                prior_ref="0000001",
                dissemination="N",
            )
        ],
    )
    result = normalize_trace_transactions(read_trace_enhanced_files([first, second]))
    assert result.cancelled_record_count == 1
    assert result.active_record_count == 0
    assert result.observations == ()


def test_unresolved_reversal_is_error_by_default_and_explicit_warn_when_requested(tmp_path):
    path = tmp_path / "trace.txt"
    write_trace(
        path,
        [
            tx(
                "0000002",
                status="Y",
                as_of="R",
                report_date="20230215",
                prior_date="20221201",
                prior_ref="9999999",
                dissemination="N",
            )
        ],
    )
    records = read_trace_enhanced_files([path])
    with pytest.raises(ValueError, match="unresolved cancel/correction/reversal"):
        normalize_trace_transactions(records)

    result = normalize_trace_transactions(
        records,
        config=TraceAggregationConfig(unresolved_correction_policy="warn"),
    )
    assert result.unresolved_correction_count == 1
    assert result.observations == ()
    assert any("9999999" in warning for warning in result.warnings)


def test_negative_yield_direction_is_preserved(tmp_path):
    path = tmp_path / "trace.txt"
    write_trace(path, [tx("0000001", yield_pct="0.25", yield_direction="-")])
    result = normalize_trace_transactions(read_trace_enhanced_files([path]))
    assert result.observations[0].observation.yield_pct == pytest.approx(-0.25)


def test_non_cusip_historical_file_is_refused(tmp_path):
    path = tmp_path / "trace.txt"
    row = tx("0000001")
    row["CUSIP"] = ""
    write_trace(path, [row])
    with pytest.raises(ValueError, match="CUSIP is blank"):
        read_trace_enhanced_files([path])


def test_trace_cli_output_is_directly_consumable_by_csv_bond_provider(tmp_path):
    raw = tmp_path / "trace.txt"
    output = tmp_path / "daily.csv"
    metadata = tmp_path / "metadata.json"
    write_trace(
        raw,
        [
            tx("0000001", quantity="100", price="90", execution_time="090000"),
            tx("0000002", quantity="100", price="110", execution_time="150000"),
        ],
    )
    assert normalize_main(
        [
            str(raw),
            "--output",
            str(output),
            "--metadata-output",
            str(metadata),
            "--aggregation",
            "vwap",
        ]
    ) == 0
    provider = CsvBondMarketProvider(output)
    rows = provider.observations(
        cusip="111111AA1",
        isin=None,
        start=date(2023, 1, 1),
        end=date(2023, 1, 3),
    )
    assert len(rows) == 1
    assert rows[0].price_pct_par == pytest.approx(100.0)
    assert rows[0].volume == pytest.approx(200.0)
    assert metadata.exists()

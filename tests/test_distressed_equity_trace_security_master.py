from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pytest

from distressed_equity.trace_normalize_cli import main as normalize_main
from distressed_equity.trace_security_master import (
    build_trace_security_master_resolver,
    read_trace_daily_list_events,
    read_trace_security_master_snapshot,
)
from distressed_equity.trace_symbol_transactions import read_trace_enhanced_files_with_security_master


TRACE_FIELDS = [
    "Reference Number",
    "Trade Status",
    "TRACE Symbol",
    "CUSIP",
    "Quantity",
    "Price",
    "Yield Direction",
    "Yield",
    "As Of Indicator",
    "Execution Date",
    "Execution Time",
    "Trade Report Date",
    "Trade Report Time",
    "Trade Modifier 3",
    "Trade Modifier 4",
    "Buy/Sell Indicator",
    "Contra Party Indicator",
    "Special Price Indicator",
    "Trading Market Indicator",
    "Dissemination Flag",
    "Prior Trade Report Date",
    "Prior Reference Number",
]


def _write_trace(path: Path, *, symbol="TEST1", cusip="", execution_date="20230102"):
    row = {
        "Reference Number": "1",
        "Trade Status": "T",
        "TRACE Symbol": symbol,
        "CUSIP": cusip,
        "Quantity": "100",
        "Price": "90",
        "Yield Direction": "",
        "Yield": "8",
        "As Of Indicator": "",
        "Execution Date": execution_date,
        "Execution Time": "100000",
        "Trade Report Date": execution_date,
        "Trade Report Time": "100100",
        "Trade Modifier 3": "",
        "Trade Modifier 4": "",
        "Buy/Sell Indicator": "S",
        "Contra Party Indicator": "C",
        "Special Price Indicator": "",
        "Trading Market Indicator": "S1",
        "Dissemination Flag": "Y",
        "Prior Trade Report Date": "",
        "Prior Reference Number": "",
    }
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("|".join(TRACE_FIELDS) + "\n")
        handle.write("|".join(row[field] for field in TRACE_FIELDS) + "\n")
        handle.write("202301022359590000000123\n")


def _write_daily_list(path: Path, rows):
    fields = [
        "Category", "Effective Date", "Symbol", "CUSIP",
        "Old Symbol", "New Symbol", "Old CUSIP", "New CUSIP",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(",".join(fields) + "\n")
        for row in rows:
            handle.write(",".join(str(row.get(field, "")) for field in fields) + "\n")


def test_daily_list_builds_effective_symbol_intervals_without_backfill(tmp_path):
    daily = tmp_path / "daily.csv"
    _write_daily_list(daily, [
        {"Category": "SA-Security Addition", "Effective Date": "2023-01-01", "Symbol": "TEST1", "CUSIP": "111111AA1"},
        {
            "Category": "SC-Security Change", "Effective Date": "2023-02-01",
            "Old Symbol": "TEST1", "New Symbol": "TEST2",
            "Old CUSIP": "111111AA1", "New CUSIP": "222222BB2",
        },
    ])
    resolver = build_trace_security_master_resolver(read_trace_daily_list_events([daily]))
    assert resolver.resolve("TEST1", date(2022, 12, 31)) is None
    assert resolver.resolve("TEST1", date(2023, 1, 31)).cusip == "111111AA1"
    assert resolver.resolve("TEST1", date(2023, 2, 1)) is None
    assert resolver.resolve("TEST2", date(2023, 2, 1)).cusip == "222222BB2"


def test_security_master_snapshot_is_never_backfilled_before_as_of_date(tmp_path):
    master = tmp_path / "master.csv"
    master.write_text("Symbol,CUSIP\nTEST1,111111AA1\n", encoding="utf-8")
    events = read_trace_security_master_snapshot([master], as_of=date(2023, 3, 1))
    resolver = build_trace_security_master_resolver(events)
    assert resolver.resolve("TEST1", date(2023, 2, 28)) is None
    assert resolver.resolve("TEST1", date(2023, 3, 1)).cusip == "111111AA1"


def test_symbol_only_trace_row_resolves_from_execution_date_mapping(tmp_path):
    daily = tmp_path / "daily.csv"
    raw = tmp_path / "trace.txt"
    _write_daily_list(daily, [
        {"Category": "SA-Security Addition", "Effective Date": "2023-01-01", "Symbol": "TEST1", "CUSIP": "111111AA1"},
    ])
    _write_trace(raw, cusip="", execution_date="20230102")
    resolver = build_trace_security_master_resolver(read_trace_daily_list_events([daily]))
    result = read_trace_enhanced_files_with_security_master([raw], resolver)
    assert result.symbol_resolved_record_count == 1
    assert result.raw_cusip_record_count == 0
    assert result.transactions[0].cusip == "111111AA1"


def test_symbol_only_trace_before_mapping_is_rejected_not_guessed(tmp_path):
    daily = tmp_path / "daily.csv"
    raw = tmp_path / "trace.txt"
    _write_daily_list(daily, [
        {"Category": "SA-Security Addition", "Effective Date": "2023-01-03", "Symbol": "TEST1", "CUSIP": "111111AA1"},
    ])
    _write_trace(raw, cusip="", execution_date="20230102")
    resolver = build_trace_security_master_resolver(read_trace_daily_list_events([daily]))
    with pytest.raises(ValueError, match="no source-backed point-in-time mapping"):
        read_trace_enhanced_files_with_security_master([raw], resolver)


def test_raw_cusip_conflicting_with_point_in_time_symbol_mapping_is_rejected(tmp_path):
    daily = tmp_path / "daily.csv"
    raw = tmp_path / "trace.txt"
    _write_daily_list(daily, [
        {"Category": "SA-Security Addition", "Effective Date": "2023-01-01", "Symbol": "TEST1", "CUSIP": "111111AA1"},
    ])
    _write_trace(raw, cusip="999999ZZ9", execution_date="20230102")
    resolver = build_trace_security_master_resolver(read_trace_daily_list_events([daily]))
    with pytest.raises(ValueError, match="conflicts with point-in-time TRACE Symbol"):
        read_trace_enhanced_files_with_security_master([raw], resolver)


def test_conflicting_same_day_symbol_starts_are_rejected(tmp_path):
    daily = tmp_path / "daily.csv"
    _write_daily_list(daily, [
        {"Category": "SA-Security Addition", "Effective Date": "2023-01-01", "Symbol": "TEST1", "CUSIP": "111111AA1"},
        {"Category": "SA-Security Addition", "Effective Date": "2023-01-01", "Symbol": "TEST1", "CUSIP": "222222BB2"},
    ])
    with pytest.raises(ValueError, match="conflicting TRACE symbol/CUSIP starts"):
        build_trace_security_master_resolver(read_trace_daily_list_events([daily]))


def test_cli_symbol_only_normalization_records_identity_audit_metadata(tmp_path):
    daily = tmp_path / "daily.csv"
    raw = tmp_path / "trace.txt"
    output = tmp_path / "daily_bond.csv"
    metadata = tmp_path / "meta.json"
    _write_daily_list(daily, [
        {"Category": "SA-Security Addition", "Effective Date": "2023-01-01", "Symbol": "TEST1", "CUSIP": "111111AA1"},
    ])
    _write_trace(raw, cusip="", execution_date="20230102")
    assert normalize_main([
        str(raw),
        "--trace-daily-list-csv", str(daily),
        "--output", str(output),
        "--metadata-output", str(metadata),
    ]) == 0
    assert "111111AA1" in output.read_text(encoding="utf-8")
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    assert payload["identity"]["mode"] == "point_in_time_symbol_cusip"
    assert payload["identity"]["symbol_resolved_record_count"] == 1
    assert payload["identity"]["resolver_interval_count"] == 1

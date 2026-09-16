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


DAILY_LIST_FIELDS = [
    "Category",
    "DL Date",
    "Update Date",
    "Trade Report Effective Date",
    "Effective Date",
    "Old Trade Report Effective Date",
    "New Trade Report Effective Date",
    "Symbol",
    "CUSIP",
    "Old Symbol",
    "New Symbol",
    "Old CUSIP",
    "New CUSIP",
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
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(",".join(DAILY_LIST_FIELDS) + "\n")
        for row in rows:
            handle.write(",".join(str(row.get(field, "")) for field in DAILY_LIST_FIELDS) + "\n")


def _addition(effective_date: str, symbol="TEST1", cusip="111111AA1"):
    return {
        "Category": "SA-Security Addition",
        "DL Date": effective_date,
        "Trade Report Effective Date": effective_date,
        "Symbol": symbol,
        "CUSIP": cusip,
    }


def test_daily_list_builds_effective_symbol_intervals_without_backfill(tmp_path):
    daily = tmp_path / "daily.csv"
    _write_daily_list(daily, [
        _addition("2023-01-01"),
        {
            "Category": "SC-Security Change",
            "DL Date": "2023-02-01",
            "Update Date": "2023-02-01",
            # This is a reporting-eligibility property, not the identity-change boundary.
            "Old Trade Report Effective Date": "2022-01-01",
            "New Trade Report Effective Date": "2022-01-01",
            "Old Symbol": "TEST1",
            "New Symbol": "TEST2",
            "Old CUSIP": "111111AA1",
            "New CUSIP": "222222BB2",
        },
    ])
    events = read_trace_daily_list_events([daily])
    change = next(event for event in events if event.kind == "change")
    assert change.effective_date == date(2023, 2, 1)
    assert change.date_basis == "update_date"

    resolver = build_trace_security_master_resolver(events)
    assert resolver.resolve("TEST1", date(2022, 12, 31)) is None
    assert resolver.resolve("TEST1", date(2023, 1, 31)).cusip == "111111AA1"
    assert resolver.resolve("TEST1", date(2023, 2, 1)) is None
    assert resolver.resolve("TEST2", date(2023, 2, 1)).cusip == "222222BB2"


def test_change_uses_dl_date_only_as_availability_fallback_not_new_trade_report_effective_date(tmp_path):
    daily = tmp_path / "daily.csv"
    _write_daily_list(daily, [
        _addition("2023-01-01"),
        {
            "Category": "SC-Security Change",
            "DL Date": "2023-02-10",
            "Update Date": "",
            "New Trade Report Effective Date": "2022-06-01",
            "Old Symbol": "TEST1",
            "New Symbol": "TEST2",
            "Old CUSIP": "111111AA1",
            "New CUSIP": "222222BB2",
        },
    ])
    events = read_trace_daily_list_events([daily])
    change = next(event for event in events if event.kind == "change")
    assert change.effective_date == date(2023, 2, 10)
    assert change.date_basis == "daily_list_date_availability_fallback"
    resolver = build_trace_security_master_resolver(events)
    assert resolver.resolve("TEST1", date(2023, 2, 9)).cusip == "111111AA1"
    assert resolver.resolve("TEST2", date(2023, 2, 9)) is None
    assert resolver.resolve("TEST2", date(2023, 2, 10)).cusip == "222222BB2"


def test_deletion_uses_deletion_effective_date_not_daily_list_date(tmp_path):
    daily = tmp_path / "daily.csv"
    _write_daily_list(daily, [
        _addition("2023-01-01"),
        {
            "Category": "SD-Security Deletion",
            "DL Date": "2023-02-05",
            "Effective Date": "2023-02-03",
            "Symbol": "TEST1",
            "CUSIP": "111111AA1",
        },
    ])
    events = read_trace_daily_list_events([daily])
    deletion = next(event for event in events if event.kind == "delete")
    assert deletion.effective_date == date(2023, 2, 3)
    assert deletion.date_basis == "deletion_effective_date"
    resolver = build_trace_security_master_resolver(events)
    assert resolver.resolve("TEST1", date(2023, 2, 2)).cusip == "111111AA1"
    assert resolver.resolve("TEST1", date(2023, 2, 3)) is None


def test_security_master_snapshot_is_never_backfilled_before_as_of_date(tmp_path):
    master = tmp_path / "master.csv"
    master.write_text("Symbol,CUSIP\nTEST1,111111AA1\n", encoding="utf-8")
    events = read_trace_security_master_snapshot([master], as_of=date(2023, 3, 1))
    resolver = build_trace_security_master_resolver(events)
    assert resolver.resolve("TEST1", date(2023, 2, 28)) is None
    assert resolver.resolve("TEST1", date(2023, 3, 1)).cusip == "111111AA1"


def test_same_day_change_chain_does_not_leave_intermediate_symbol_active(tmp_path):
    daily = tmp_path / "daily.csv"
    _write_daily_list(daily, [
        _addition("2023-01-01", symbol="A", cusip="111111AA1"),
        {
            "Category": "SC-Security Change",
            "DL Date": "2023-02-01",
            "Update Date": "2023-02-01",
            "Old Symbol": "A",
            "New Symbol": "B",
            "Old CUSIP": "111111AA1",
            "New CUSIP": "222222BB2",
        },
        {
            "Category": "SC-Security Change",
            "DL Date": "2023-02-01",
            "Update Date": "2023-02-01",
            "Old Symbol": "B",
            "New Symbol": "C",
            "Old CUSIP": "222222BB2",
            "New CUSIP": "333333CC3",
        },
    ])
    resolver = build_trace_security_master_resolver(read_trace_daily_list_events([daily]))
    assert resolver.resolve("A", date(2023, 1, 31)).cusip == "111111AA1"
    assert resolver.resolve("A", date(2023, 2, 1)) is None
    assert resolver.resolve("B", date(2023, 2, 1)) is None
    assert resolver.resolve("B", date(2023, 2, 2)) is None
    assert resolver.resolve("C", date(2023, 2, 1)).cusip == "333333CC3"


def test_later_snapshot_provenance_is_not_attached_to_earlier_identity_interval(tmp_path):
    daily = tmp_path / "daily.csv"
    master = tmp_path / "master.csv"
    _write_daily_list(daily, [_addition("2023-01-01")])
    master.write_text("Symbol,CUSIP\nTEST1,111111AA1\n", encoding="utf-8")
    daily_events = read_trace_daily_list_events([daily])
    snapshot_events = read_trace_security_master_snapshot([master], as_of=date(2023, 3, 1))
    resolver = build_trace_security_master_resolver((*daily_events, *snapshot_events))

    february = resolver.resolve("TEST1", date(2023, 2, 15))
    march = resolver.resolve("TEST1", date(2023, 3, 1))
    assert february is not None and march is not None
    assert all("security_master_as_of" not in ref for ref in february.source_refs)
    assert any("security_master_as_of" in ref for ref in march.source_refs)
    assert set(february.source_refs).issubset(set(march.source_refs))
    assert resolver.interval_count == 2


def test_symbol_only_trace_row_resolves_from_execution_date_mapping(tmp_path):
    daily = tmp_path / "daily.csv"
    raw = tmp_path / "trace.txt"
    _write_daily_list(daily, [_addition("2023-01-01")])
    _write_trace(raw, cusip="", execution_date="20230102")
    resolver = build_trace_security_master_resolver(read_trace_daily_list_events([daily]))
    result = read_trace_enhanced_files_with_security_master([raw], resolver)
    assert result.symbol_resolved_record_count == 1
    assert result.raw_cusip_record_count == 0
    assert result.transactions[0].cusip == "111111AA1"


def test_symbol_only_trace_before_mapping_is_rejected_not_guessed(tmp_path):
    daily = tmp_path / "daily.csv"
    raw = tmp_path / "trace.txt"
    _write_daily_list(daily, [_addition("2023-01-03")])
    _write_trace(raw, cusip="", execution_date="20230102")
    resolver = build_trace_security_master_resolver(read_trace_daily_list_events([daily]))
    with pytest.raises(ValueError, match="no source-backed point-in-time mapping"):
        read_trace_enhanced_files_with_security_master([raw], resolver)


def test_raw_cusip_conflicting_with_point_in_time_symbol_mapping_is_rejected(tmp_path):
    daily = tmp_path / "daily.csv"
    raw = tmp_path / "trace.txt"
    _write_daily_list(daily, [_addition("2023-01-01")])
    _write_trace(raw, cusip="999999ZZ9", execution_date="20230102")
    resolver = build_trace_security_master_resolver(read_trace_daily_list_events([daily]))
    with pytest.raises(ValueError, match="conflicts with point-in-time TRACE Symbol"):
        read_trace_enhanced_files_with_security_master([raw], resolver)


def test_conflicting_same_day_symbol_starts_are_rejected(tmp_path):
    daily = tmp_path / "daily.csv"
    _write_daily_list(daily, [
        _addition("2023-01-01", cusip="111111AA1"),
        _addition("2023-01-01", cusip="222222BB2"),
    ])
    with pytest.raises(ValueError, match="conflicting TRACE symbol/CUSIP starts"):
        build_trace_security_master_resolver(read_trace_daily_list_events([daily]))


def test_cli_symbol_only_normalization_records_identity_audit_metadata(tmp_path):
    daily = tmp_path / "daily.csv"
    raw = tmp_path / "trace.txt"
    output = tmp_path / "daily_bond.csv"
    metadata = tmp_path / "meta.json"
    _write_daily_list(daily, [_addition("2023-01-01")])
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

from datetime import date
from decimal import Decimal

import pytest

from distressed_equity.trace_transactions import TraceTransaction, normalize_trace_transactions


def transaction(*, source_file, source_row, price="100"):
    return TraceTransaction(
        source_file=source_file,
        source_row=source_row,
        reference_number="0000001",
        trade_status="T",
        trace_symbol="TEST1",
        cusip="111111AA1",
        quantity=Decimal("100"),
        price=Decimal(price),
        yield_pct=Decimal("5"),
        as_of_indicator=None,
        execution_date=date(2023, 1, 2),
        execution_time="100000",
        trade_report_date=date(2023, 1, 2),
        trade_report_time="100100",
        trade_modifier_3=None,
        trade_modifier_4=None,
        buy_sell_indicator="S",
        contra_party_indicator="C",
        special_price_indicator=None,
        trading_market_indicator="S1",
        dissemination_flag="Y",
        prior_trade_report_date=None,
        prior_reference_number=None,
    )


def test_exact_report_repeated_in_overlapping_files_is_collapsed_once():
    result = normalize_trace_transactions(
        (
            transaction(source_file="daily.txt", source_row=2),
            transaction(source_file="monthly.txt", source_row=9002),
        )
    )
    assert result.parsed_record_count == 2
    assert result.exact_duplicate_record_count == 1
    assert result.active_record_count == 1
    assert result.included_record_count == 1
    assert result.observations[0].trade_count == 1
    assert result.observations[0].observation.volume == 100.0
    assert any("exact duplicate" in warning for warning in result.warnings)


def test_same_report_key_with_conflicting_economics_is_hard_error():
    with pytest.raises(ValueError, match="conflicting TRACE reports share key"):
        normalize_trace_transactions(
            (
                transaction(source_file="daily.txt", source_row=2, price="100"),
                transaction(source_file="monthly.txt", source_row=9002, price="101"),
            )
        )

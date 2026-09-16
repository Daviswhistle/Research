from datetime import date

from distressed_equity.bond_market import BondMarketObservation, build_bond_market_packet
from distressed_equity.debt_instruments import DebtInstrumentLedger, DebtInstrumentSnapshot, DebtInstrumentVersion


class RawTradeLikeProvider:
    name = "raw_trade_like"
    bulk_safe = True

    def observations(self, *, cusip, isin, start, end):
        return (
            BondMarketObservation(
                date=date(2022, 12, 30), cusip=cusip,
                price_pct_par=55.0, yield_pct=18.0, volume=100_000,
                benchmark_yield_pct=4.0, source="trade-1",
            ),
            BondMarketObservation(
                date=date(2022, 12, 30), cusip=cusip,
                price_pct_par=57.0, yield_pct=17.0, volume=250_000,
                benchmark_yield_pct=4.0, source="trade-2",
            ),
        )

    def benchmark_on_or_before(self, *, as_of, remaining_years):
        return None


def _ledger():
    snapshot = DebtInstrumentSnapshot(
        as_of_date=date(2022, 9, 30),
        source_accession="acc",
        name="Senior Notes",
        maturity_date=date(2028, 6, 15),
        maturity_year=2028,
        cusip="111111AA1",
    )
    return DebtInstrumentLedger(
        versions=(DebtInstrumentVersion("CUSIP:111111AA1", snapshot, "exact_identifier"),),
        changes=(), unmatched_versions=(), warnings=(),
    )


def test_transaction_level_same_day_rows_require_explicit_upstream_aggregation():
    try:
        build_bond_market_packet(RawTradeLikeProvider(), _ledger(), date(2022, 12, 31))
    except ValueError as exc:
        assert "multiple distinct bond observations" in str(exc)
        assert "explicit daily policy" in str(exc)
    else:
        raise AssertionError("expected same-day transaction rows to require aggregation")

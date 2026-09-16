from datetime import date

from distressed_equity.bond_market import BondMarketObservation, build_bond_market_packet
from distressed_equity.debt_instruments import DebtInstrumentLedger, DebtInstrumentSnapshot, DebtInstrumentVersion


class WrongIdentifierProvider:
    name = "wrong_id"
    bulk_safe = True

    def observations(self, *, cusip, isin, start, end):
        return (
            BondMarketObservation(
                date=date(2022, 12, 30),
                cusip="999999ZZ9",
                price_pct_par=50.0,
                yield_pct=20.0,
                benchmark_yield_pct=4.0,
                source="bad normalization",
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


def test_provider_observation_must_match_requested_stable_debt_identifier():
    try:
        build_bond_market_packet(WrongIdentifierProvider(), _ledger(), date(2022, 12, 31))
    except ValueError as exc:
        assert "returned CUSIP 999999ZZ9" in str(exc)
        assert "requested CUSIP 111111AA1" in str(exc)
    else:
        raise AssertionError("expected mismatched provider identifier to be rejected")

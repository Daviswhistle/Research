from datetime import date

from distressed_equity.bond_market import BondMarketObservation, build_bond_market_packet
from distressed_equity.debt_instruments import DebtInstrumentLedger, DebtInstrumentSnapshot, DebtInstrumentVersion


class RecordingProvider:
    name = "recording"
    bulk_safe = True

    def __init__(self):
        self.requests = []

    def observations(self, *, cusip, isin, start, end):
        self.requests.append((cusip, isin, start, end))
        return (
            BondMarketObservation(
                date=date(2022, 12, 30),
                cusip=cusip,
                price_pct_par=55.0,
                yield_pct=18.0,
                benchmark_yield_pct=4.0,
                source="test",
            ),
        )

    def benchmark_on_or_before(self, *, as_of, remaining_years):
        return None


def test_latest_snapshot_can_omit_identifier_without_losing_verified_stable_identity():
    identified = DebtInstrumentSnapshot(
        as_of_date=date(2022, 6, 30),
        source_accession="q2",
        name="5% Senior Notes",
        maturity_date=date(2028, 6, 15),
        maturity_year=2028,
        cusip="111111AA1",
    )
    later_without_identifier = DebtInstrumentSnapshot(
        as_of_date=date(2022, 9, 30),
        source_accession="q3",
        name="5% Senior Notes",
        maturity_date=date(2028, 6, 15),
        maturity_year=2028,
        cusip=None,
    )
    ledger = DebtInstrumentLedger(
        versions=(
            DebtInstrumentVersion("CUSIP:111111AA1", identified, "exact_identifier"),
            DebtInstrumentVersion("CUSIP:111111AA1", later_without_identifier, "matched"),
        ),
        changes=(),
        unmatched_versions=(),
        warnings=(),
    )
    provider = RecordingProvider()
    packet = build_bond_market_packet(provider, ledger, date(2022, 12, 31))
    assert provider.requests
    assert provider.requests[0][0] == "111111AA1"
    assessment = packet.assessments[0]
    assert assessment.cusip == "111111AA1"
    assert assessment.observation is not None
    assert assessment.observation.price_pct_par == 55.0
    assert any("carried forward" in warning for warning in assessment.warnings)


def test_identifier_first_seen_after_cutoff_is_not_used_for_earlier_market_lookup():
    earlier = DebtInstrumentSnapshot(
        as_of_date=date(2022, 9, 30),
        source_accession="q3",
        name="Senior Notes",
        maturity_year=2028,
    )
    future_identified = DebtInstrumentSnapshot(
        as_of_date=date(2023, 3, 31),
        source_accession="q1-2023",
        name="Senior Notes",
        maturity_year=2028,
        cusip="222222BB2",
    )
    ledger = DebtInstrumentLedger(
        versions=(
            DebtInstrumentVersion("heuristic:senior-notes", earlier, "new"),
            DebtInstrumentVersion("heuristic:senior-notes", future_identified, "matched"),
        ),
        changes=(),
        unmatched_versions=(),
        warnings=(),
    )
    provider = RecordingProvider()
    packet = build_bond_market_packet(provider, ledger, date(2022, 12, 31))
    assert provider.requests == []
    assessment = packet.assessments[0]
    assert assessment.observation is None
    assert assessment.cusip is None
    assert any("verified by the cutoff" in warning for warning in assessment.warnings)

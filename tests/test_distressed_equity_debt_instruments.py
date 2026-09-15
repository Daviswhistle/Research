from datetime import date

from distressed_equity.debt_instruments import (
    DebtInstrumentSnapshot,
    build_debt_instrument_ledger,
    debt_instrument_ledger_to_dict,
    instrument_match_score,
)


def test_explicit_identifier_preserves_identity_across_amendment():
    old = DebtInstrumentSnapshot(
        as_of_date=date(2022, 9, 30),
        source_accession="old",
        name="5.00% Senior Notes due 2027",
        instrument_type="senior_notes",
        principal=500.0,
        maturity_year=2027,
        coupon_pct=5.0,
        seniority="senior_unsecured",
        secured=False,
        cusip="123456AB7",
    )
    new = DebtInstrumentSnapshot(
        as_of_date=date(2023, 3, 31),
        source_accession="new",
        name="Senior Notes due 2028",
        instrument_type="senior_notes",
        principal=450.0,
        maturity_year=2028,
        coupon_pct=5.5,
        seniority="senior_unsecured",
        secured=False,
        cusip="123456AB7",
    )
    ledger = build_debt_instrument_ledger((old, new))
    assert len({version.stable_id for version in ledger.versions}) == 1
    assert ledger.versions[1].match_confidence == "exact_identifier"
    change = ledger.changes[0]
    assert "maturity_extended" in change.classification
    assert "principal_changed" in change.classification
    assert "pricing_changed" in change.classification


def test_heuristic_match_survives_small_label_and_maturity_change():
    old = DebtInstrumentSnapshot(
        as_of_date=date(2022, 6, 30),
        source_accession="a",
        name="Term Loan B due 2026",
        instrument_type="term_loan_b",
        principal=800.0,
        maturity_year=2026,
        coupon_pct=7.0,
        seniority="first_lien",
        secured=True,
    )
    new = DebtInstrumentSnapshot(
        as_of_date=date(2022, 12, 31),
        source_accession="b",
        name="Term Loan B",
        instrument_type="term_loan_b",
        principal=790.0,
        maturity_year=2027,
        coupon_pct=7.0,
        seniority="first_lien",
        secured=True,
    )
    assert instrument_match_score(old, new) >= 55
    ledger = build_debt_instrument_ledger((old, new))
    assert ledger.versions[0].stable_id == ledger.versions[1].stable_id
    assert ledger.versions[1].match_confidence in {"medium", "high"}


def test_ambiguous_matches_are_not_forced():
    first = DebtInstrumentSnapshot(
        as_of_date=date(2022, 3, 31),
        source_accession="a",
        name="Term Loan Alpha",
        instrument_type="term_loan",
        maturity_year=2026,
        coupon_pct=6.0,
        secured=True,
    )
    second = DebtInstrumentSnapshot(
        as_of_date=date(2022, 3, 31),
        source_accession="a",
        name="Term Loan Beta",
        instrument_type="term_loan",
        maturity_year=2026,
        coupon_pct=6.0,
        secured=True,
    )
    current = DebtInstrumentSnapshot(
        as_of_date=date(2022, 9, 30),
        source_accession="b",
        name="Term Loan",
        instrument_type="term_loan",
        maturity_year=2026,
        coupon_pct=6.0,
        secured=True,
    )
    ledger = build_debt_instrument_ledger((first, second, current), ambiguity_margin=8.0)
    assert ledger.versions[-1].match_confidence == "new"
    assert any("ambiguous identity" in warning for warning in ledger.versions[-1].warnings)


def test_ledger_serialization_is_json_native():
    snapshot = DebtInstrumentSnapshot(
        as_of_date=date(2022, 12, 31),
        source_accession="a",
        name="Revolver",
        instrument_type="revolver",
        commitment=100.0,
        drawn=20.0,
        available=80.0,
    )
    payload = debt_instrument_ledger_to_dict(build_debt_instrument_ledger((snapshot,)))
    assert payload["versions"][0]["snapshot"]["as_of_date"] == "2022-12-31"
    assert isinstance(payload["unmatched_versions"], list)

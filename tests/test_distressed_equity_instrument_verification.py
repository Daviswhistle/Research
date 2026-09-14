from distressed_equity.instrument_verification import validate_debt_snapshots_against_source_packet


def source_packet():
    return {
        "analysis_date": "2022-12-31",
        "spans": [
            {
                "span_id": "a:s1",
                "source_accession": "0000000001-22-000001",
                "published_on": "2022-12-01",
                "document_url": "https://www.sec.gov/example/ex41.htm",
                "text": "5.25% Senior Notes due 2028",
            }
        ],
    }


def instrument(**overrides):
    row = {
        "as_of_date": "2022-12-01",
        "source_accession": "0000000001-22-000001",
        "name": "5.25% Senior Notes due 2028",
        "instrument_type": "notes",
        "principal": 500_000_000,
        "maturity_year": 2028,
        "coupon_pct": 5.25,
        "source_refs": ["a:s1"],
    }
    row.update(overrides)
    return {"instruments": [row]}


def test_source_backed_snapshot_validation_accepts_matching_span():
    result = validate_debt_snapshots_against_source_packet(source_packet(), instrument())
    assert result.valid is True
    assert len(result.snapshots) == 1
    assert result.errors == ()


def test_source_backed_snapshot_requires_refs():
    result = validate_debt_snapshots_against_source_packet(
        source_packet(), instrument(source_refs=[])
    )
    assert result.valid is False
    assert any("source_refs are required" in error for error in result.errors)


def test_source_backed_snapshot_rejects_unknown_or_mismatched_accession():
    unknown = validate_debt_snapshots_against_source_packet(
        source_packet(), instrument(source_refs=["missing"])
    )
    assert unknown.valid is False
    assert any("unknown source_refs" in error for error in unknown.errors)

    mismatch = validate_debt_snapshots_against_source_packet(
        source_packet(), instrument(source_accession="0000000001-22-999999")
    )
    assert mismatch.valid is False
    assert any("does not match cited span accessions" in error for error in mismatch.errors)


def test_source_backed_snapshot_rejects_lookahead_as_of_date():
    result = validate_debt_snapshots_against_source_packet(
        source_packet(), instrument(as_of_date="2023-01-01")
    )
    assert result.valid is False
    assert any("after workspace cutoff" in error for error in result.errors)


def test_snapshot_cannot_predate_cited_exhibit():
    result = validate_debt_snapshots_against_source_packet(
        source_packet(), instrument(as_of_date="2022-11-30")
    )
    assert result.valid is False
    assert any("predates" in error for error in result.errors)

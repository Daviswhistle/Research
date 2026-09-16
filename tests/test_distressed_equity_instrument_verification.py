from distressed_equity.instrument_verification import validate_debt_snapshots_against_source_packet


def source_packet(*, published_on="2022-12-01", analysis_date="2022-12-31"):
    return {
        "analysis_date": analysis_date,
        "spans": [
            {
                "span_id": "a:s1",
                "source_accession": "0000000001-22-000001",
                "published_on": published_on,
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
        "verification": {
            "status": "verified",
            "verifier": "test-reviewer",
            "verified_on": "2026-09-16",
            "evidence_reopened": True,
        },
    }
    row.update(overrides)
    return {"instruments": [row]}


def test_source_backed_snapshot_validation_accepts_matching_span():
    result = validate_debt_snapshots_against_source_packet(source_packet(), instrument())
    assert result.valid is True
    assert len(result.snapshots) == 1
    assert result.errors == ()


def test_unverified_candidate_template_cannot_enter_stable_ledger():
    raw = instrument()
    raw["instruments"][0].pop("verification")
    result = validate_debt_snapshots_against_source_packet(source_packet(), raw)
    assert result.valid is False
    assert any("explicit verification is required" in error for error in result.errors)


def test_snapshot_verification_requires_verifier_date_and_reopened_evidence():
    raw = instrument()
    raw["instruments"][0]["verification"] = {
        "status": "verified",
        "verifier": "",
        "verified_on": None,
        "evidence_reopened": False,
    }
    result = validate_debt_snapshots_against_source_packet(source_packet(), raw)
    assert result.valid is False
    assert any("verification.verifier is required" in error for error in result.errors)
    assert any("evidence_reopened must be true" in error for error in result.errors)
    assert any("verification.verified_on must be YYYY-MM-DD" in error for error in result.errors)


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


def test_quarter_end_snapshot_may_predate_later_filing_publication():
    result = validate_debt_snapshots_against_source_packet(
        source_packet(published_on="2022-05-10"),
        instrument(as_of_date="2022-03-31"),
    )
    assert result.valid is True
    assert result.errors == ()


def test_cited_source_publication_cannot_be_after_research_cutoff():
    result = validate_debt_snapshots_against_source_packet(
        source_packet(published_on="2023-01-02"),
        instrument(as_of_date="2022-09-30"),
    )
    assert result.valid is False
    assert any("published after workspace cutoff" in error for error in result.errors)

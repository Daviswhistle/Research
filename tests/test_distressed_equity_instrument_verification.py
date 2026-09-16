from copy import deepcopy

from distressed_equity.instrument_verification import (
    debt_snapshot_verification_template,
    validate_debt_snapshots_against_source_packet,
)


def source_packet(*, published_on="2022-12-01", analysis_date="2022-12-31", text="5.25% Senior Notes due 2028"):
    return {
        "analysis_date": analysis_date,
        "spans": [
            {
                "span_id": "a:s1",
                "source_accession": "0000000001-22-000001",
                "published_on": published_on,
                "document_url": "https://www.sec.gov/example/ex41.htm",
                "text": text,
            }
        ],
    }


def raw_instrument(**overrides):
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


def verified_instrument(packet, **overrides):
    payload = debt_snapshot_verification_template(packet, raw_instrument(**overrides))
    payload["instruments"][0]["verification"].update({
        "status": "verified",
        "verifier": "test-reviewer",
        "verified_on": "2026-09-16",
        "evidence_reopened": True,
    })
    return payload


def test_source_backed_snapshot_validation_accepts_matching_span():
    packet = source_packet()
    result = validate_debt_snapshots_against_source_packet(packet, verified_instrument(packet))
    assert result.valid is True
    assert len(result.snapshots) == 1
    assert result.errors == ()


def test_unverified_candidate_template_cannot_enter_stable_ledger():
    result = validate_debt_snapshots_against_source_packet(source_packet(), raw_instrument())
    assert result.valid is False
    assert any("explicit verification is required" in error for error in result.errors)


def test_snapshot_verification_requires_verifier_date_and_reopened_evidence():
    packet = source_packet()
    raw = debt_snapshot_verification_template(packet, raw_instrument())
    raw["instruments"][0]["verification"].update({
        "status": "verified",
        "verifier": "",
        "verified_on": None,
        "evidence_reopened": False,
    })
    result = validate_debt_snapshots_against_source_packet(packet, raw)
    assert result.valid is False
    assert any("verification.verifier is required" in error for error in result.errors)
    assert any("evidence_reopened must be true" in error for error in result.errors)
    assert any("verification.verified_on must be YYYY-MM-DD" in error for error in result.errors)


def test_source_backed_snapshot_requires_refs():
    packet = source_packet()
    result = validate_debt_snapshots_against_source_packet(
        packet, verified_instrument(packet, source_refs=[])
    )
    assert result.valid is False
    assert any("source_refs are required" in error for error in result.errors)


def test_source_backed_snapshot_rejects_unknown_or_mismatched_accession():
    packet = source_packet()
    unknown = validate_debt_snapshots_against_source_packet(
        packet, verified_instrument(packet, source_refs=["missing"])
    )
    assert unknown.valid is False
    assert any("unknown source_refs" in error for error in unknown.errors)

    mismatch = validate_debt_snapshots_against_source_packet(
        packet, verified_instrument(packet, source_accession="0000000001-22-999999")
    )
    assert mismatch.valid is False
    assert any("does not match cited span accessions" in error for error in mismatch.errors)


def test_source_backed_snapshot_rejects_lookahead_as_of_date():
    packet = source_packet()
    result = validate_debt_snapshots_against_source_packet(
        packet, verified_instrument(packet, as_of_date="2023-01-01")
    )
    assert result.valid is False
    assert any("after workspace cutoff" in error for error in result.errors)


def test_quarter_end_snapshot_may_predate_later_filing_publication():
    packet = source_packet(published_on="2022-05-10")
    result = validate_debt_snapshots_against_source_packet(
        packet,
        verified_instrument(packet, as_of_date="2022-03-31"),
    )
    assert result.valid is True
    assert result.errors == ()


def test_cited_source_publication_cannot_be_after_research_cutoff():
    packet = source_packet(published_on="2023-01-02")
    result = validate_debt_snapshots_against_source_packet(
        packet,
        verified_instrument(packet, as_of_date="2022-09-30"),
    )
    assert result.valid is False
    assert any("published after workspace cutoff" in error for error in result.errors)


def test_verified_snapshot_row_edit_invalidates_approval():
    packet = source_packet()
    approved = verified_instrument(packet)
    tampered = deepcopy(approved)
    tampered["instruments"][0]["principal"] = 700_000_000
    result = validate_debt_snapshots_against_source_packet(packet, tampered)
    assert result.valid is False
    assert any("row fingerprint mismatch" in error for error in result.errors)


def test_verified_snapshot_source_packet_edit_invalidates_approval():
    packet = source_packet()
    approved = verified_instrument(packet)
    changed_packet = source_packet(text="CHANGED evidence text")
    result = validate_debt_snapshots_against_source_packet(changed_packet, approved)
    assert result.valid is False
    assert any("source packet fingerprint mismatch" in error for error in result.errors)

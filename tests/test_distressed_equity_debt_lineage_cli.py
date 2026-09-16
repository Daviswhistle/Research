import json

import pytest

from distressed_equity.debt_lineage_cli import main
from distressed_equity.instrument_verification import debt_snapshot_verification_template


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_packet():
    return {
        "analysis_date": "2023-12-31",
        "spans": [
            {"span_id": "old-span", "source_accession": "old", "published_on": "2023-01-10"},
            {"span_id": "new-span", "source_accession": "new", "published_on": "2023-04-15"},
        ],
    }


def instruments(packet=None):
    packet = packet or source_packet()
    raw = {"instruments": [
        {
            "as_of_date": "2023-01-01", "source_accession": "old", "name": "Old Notes",
            "principal": 1000.0, "maturity_year": 2025, "secured": False,
            "cusip": "111111AA1", "source_refs": ["old-span"],
        },
        {
            "as_of_date": "2023-03-31", "source_accession": "new", "name": "New Notes",
            "principal": 550.0, "maturity_year": 2028, "secured": True,
            "cusip": "222222BB2", "source_refs": ["new-span"],
        },
    ]}
    bound = debt_snapshot_verification_template(packet, raw)
    for row in bound["instruments"]:
        row["verification"].update({
            "status": "verified",
            "verifier": "snapshot-reviewer",
            "verified_on": "2026-09-16",
            "evidence_reopened": True,
        })
    return bound


def events():
    return {"events": [{
        "event_id": "exchange-1", "effective_date": "2023-02-01",
        "event_type": "exchange", "status": "candidate",
        "predecessors": [{"stable_id": "CUSIP:111111AA1", "amount": 600.0}],
        "successors": [{"stable_id": "CUSIP:222222BB2", "amount": 550.0}],
        "source_refs": ["new-span"], "source_accession": "new",
    }]}


def test_cli_preserves_in_place_verification_file(tmp_path):
    packet = source_packet()
    source = tmp_path / "source.json"; write_json(source, packet)
    debt = tmp_path / "debt.json"; write_json(debt, instruments(packet))
    event_path = tmp_path / "events.json"; write_json(event_path, events())
    verification = tmp_path / "verification.json"
    output = tmp_path / "lineage.json"

    assert main([
        str(debt), str(event_path), "--source-packet", str(source),
        "--verification-template-output", str(verification), "-o", str(output),
    ]) == 0
    approval = json.loads(verification.read_text(encoding="utf-8"))
    approval["event_reviews"][0].update({
        "status": "verified", "verifier": "lineage-reviewer",
        "verified_on": "2026-09-16", "evidence_reopened": True,
    })
    write_json(verification, approval)

    assert main([
        str(debt), str(event_path), "--source-packet", str(source),
        "--verification-template-output", str(verification),
        "--verification", str(verification), "-o", str(output),
    ]) == 0
    assert json.loads(verification.read_text(encoding="utf-8"))["event_reviews"][0]["status"] == "verified"
    lineage = json.loads(output.read_text(encoding="utf-8"))["lineage"]
    assert lineage["events"][0]["status"] == "verified"


def test_cli_refuses_verification_without_frozen_source_packet(tmp_path):
    debt = tmp_path / "debt.json"; write_json(debt, instruments())
    event_path = tmp_path / "events.json"; write_json(event_path, events())
    verification = tmp_path / "verification.json"; write_json(verification, {})
    with pytest.raises(ValueError, match="requires --source-packet"):
        main([str(debt), str(event_path), "--verification", str(verification)])

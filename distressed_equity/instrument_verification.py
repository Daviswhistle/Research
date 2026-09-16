from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from typing import Any

from .debt_instruments import DebtInstrumentSnapshot, debt_snapshots_from_dict


@dataclass(frozen=True)
class DebtSnapshotSourceValidation:
    valid: bool
    snapshots: tuple[DebtInstrumentSnapshot, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def _parse_date(value: Any, field: str) -> date:
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be YYYY-MM-DD")
    return date.fromisoformat(value[:10])


def _jsonable(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _fingerprint(value: Any) -> str:
    canonical = json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def debt_snapshot_source_packet_fingerprint(source_packet: dict[str, Any]) -> str:
    """Fingerprint the exact frozen evidence packet used for snapshot review."""

    if not isinstance(source_packet, dict):
        raise ValueError("source packet must be an object")
    return _fingerprint(source_packet)


def debt_snapshot_row_fingerprint(raw: dict[str, Any]) -> str:
    """Fingerprint every snapshot semantic except the reviewer attestation itself."""

    if not isinstance(raw, dict):
        raise ValueError("debt snapshot row must be an object")
    semantic = {key: value for key, value in raw.items() if key != "verification"}
    return _fingerprint(semantic)


def debt_snapshot_verification_template(
    source_packet: dict[str, Any],
    raw_instruments: dict[str, Any] | list[dict[str, Any]],
) -> dict[str, Any]:
    """Bind finalized candidate rows to the frozen evidence before reviewer approval.

    Call this *after* resolving as_of_date and every other snapshot field. The
    reviewer then changes only status/verifier/verified_on/evidence_reopened. Any
    later semantic row edit or source-packet change invalidates the approval.
    """

    source_fingerprint = debt_snapshot_source_packet_fingerprint(source_packet)
    rows = raw_instruments if isinstance(raw_instruments, list) else raw_instruments.get("instruments", [])
    if not isinstance(rows, list):
        raise ValueError("instruments must be a list")
    bound_rows: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError(f"instrument {index}: row must be an object")
        row = dict(raw)
        row["verification"] = {
            "schema_version": "2",
            "source_packet_fingerprint": source_fingerprint,
            "row_fingerprint": debt_snapshot_row_fingerprint(row),
            "status": "unverified",
            "verifier": None,
            "verified_on": None,
            "evidence_reopened": False,
        }
        bound_rows.append(row)
    payload = dict(raw_instruments) if isinstance(raw_instruments, dict) else {}
    payload["schema_version"] = "2"
    payload["source_packet_fingerprint"] = source_fingerprint
    payload["instruments"] = bound_rows
    notes = list(payload.get("verification_notes") or [])
    notes.extend([
        "Verification is bound to both the complete snapshot row and the frozen source packet.",
        "After generating this template, change only reviewer-attestation fields; editing snapshot semantics requires a new template/fingerprint.",
    ])
    payload["verification_notes"] = list(dict.fromkeys(str(item) for item in notes if str(item).strip()))
    return payload


def _validate_snapshot_verification(
    raw: dict[str, Any],
    name: str,
    source_packet_fingerprint: str,
) -> tuple[str, ...]:
    errors: list[str] = []
    verification = raw.get("verification")
    if not isinstance(verification, dict):
        return (
            f"{name}: explicit verification is required before a debt snapshot can enter the stable ledger",
        )
    if str(verification.get("schema_version") or "") != "2":
        errors.append(f"{name}: verification.schema_version must be '2'")
    if verification.get("source_packet_fingerprint") != source_packet_fingerprint:
        errors.append(f"{name}: verification source packet fingerprint mismatch")
    try:
        expected_row_fingerprint = debt_snapshot_row_fingerprint(raw)
    except (TypeError, ValueError) as exc:
        errors.append(f"{name}: cannot fingerprint snapshot row: {exc}")
    else:
        if verification.get("row_fingerprint") != expected_row_fingerprint:
            errors.append(f"{name}: verification row fingerprint mismatch")
    status = str(verification.get("status") or "").strip().lower()
    if status != "verified":
        errors.append(f"{name}: verification.status must be 'verified'")
    verifier = str(verification.get("verifier") or "").strip()
    if not verifier:
        errors.append(f"{name}: verification.verifier is required")
    if verification.get("evidence_reopened") is not True:
        errors.append(f"{name}: verification.evidence_reopened must be true")
    try:
        _parse_date(verification.get("verified_on"), f"{name}.verification.verified_on")
    except ValueError as exc:
        errors.append(str(exc))
    return tuple(errors)


def validate_debt_snapshots_against_source_packet(
    source_packet: dict[str, Any],
    raw_instruments: dict[str, Any] | list[dict[str, Any]],
) -> DebtSnapshotSourceValidation:
    """Require verified ledger snapshots to trace back to frozen SEC exhibit spans.

    `as_of_date` is the economic/effective measurement date of the debt balance or
    terms. SEC `published_on` is when that evidence became public. A quarter-end
    snapshot may therefore legitimately predate the filing that reports it. Only
    the research cutoff constrains publication. Promotion also requires a reviewer
    attestation bound to both the complete snapshot semantics and frozen source
    packet, so editing either after review invalidates approval.
    """

    errors: list[str] = []
    warnings: list[str] = []
    try:
        analysis_date = _parse_date(source_packet.get("analysis_date"), "source_packet.analysis_date")
        source_fingerprint = debt_snapshot_source_packet_fingerprint(source_packet)
    except (TypeError, ValueError) as exc:
        return DebtSnapshotSourceValidation(False, (), (str(exc),), ())

    spans_raw = source_packet.get("spans", [])
    if not isinstance(spans_raw, list):
        return DebtSnapshotSourceValidation(False, (), ("source_packet.spans must be a list",), ())
    span_map: dict[str, dict[str, Any]] = {}
    for raw in spans_raw:
        if not isinstance(raw, dict):
            continue
        span_id = str(raw.get("span_id") or "").strip()
        if span_id:
            span_map[span_id] = raw
    if not span_map:
        warnings.append("source packet contains no extracted SEC instrument spans")

    rows = raw_instruments if isinstance(raw_instruments, list) else raw_instruments.get("instruments", [])
    if not isinstance(rows, list):
        return DebtSnapshotSourceValidation(False, (), ("instruments must be a list",), tuple(warnings))

    for idx, raw in enumerate(rows):
        if not isinstance(raw, dict):
            errors.append(f"instrument {idx}: row must be an object")
            continue
        name = str(raw.get("name") or f"instrument {idx}")
        errors.extend(_validate_snapshot_verification(raw, name, source_fingerprint))

        refs = [str(item).strip() for item in raw.get("source_refs", []) if str(item).strip()]
        if not refs:
            errors.append(f"{name}: source_refs are required for source-backed ledger ingestion")
            continue
        unknown = [ref for ref in refs if ref not in span_map]
        if unknown:
            errors.append(f"{name}: unknown source_refs: {', '.join(unknown)}")
            continue
        accession = str(raw.get("source_accession") or "").strip()
        referenced_accessions = {
            str(span_map[ref].get("source_accession") or "").strip()
            for ref in refs
        }
        if not accession:
            errors.append(f"{name}: source_accession is required")
        elif referenced_accessions != {accession}:
            errors.append(
                f"{name}: source_accession {accession!r} does not match cited span accessions {sorted(referenced_accessions)!r}"
            )

        try:
            as_of = _parse_date(raw.get("as_of_date"), f"{name}.as_of_date")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if as_of > analysis_date:
            errors.append(
                f"{name}: as_of_date {as_of.isoformat()} is after workspace cutoff {analysis_date.isoformat()}"
            )
        for ref in refs:
            try:
                published_on = _parse_date(span_map[ref].get("published_on"), f"span {ref}.published_on")
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if published_on > analysis_date:
                errors.append(
                    f"{name}: cited source {ref} was published after workspace cutoff {analysis_date.isoformat()}"
                )

    try:
        snapshots = debt_snapshots_from_dict(raw_instruments)
    except (TypeError, ValueError) as exc:
        errors.append(f"debt snapshot schema: {exc}")
        snapshots = ()

    return DebtSnapshotSourceValidation(
        valid=not errors,
        snapshots=snapshots if not errors else (),
        errors=tuple(dict.fromkeys(errors)),
        warnings=tuple(dict.fromkeys(warnings)),
    )

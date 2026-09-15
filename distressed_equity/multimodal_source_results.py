from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from typing import Any

from .agent_results import agent_result_from_dict, validate_agent_result
from .multimodal_source_reader import (
    MultimodalSourceTask,
    multimodal_source_result_template,
)


ALLOWED_STATUS = frozenset({"complete", "partial", "unresolved"})
ALLOWED_CONFIDENCE = frozenset({"high", "medium", "low"})
MULTIMODAL_PATCH_PATHS = frozenset(
    {
        "capital_structure.current_shares",
        "capital_structure.net_debt",
        "capital_structure.other_senior_claims",
        "starting_liquidity",
        "available_credit",
        "asset_monetization",
        "restricted_cash",
        "debt_obligations",
        "covenants",
    }
)
_SEMANTIC_KEYS = (
    "schema_version",
    "task_id",
    "analysis_date",
    "source",
    "status",
    "findings",
    "disconfirming_or_ambiguous_evidence",
    "engine_patches",
    "unresolved",
    "warnings",
)


def multimodal_result_schema(task: MultimodalSourceTask) -> dict[str, Any]:
    """Provider-facing shape; local validation remains the authoritative contract."""

    return {
        "type": "object",
        "properties": {
            "schema_version": {"type": "string", "const": "1"},
            "task_id": {"type": "string", "const": task.task_id},
            "analysis_date": {"type": "string", "const": task.analysis_date.isoformat()},
            "source": {"type": "object"},
            "status": {"type": "string", "enum": sorted(ALLOWED_STATUS)},
            "findings": {"type": "array"},
            "disconfirming_or_ambiguous_evidence": {"type": "array"},
            "engine_patches": {"type": "array"},
            "unresolved": {"type": "array", "items": {"type": "string"}},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": list(_SEMANTIC_KEYS),
        "additionalProperties": True,
    }


@dataclass(frozen=True)
class MultimodalResultValidation:
    valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def validate_multimodal_source_result(
    task: MultimodalSourceTask,
    raw: dict[str, Any],
) -> MultimodalResultValidation:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(raw, dict):
        return MultimodalResultValidation(False, ("result must be an object",), ())
    if task.source_filing_date and task.source_filing_date > task.analysis_date:
        errors.append("source_filing_date is after analysis_date")
    if raw.get("schema_version") != "1":
        errors.append("schema_version must be '1'")
    if raw.get("task_id") != task.task_id:
        errors.append("task_id does not match frozen task")
    if str(raw.get("analysis_date") or "")[:10] != task.analysis_date.isoformat():
        errors.append("analysis_date does not match frozen task")
    if str(raw.get("status") or "").lower() not in ALLOWED_STATUS:
        errors.append("status must be complete, partial, or unresolved")

    source = raw.get("source")
    expected = multimodal_source_result_template(task)["source"]
    if not isinstance(source, dict):
        errors.append("source must be an object")
    else:
        for key, value in expected.items():
            if source.get(key) != value:
                errors.append(f"source.{key} does not match frozen task")

    findings = raw.get("findings")
    if not isinstance(findings, list):
        errors.append("findings must be a list")
        findings = []
    finding_ids: set[str] = set()
    unresolved_findings: set[str] = set()
    evidence_ids: set[str] = set()
    for idx, finding in enumerate(findings):
        if not isinstance(finding, dict):
            errors.append(f"finding {idx} must be an object")
            continue
        finding_id = str(finding.get("finding_id") or "").strip()
        if not finding_id:
            errors.append(f"finding {idx} requires finding_id")
        elif finding_id in finding_ids:
            errors.append(f"duplicate finding_id: {finding_id}")
        else:
            finding_ids.add(finding_id)
        if not str(finding.get("visible_fact") or "").strip():
            errors.append(f"finding {finding_id or idx} requires visible_fact")
        if str(finding.get("confidence") or "").lower() not in ALLOWED_CONFIDENCE:
            errors.append(f"finding {finding_id or idx} has invalid confidence")
        unresolved = finding.get("unresolved")
        if not isinstance(unresolved, bool):
            errors.append(f"finding {finding_id or idx} unresolved must be boolean")
        elif unresolved and finding_id:
            unresolved_findings.add(finding_id)
        if not isinstance(finding.get("interpretation"), str):
            errors.append(f"finding {finding_id or idx} interpretation must be string")
        if not isinstance(finding.get("investment_implication"), str):
            errors.append(f"finding {finding_id or idx} investment_implication must be string")

        evidence = finding.get("evidence")
        if not isinstance(evidence, list):
            errors.append(f"finding {finding_id or idx} evidence must be a list")
            continue
        if not evidence and unresolved is False:
            errors.append(f"finding {finding_id or idx} has no evidence")
        for evidence_idx, item in enumerate(evidence):
            if not isinstance(item, dict):
                errors.append(f"finding {finding_id or idx} evidence {evidence_idx} must be an object")
                continue
            evidence_id = str(item.get("evidence_id") or "").strip()
            if not evidence_id:
                errors.append(f"finding {finding_id or idx} evidence requires evidence_id")
            elif evidence_id in evidence_ids:
                errors.append(f"duplicate evidence_id: {evidence_id}")
            else:
                evidence_ids.add(evidence_id)
            page = item.get("page")
            if "pdf" in task.media_type.lower():
                if not isinstance(page, int) or isinstance(page, bool) or page <= 0:
                    errors.append(f"evidence {evidence_id or evidence_idx} requires positive PDF page")
            elif page is not None and (
                not isinstance(page, int) or isinstance(page, bool) or page <= 0
            ):
                errors.append(f"evidence {evidence_id or evidence_idx} page must be positive or null")
            if not str(item.get("region") or "").strip():
                errors.append(f"evidence {evidence_id or evidence_idx} requires region")

    ambiguous = raw.get("disconfirming_or_ambiguous_evidence")
    if not isinstance(ambiguous, list):
        errors.append("disconfirming_or_ambiguous_evidence must be a list")

    patches = raw.get("engine_patches")
    if not isinstance(patches, list):
        errors.append("engine_patches must be a list")
        patches = []
    patch_ids: set[str] = set()
    for idx, patch in enumerate(patches):
        if not isinstance(patch, dict):
            errors.append(f"engine patch {idx} must be an object")
            continue
        patch_id = str(patch.get("patch_id") or "").strip()
        if not patch_id:
            errors.append(f"engine patch {idx} requires patch_id")
        elif patch_id in patch_ids:
            errors.append(f"duplicate patch_id: {patch_id}")
        else:
            patch_ids.add(patch_id)
        path = str(patch.get("path") or "").strip()
        if path not in MULTIMODAL_PATCH_PATHS:
            errors.append(f"engine patch {patch_id or idx} path is not allowed: {path!r}")
        if str(patch.get("confidence") or "").lower() not in ALLOWED_CONFIDENCE:
            errors.append(f"engine patch {patch_id or idx} has invalid confidence")
        finding_refs = patch.get("finding_refs")
        if not isinstance(finding_refs, list) or not finding_refs or not all(
            isinstance(ref, str) for ref in finding_refs
        ):
            errors.append(f"engine patch {patch_id or idx} requires finding_refs")
            finding_refs = []
        unknown_findings = [ref for ref in finding_refs if ref not in finding_ids]
        if unknown_findings:
            errors.append(
                f"engine patch {patch_id or idx} references unknown findings: {', '.join(unknown_findings)}"
            )
        unresolved_refs = [ref for ref in finding_refs if ref in unresolved_findings]
        if unresolved_refs:
            errors.append(
                f"engine patch {patch_id or idx} depends on unresolved findings: {', '.join(unresolved_refs)}"
            )
        evidence_refs = patch.get("evidence_refs")
        if not isinstance(evidence_refs, list) or not evidence_refs or not all(
            isinstance(ref, str) for ref in evidence_refs
        ):
            errors.append(f"engine patch {patch_id or idx} requires evidence_refs")
            evidence_refs = []
        unknown_evidence = [ref for ref in evidence_refs if ref not in evidence_ids]
        if unknown_evidence:
            errors.append(
                f"engine patch {patch_id or idx} references unknown evidence: {', '.join(unknown_evidence)}"
            )

    for field in ("unresolved", "warnings"):
        value = raw.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            errors.append(f"{field} must be a list of strings")
    raw_warnings = raw.get("warnings")
    if isinstance(raw_warnings, list):
        warnings.extend(str(item) for item in raw_warnings if isinstance(item, str))
    if patches:
        warnings.append("multimodal engine patches remain proposals until independent verification")
    return MultimodalResultValidation(
        valid=not errors,
        errors=tuple(errors),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def multimodal_result_fingerprint(raw: dict[str, Any]) -> str:
    semantic = {key: raw.get(key) for key in _SEMANTIC_KEYS}
    canonical = json.dumps(
        semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def multimodal_verification_template(
    task: MultimodalSourceTask,
    result: dict[str, Any],
) -> dict[str, Any]:
    validation = validate_multimodal_source_result(task, result)
    if not validation.valid:
        raise ValueError("invalid multimodal result: " + "; ".join(validation.errors))
    return {
        "schema_version": "1",
        "task_id": task.task_id,
        "result_fingerprint": multimodal_result_fingerprint(result),
        "verifier": None,
        "verified_on": None,
        "patch_reviews": [
            {
                "patch_id": patch["patch_id"],
                "status": "unverified",
                "evidence_reopened": False,
                "reviewer_note": "",
            }
            for patch in result.get("engine_patches", [])
        ],
        "notes": [
            "Mark a patch verified only after re-opening every cited original page/image region.",
            "Model confidence alone is not verification.",
        ],
    }


def _review_index(verification: dict[str, Any]) -> dict[str, dict[str, Any]]:
    reviews = verification.get("patch_reviews")
    if not isinstance(reviews, list):
        raise ValueError("verification patch_reviews must be a list")
    index: dict[str, dict[str, Any]] = {}
    for idx, review in enumerate(reviews):
        if not isinstance(review, dict):
            raise ValueError(f"verification patch review {idx} must be an object")
        patch_id = str(review.get("patch_id") or "").strip()
        if not patch_id or patch_id in index:
            raise ValueError(f"invalid or duplicate verification patch_id: {patch_id!r}")
        status = str(review.get("status") or "").lower()
        if status not in {"verified", "rejected", "unverified"}:
            raise ValueError(f"verification patch {patch_id} has invalid status")
        if not isinstance(review.get("evidence_reopened"), bool):
            raise ValueError(f"verification patch {patch_id} evidence_reopened must be boolean")
        if status == "verified" and not review["evidence_reopened"]:
            raise ValueError(f"verification patch {patch_id} was not re-opened")
        index[patch_id] = review
    return index


def promote_verified_multimodal_result(
    task: MultimodalSourceTask,
    result: dict[str, Any],
    verification: dict[str, Any],
) -> dict[str, Any]:
    """Convert only independently verified visual patches to existing agent-result JSON."""

    validation = validate_multimodal_source_result(task, result)
    if not validation.valid:
        raise ValueError("invalid multimodal result: " + "; ".join(validation.errors))
    if task.source_filing_date is None:
        raise ValueError("source_filing_date is required for promotion")
    if verification.get("schema_version") != "1":
        raise ValueError("verification schema_version must be '1'")
    if verification.get("task_id") != task.task_id:
        raise ValueError("verification task_id mismatch")
    if verification.get("result_fingerprint") != multimodal_result_fingerprint(result):
        raise ValueError("verification fingerprint does not match result")
    verifier = str(verification.get("verifier") or "").strip()
    if not verifier:
        raise ValueError("verification verifier is required")
    verified_on = str(verification.get("verified_on") or "")
    try:
        date.fromisoformat(verified_on[:10])
    except ValueError:
        raise ValueError("verification verified_on must be YYYY-MM-DD") from None

    reviews = _review_index(verification)
    patches_by_id = {patch["patch_id"]: patch for patch in result.get("engine_patches", [])}
    unknown_reviews = sorted(set(reviews) - set(patches_by_id))
    if unknown_reviews:
        raise ValueError("verification references unknown patches: " + ", ".join(unknown_reviews))

    selected = [
        patch
        for patch_id, patch in patches_by_id.items()
        if reviews.get(patch_id, {}).get("status") == "verified"
        and reviews[patch_id]["evidence_reopened"]
    ]
    findings_by_evidence: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for finding in result.get("findings", []):
        for evidence in finding.get("evidence", []):
            findings_by_evidence[evidence["evidence_id"]] = (finding, evidence)

    task_ns = hashlib.sha256(task.task_id.encode("utf-8")).hexdigest()[:12]
    id_map = {
        evidence_id: f"mm:{task_ns}:{evidence_id}"
        for patch in selected
        for evidence_id in patch["evidence_refs"]
    }
    evidence_out: list[dict[str, Any]] = []
    for original_id in sorted(id_map):
        finding, evidence = findings_by_evidence[original_id]
        page = evidence.get("page")
        region = str(evidence.get("region") or "")
        locator = f"page {page}; region {region}" if page else f"visual region {region}"
        notes = (
            f"finding_id={finding['finding_id']}; "
            f"interpretation={finding.get('interpretation', '')}; "
            f"investment_implication={finding.get('investment_implication', '')}"
        )
        if evidence.get("visible_text"):
            notes += f"; visible_text={evidence['visible_text']}"
        evidence_out.append(
            {
                "evidence_id": id_map[original_id],
                "claim": finding["visible_fact"],
                "source": task.source_url,
                "published_on": task.source_filing_date.isoformat(),
                "event_on": None,
                "evidence_type": "fact",
                "locator": locator,
                "notes": notes,
            }
        )

    patch_out = [
        {
            "path": patch["path"],
            "value": patch.get("value"),
            "evidence_refs": [id_map[ref] for ref in patch["evidence_refs"]],
            "confidence": patch["confidence"],
            "rationale": patch.get("rationale"),
        }
        for patch in selected
    ]
    unresolved = [str(item) for item in result.get("unresolved", []) if str(item).strip()]
    warnings = list(validation.warnings)
    for patch_id in patches_by_id:
        review = reviews.get(patch_id)
        if review is None or review["status"] == "unverified":
            unresolved.append(f"multimodal patch not independently verified: {patch_id}")
        elif review["status"] == "rejected":
            note = str(review.get("reviewer_note") or "").strip()
            warnings.append(
                f"multimodal patch rejected by verifier: {patch_id}"
                + (f" ({note})" if note else "")
            )
    if selected:
        warnings.append(f"multimodal patches verified by {verifier} on {verified_on[:10]}")

    promoted = {
        "schema_version": "1",
        "task_name": "capital_stack_auditor",
        "analysis_date": task.analysis_date.isoformat(),
        "status": "partial" if unresolved else str(result.get("status") or "partial"),
        "evidence": evidence_out,
        "patches": patch_out,
        "unresolved": list(dict.fromkeys(unresolved)),
        "warnings": list(dict.fromkeys(warnings)),
        "multimodal_provenance": {
            "task_id": task.task_id,
            "result_fingerprint": multimodal_result_fingerprint(result),
            "verifier": verifier,
            "verified_on": verified_on[:10],
            "source_url": task.source_url,
        },
    }
    agent_result = agent_result_from_dict(promoted)
    agent_validation = validate_agent_result(
        agent_result, expected_analysis_date=task.analysis_date
    )
    if not agent_validation.valid:
        raise ValueError(
            "verified multimodal patch does not satisfy agent ingestion contract: "
            + "; ".join(agent_validation.errors)
        )
    return promoted

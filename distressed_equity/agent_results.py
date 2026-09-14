from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import date
from typing import Any, Iterable

from .evidence import EvidenceRecord, validate_point_in_time

SCHEMA_VERSION = "1"
ALLOWED_EVIDENCE_TYPES = {"fact", "estimate", "inference", "forecast", "unknown"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}

TASK_PATCH_PATHS: dict[str, frozenset[str]] = {
    "capital_stack_extractor": frozenset(
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
    ),
    "historical_market_reconstructor": frozenset(
        {
            "capital_structure.current_price",
            "peak_market_cap",
            "current_enterprise_value",
        }
    ),
    "impairment_and_normalization_researcher": frozenset(
        {
            "ttm_revenue",
            "has_real_customers",
            "is_pre_revenue",
            "has_historical_operating_profit",
            "monthly_cash_burn",
            "recovery_month",
            "base_scenario",
            "reference_scenario",
            "downside_scenario",
            "critical_assumptions_complete",
        }
    ),
    "survival_auditor": frozenset(
        {
            "starting_liquidity",
            "available_credit",
            "asset_monetization",
            "restricted_cash",
            "monthly_cash_burn",
            "recovery_month",
            "debt_obligations",
            "covenants",
        }
    ),
    "business_and_normalization_auditor": frozenset(
        {
            "ttm_revenue",
            "has_real_customers",
            "is_pre_revenue",
            "has_historical_operating_profit",
            "monthly_cash_burn",
            "recovery_month",
            "base_scenario",
            "reference_scenario",
            "downside_scenario",
            "critical_assumptions_complete",
        }
    ),
    "capital_stack_auditor": frozenset(
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
    ),
    "probability_calibrator": frozenset(),
    "model_verifier": frozenset(),
}


@dataclass(frozen=True)
class PatchProposal:
    path: str
    value: Any
    evidence_refs: tuple[str, ...]
    confidence: str = "medium"
    rationale: str | None = None


@dataclass(frozen=True)
class AgentResult:
    schema_version: str
    task_name: str
    analysis_date: date
    status: str
    evidence: tuple[EvidenceRecord, ...]
    patches: tuple[PatchProposal, ...]
    unresolved: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ResultValidation:
    valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class IngestionReport:
    screening_candidate_draft: dict[str, Any]
    evidence: tuple[EvidenceRecord, ...]
    applied_paths: tuple[str, ...]
    skipped_low_confidence: tuple[str, ...]
    conflicts: tuple[str, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    unresolved: tuple[str, ...]
    missing_for_screening: tuple[str, ...]
    ready_for_screening: bool


def _parse_date(value: Any, field: str) -> date:
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be YYYY-MM-DD")
    return date.fromisoformat(value[:10])


def _evidence_from_dict(raw: dict[str, Any]) -> EvidenceRecord:
    evidence_id = str(raw.get("evidence_id") or "").strip()
    if not evidence_id:
        raise ValueError("evidence_id is required")
    claim = str(raw.get("claim") or "").strip()
    source = str(raw.get("source") or "").strip()
    evidence_type = str(raw.get("evidence_type") or "").strip().lower()
    if not claim:
        raise ValueError(f"evidence {evidence_id}: claim is required")
    if not source:
        raise ValueError(f"evidence {evidence_id}: source is required")
    if evidence_type not in ALLOWED_EVIDENCE_TYPES:
        raise ValueError(
            f"evidence {evidence_id}: evidence_type must be one of {sorted(ALLOWED_EVIDENCE_TYPES)}"
        )
    return EvidenceRecord(
        claim=claim,
        source=source,
        published_on=_parse_date(raw.get("published_on"), "published_on"),
        evidence_type=evidence_type,
        event_on=(
            _parse_date(raw.get("event_on"), "event_on")
            if raw.get("event_on") is not None
            else None
        ),
        locator=(str(raw["locator"]) if raw.get("locator") else None),
        notes=(str(raw["notes"]) if raw.get("notes") else None),
        evidence_id=evidence_id,
    )


def _patch_from_dict(raw: dict[str, Any]) -> PatchProposal:
    path = str(raw.get("path") or "").strip()
    if not path:
        raise ValueError("patch path is required")
    refs = tuple(str(item).strip() for item in raw.get("evidence_refs", []) if str(item).strip())
    confidence = str(raw.get("confidence") or "medium").strip().lower()
    if confidence not in ALLOWED_CONFIDENCE:
        raise ValueError(f"patch {path}: confidence must be one of {sorted(ALLOWED_CONFIDENCE)}")
    return PatchProposal(
        path=path,
        value=raw.get("value"),
        evidence_refs=refs,
        confidence=confidence,
        rationale=(str(raw["rationale"]) if raw.get("rationale") else None),
    )


def agent_result_from_dict(raw: dict[str, Any]) -> AgentResult:
    evidence = tuple(_evidence_from_dict(item) for item in raw.get("evidence", []))
    patches = tuple(_patch_from_dict(item) for item in raw.get("patches", []))
    return AgentResult(
        schema_version=str(raw.get("schema_version") or ""),
        task_name=str(raw.get("task_name") or "").strip(),
        analysis_date=_parse_date(raw.get("analysis_date"), "analysis_date"),
        status=str(raw.get("status") or "unresolved").strip().lower(),
        evidence=evidence,
        patches=patches,
        unresolved=tuple(str(item) for item in raw.get("unresolved", []) if str(item).strip()),
        warnings=tuple(str(item) for item in raw.get("warnings", []) if str(item).strip()),
    )


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_patch_value(path: str, value: Any) -> str | None:
    positive = {
        "capital_structure.current_price",
        "capital_structure.current_shares",
        "peak_market_cap",
    }
    signed_numeric = {
        "capital_structure.net_debt",
        "current_enterprise_value",
        "monthly_cash_burn",
    }
    nonnegative = {
        "capital_structure.other_senior_claims",
        "ttm_revenue",
        "starting_liquidity",
        "available_credit",
        "asset_monetization",
        "restricted_cash",
    }
    booleans = {
        "has_real_customers",
        "is_pre_revenue",
        "has_historical_operating_profit",
        "critical_assumptions_complete",
    }
    if path in positive:
        if not _is_number(value) or float(value) <= 0:
            return "must be a positive number"
    elif path in signed_numeric:
        if not _is_number(value):
            return "must be numeric"
    elif path in nonnegative:
        if not _is_number(value) or float(value) < 0:
            return "must be a non-negative number"
    elif path == "recovery_month":
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            return "must be a positive integer month"
    elif path in booleans:
        if not isinstance(value, bool):
            return "must be boolean"
    elif path in {"debt_obligations", "covenants"}:
        if not isinstance(value, list):
            return "must be a list"
        for idx, item in enumerate(value):
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                return f"item {idx} must be an object with a non-empty name"
            if path == "debt_obligations":
                if not _is_number(item.get("amount")) or float(item["amount"]) < 0:
                    return f"item {idx} amount must be non-negative"
                due_month = item.get("due_month")
                if not isinstance(due_month, int) or isinstance(due_month, bool) or due_month < 0:
                    return f"item {idx} due_month must be a non-negative integer"
    elif path in {"base_scenario", "reference_scenario", "downside_scenario"}:
        if value is not None and not isinstance(value, dict):
            return "must be an object or null"
        if isinstance(value, dict):
            for key in ("enterprise_value", "exit_net_debt"):
                if not _is_number(value.get(key)):
                    return f"scenario {key} must be numeric"
            if float(value["enterprise_value"]) < 0:
                return "scenario enterprise_value must be non-negative"
            assumptions = value.get("critical_assumptions", [])
            if not isinstance(assumptions, list) or not all(isinstance(item, str) for item in assumptions):
                return "scenario critical_assumptions must be a list of strings"
    return None


def validate_agent_result(
    result: AgentResult,
    *,
    expected_analysis_date: date,
) -> ResultValidation:
    errors: list[str] = []
    warnings: list[str] = list(result.warnings)
    if result.schema_version != SCHEMA_VERSION:
        errors.append(f"unsupported schema_version: {result.schema_version!r}")
    if result.task_name not in TASK_PATCH_PATHS:
        errors.append(f"unknown task_name: {result.task_name!r}")
    if result.analysis_date != expected_analysis_date:
        errors.append(
            f"analysis_date mismatch: result={result.analysis_date.isoformat()} expected={expected_analysis_date.isoformat()}"
        )
    if result.status not in {"complete", "partial", "unresolved"}:
        errors.append("status must be complete, partial, or unresolved")

    ids = [record.evidence_id for record in result.evidence]
    if len(ids) != len(set(ids)):
        errors.append("duplicate evidence_id values")
    evidence_ids = {item for item in ids if item}
    errors.extend(validate_point_in_time(result.evidence, expected_analysis_date))

    allowed = TASK_PATCH_PATHS.get(result.task_name, frozenset())
    for patch in result.patches:
        if patch.path not in allowed:
            errors.append(f"task {result.task_name} is not allowed to patch {patch.path}")
        if not patch.evidence_refs:
            errors.append(f"patch {patch.path} has no evidence_refs")
        unknown_refs = [ref for ref in patch.evidence_refs if ref not in evidence_ids]
        if unknown_refs:
            errors.append(f"patch {patch.path} references unknown evidence: {', '.join(unknown_refs)}")
        problem = _validate_patch_value(patch.path, patch.value)
        if problem:
            errors.append(f"patch {patch.path}: {problem}")
        if patch.confidence == "low":
            warnings.append(f"low-confidence patch will not be auto-applied: {patch.path}")

    return ResultValidation(valid=not errors, errors=tuple(errors), warnings=tuple(dict.fromkeys(warnings)))


def _get_path(target: dict[str, Any], path: str) -> Any:
    node: Any = target
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _set_path(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = target
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = deepcopy(value)


def missing_for_screening(candidate: dict[str, Any]) -> tuple[str, ...]:
    required_paths = (
        "ticker",
        "company_name",
        "analysis_date",
        "capital_structure.current_price",
        "capital_structure.current_shares",
        "capital_structure.net_debt",
        "capital_structure.other_senior_claims",
        "available_credit",
        "asset_monetization",
        "restricted_cash",
        "base_scenario",
        "downside_scenario",
    )
    missing = [path for path in required_paths if _get_path(candidate, path) is None]
    return tuple(missing)


def _existing_is_unset(path: str, existing: Any, proposed: Any) -> bool:
    if existing is None:
        return True
    if path in {"debt_obligations", "covenants"} and existing == []:
        return True
    if path == "critical_assumptions_complete" and existing is False and proposed is True:
        return True
    return False


def ingest_agent_results(
    research_packet: dict[str, Any],
    results: Iterable[AgentResult],
    *,
    allow_overwrite: bool = False,
    apply_low_confidence: bool = False,
) -> IngestionReport:
    packet = deepcopy(research_packet)
    screening_wrapper = packet.get("screening_draft")
    if not isinstance(screening_wrapper, dict):
        raise ValueError("research packet is missing screening_draft")
    candidate = screening_wrapper.get("screening_candidate_draft")
    if not isinstance(candidate, dict):
        raise ValueError("screening_draft is missing screening_candidate_draft")

    identity = screening_wrapper.get("identity", {})
    analysis_date = _parse_date(identity.get("analysis_date") or candidate.get("analysis_date"), "analysis_date")

    base_evidence: list[EvidenceRecord] = []
    for raw in packet.get("evidence", []):
        if not isinstance(raw, dict):
            continue
        try:
            base_evidence.append(
                EvidenceRecord(
                    claim=str(raw.get("claim") or ""),
                    source=str(raw.get("source") or ""),
                    published_on=_parse_date(raw.get("published_on"), "published_on"),
                    evidence_type=str(raw.get("evidence_type") or "fact"),
                    event_on=(
                        _parse_date(raw.get("event_on"), "event_on")
                        if raw.get("event_on")
                        else None
                    ),
                    locator=(str(raw["locator"]) if raw.get("locator") else None),
                    notes=(str(raw["notes"]) if raw.get("notes") else None),
                    evidence_id=(str(raw["evidence_id"]) if raw.get("evidence_id") else None),
                )
            )
        except ValueError:
            continue

    applied: list[str] = []
    low_skipped: list[str] = []
    conflicts: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []
    unresolved: list[str] = []
    combined_evidence = list(base_evidence)

    for result in results:
        validation = validate_agent_result(result, expected_analysis_date=analysis_date)
        errors.extend(f"{result.task_name}: {item}" for item in validation.errors)
        warnings.extend(f"{result.task_name}: {item}" for item in validation.warnings)
        unresolved.extend(f"{result.task_name}: {item}" for item in result.unresolved)
        if not validation.valid:
            continue
        for record in result.evidence:
            namespaced_id = (
                f"{result.task_name}:{record.evidence_id}" if record.evidence_id else None
            )
            combined_evidence.append(replace(record, evidence_id=namespaced_id))
        for patch in result.patches:
            if patch.confidence == "low" and not apply_low_confidence:
                low_skipped.append(patch.path)
                continue
            existing = _get_path(candidate, patch.path)
            if (
                not _existing_is_unset(patch.path, existing, patch.value)
                and existing != patch.value
                and not allow_overwrite
            ):
                conflicts.append(
                    f"{patch.path}: existing value {existing!r} differs from proposed {patch.value!r}"
                )
                continue
            _set_path(candidate, patch.path, patch.value)
            applied.append(patch.path)

    pit_violations = validate_point_in_time(tuple(combined_evidence), analysis_date)
    errors.extend(pit_violations)
    evidence_ids = [record.evidence_id for record in combined_evidence if record.evidence_id]
    if len(evidence_ids) != len(set(evidence_ids)):
        errors.append("combined evidence ledger contains duplicate evidence_id values")
    missing = missing_for_screening(candidate)
    return IngestionReport(
        screening_candidate_draft=candidate,
        evidence=tuple(combined_evidence),
        applied_paths=tuple(applied),
        skipped_low_confidence=tuple(low_skipped),
        conflicts=tuple(conflicts),
        errors=tuple(dict.fromkeys(errors)),
        warnings=tuple(dict.fromkeys(warnings)),
        unresolved=tuple(dict.fromkeys(unresolved)),
        missing_for_screening=missing,
        ready_for_screening=(not errors and not conflicts and not missing),
    )


def agent_result_template(task_name: str, analysis_date: date) -> dict[str, Any]:
    if task_name not in TASK_PATCH_PATHS:
        raise ValueError(f"unknown task_name: {task_name}")
    return {
        "schema_version": SCHEMA_VERSION,
        "task_name": task_name,
        "analysis_date": analysis_date.isoformat(),
        "status": "unresolved",
        "evidence": [
            {
                "evidence_id": "e1",
                "claim": "",
                "source": "",
                "published_on": analysis_date.isoformat(),
                "event_on": None,
                "evidence_type": "fact",
                "locator": None,
                "notes": None,
            }
        ],
        "patches": [],
        "allowed_patch_paths": sorted(TASK_PATCH_PATHS[task_name]),
        "unresolved": [],
        "warnings": [],
    }


def ingestion_report_to_dict(report: IngestionReport) -> dict[str, Any]:
    payload = asdict(report)
    payload["evidence"] = [
        {
            **asdict(record),
            "published_on": record.published_on.isoformat(),
            "event_on": record.event_on.isoformat() if record.event_on else None,
        }
        for record in report.evidence
    ]
    return payload

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from .sec_instruments import FilingDocument, SecInstrumentPacket


@dataclass(frozen=True)
class MultimodalSourceTask:
    task_id: str
    analysis_date: date
    company_name: str
    source_accession: str
    document_name: str
    document_type: str
    source_url: str
    media_type: str
    reason: str
    objective: str
    questions: tuple[str, ...]
    guardrails: tuple[str, ...]
    output_contract: tuple[str, ...]


_VISUAL_PREFIX = "VISUAL_EXTRACTION_REQUIRED|"


def _warning_fields(warning: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    if not warning.startswith(_VISUAL_PREFIX):
        return fields
    for item in warning.split("|")[1:]:
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        fields[key.strip()] = value.strip()
    return fields


def _task_for_document(
    *,
    packet: SecInstrumentPacket,
    document: FilingDocument,
    media_type: str,
    reason: str,
    ordinal: int,
) -> MultimodalSourceTask:
    objective = (
        "Read the original visual source directly and determine what it changes about "
        "the investment/survival analysis. Do not transcribe or normalize the entire "
        "document unless a specific finding requires it."
    )
    questions = (
        "What material fact in this source could change Time to Death, refinancing risk, covenant risk, dilution, or existing-common recovery?",
        "What exact page/visual region supports each material finding?",
        "Does surrounding text, a footnote, definition, exception, or cross-reference change the apparent meaning of the table or number?",
        "What would be wrong if the most obvious visual reading were taken literally?",
        "Which minimal deterministic-engine fields, if any, must be patched for the finding to affect the model?",
    )
    guardrails = (
        f"Use only information visible in this source and information available by {packet.analysis_date.isoformat()}.",
        "Read the page/image directly with multimodal vision; do not treat OCR transcription as the research objective.",
        "Do not convert every row or cell into JSON. Preserve document context and reason about the source before extracting fields.",
        "Separate what the source visibly states from your interpretation and from the investment implication.",
        "For every material finding, cite the page number and a visual region/locator precise enough for a verifier to re-open the evidence.",
        "If a number or label is visually ambiguous, mark it unresolved rather than guessing.",
        "Only emit engine patches that are necessary for deterministic calculations; leave all other information as findings/evidence.",
        "A proposed patch is not authoritative until independently verified against the cited visual evidence.",
    )
    output_contract = (
        "findings[]: concise material findings, not a full-document transcription",
        "each finding: visible fact, evidence page/region, interpretation, investment implication, confidence/unresolved state",
        "disconfirming_or_ambiguous_evidence[]: anything that could invalidate the obvious reading",
        "engine_patches[]: optional minimal model fields only, each linked to finding/evidence IDs",
        "unresolved[]: material questions the visual source does not answer safely",
    )
    return MultimodalSourceTask(
        task_id=f"multimodal-source:{document.source_accession}:{document.sequence or 0}:{ordinal}",
        analysis_date=packet.analysis_date,
        company_name=packet.company_name,
        source_accession=document.source_accession,
        document_name=document.document,
        document_type=document.document_type,
        source_url=document.url,
        media_type=media_type,
        reason=reason,
        objective=objective,
        questions=questions,
        guardrails=guardrails,
        output_contract=output_contract,
    )


def build_multimodal_source_tasks(packet: SecInstrumentPacket) -> tuple[MultimodalSourceTask, ...]:
    """Build visual-reader tasks only for sources deterministic/native extraction deferred.

    Native-text PDF and HTML candidates remain on the deterministic path. This task
    layer is deliberately structure-on-demand: the model reads the original source,
    forms evidence-backed findings, and extracts only fields needed by the engine.
    """

    documents_by_name: dict[tuple[str, str], FilingDocument] = {
        (document.source_accession, document.document): document
        for document in packet.documents
    }
    tasks: list[MultimodalSourceTask] = []
    seen: set[tuple[str, str, str]] = set()
    for warning in packet.warnings:
        fields = _warning_fields(warning)
        if not fields:
            continue
        accession = fields.get("accession", "")
        document_name = fields.get("document", "")
        media_type = fields.get("media_type", "visual")
        reason = fields.get("reason", "deterministic extraction deferred")
        key = (accession, document_name, media_type)
        if key in seen:
            continue
        document = documents_by_name.get((accession, document_name))
        if document is None:
            continue
        seen.add(key)
        tasks.append(
            _task_for_document(
                packet=packet,
                document=document,
                media_type=media_type,
                reason=reason,
                ordinal=len(tasks) + 1,
            )
        )
    return tuple(tasks)


def multimodal_source_result_template(task: MultimodalSourceTask) -> dict[str, Any]:
    """Minimal research contract: findings first, structure only at engine boundary."""

    return {
        "schema_version": "1",
        "task_id": task.task_id,
        "analysis_date": task.analysis_date.isoformat(),
        "source": {
            "accession": task.source_accession,
            "document": task.document_name,
            "document_type": task.document_type,
            "url": task.source_url,
            "media_type": task.media_type,
        },
        "status": "unresolved",
        "findings": [],
        "disconfirming_or_ambiguous_evidence": [],
        "engine_patches": [],
        "unresolved": [],
        "warnings": [
            "Do not populate a full debt table merely because the page contains one; extract only facts needed to support a material finding or deterministic model patch."
        ],
    }


def multimodal_source_task_to_dict(task: MultimodalSourceTask) -> dict[str, Any]:
    payload = asdict(task)
    payload["analysis_date"] = task.analysis_date.isoformat()
    return payload


def multimodal_source_manifest(packet: SecInstrumentPacket) -> dict[str, Any]:
    tasks = build_multimodal_source_tasks(packet)
    return {
        "strategy": "read_source_first_structure_on_demand",
        "task_count": len(tasks),
        "tasks": [
            {
                "task": multimodal_source_task_to_dict(task),
                "result_template": multimodal_source_result_template(task),
            }
            for task in tasks
        ],
        "notes": [
            "Multimodal reading is for source understanding and evidence-backed findings, not bulk OCR normalization.",
            "Only minimal engine-facing patches should be structured after a material finding is established."
        ],
    }


def install_multimodal_source_manifest(module: Any) -> None:
    """Attach visual-source reading tasks to every serialized SEC instrument packet."""

    if getattr(module, "_multimodal_source_manifest_installed", False):
        return
    original = module.sec_instrument_packet_to_dict

    def serialize_with_multimodal_manifest(packet: SecInstrumentPacket) -> dict[str, Any]:
        payload = original(packet)
        payload["multimodal_source_reading"] = multimodal_source_manifest(packet)
        return payload

    serialize_with_multimodal_manifest.__name__ = "sec_instrument_packet_to_dict"
    module.sec_instrument_packet_to_dict = serialize_with_multimodal_manifest
    module._multimodal_source_manifest_installed = True

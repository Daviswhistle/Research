from datetime import date

import pytest

from distressed_equity.agent_results import agent_result_from_dict, validate_agent_result
from distressed_equity.multimodal_runner import (
    OpenAIResponsesMultimodalProvider,
    SourceAsset,
    SourceAssetBundle,
    run_multimodal_source_task,
)
from distressed_equity.multimodal_source_reader import (
    MultimodalSourceTask,
    multimodal_source_task_from_dict,
    multimodal_source_task_to_dict,
)
from distressed_equity.multimodal_source_results import (
    multimodal_result_fingerprint,
    multimodal_result_schema,
    multimodal_verification_template,
    promote_verified_multimodal_result,
    validate_multimodal_source_result,
)


def task() -> MultimodalSourceTask:
    return MultimodalSourceTask(
        task_id="multimodal-source:0001:2:1",
        analysis_date=date(2022, 12, 31),
        company_name="Test Co",
        source_accession="0001",
        document_name="scan.pdf",
        document_type="EX-4.1",
        source_url="https://www.sec.gov/Archives/example/scan.pdf",
        media_type="pdf",
        reason="native_text_layer_unavailable_or_too_sparse",
        objective="Read the original visual source.",
        questions=("What matters?",),
        guardrails=("Do not guess.",),
        output_contract=("findings[]",),
        source_filing_date=date(2022, 12, 1),
    )


def valid_result(item: MultimodalSourceTask) -> dict:
    return {
        "schema_version": "1",
        "task_id": item.task_id,
        "analysis_date": item.analysis_date.isoformat(),
        "source": {
            "accession": item.source_accession,
            "document": item.document_name,
            "document_type": item.document_type,
            "url": item.source_url,
            "media_type": item.media_type,
            "filing_date": item.source_filing_date.isoformat(),
        },
        "status": "complete",
        "findings": [
            {
                "finding_id": "f1",
                "visible_fact": "The term loan principal is $600 million.",
                "evidence": [
                    {
                        "evidence_id": "e1",
                        "page": 43,
                        "region": "debt table, Term Loan row",
                        "visible_text": "$600",
                    }
                ],
                "interpretation": "The obligation is senior debt.",
                "investment_implication": "It increases refinancing needs before recovery.",
                "confidence": "high",
                "unresolved": False,
            }
        ],
        "disconfirming_or_ambiguous_evidence": [],
        "engine_patches": [
            {
                "patch_id": "p1",
                "path": "debt_obligations",
                "value": [
                    {
                        "name": "Term Loan",
                        "amount": 600.0,
                        "due_month": 18,
                        "cash_payment_required": True,
                        "refinancing_required": True,
                        "secured": True,
                        "notes": ["Source page 43"],
                    }
                ],
                "finding_refs": ["f1"],
                "evidence_refs": ["e1"],
                "confidence": "high",
                "rationale": "Required for deterministic liquidity runway.",
            }
        ],
        "unresolved": [],
        "warnings": [],
    }


def test_task_round_trip_preserves_filing_date():
    item = task()
    restored = multimodal_source_task_from_dict(multimodal_source_task_to_dict(item))
    assert restored == item


def test_visual_result_requires_page_anchored_evidence_and_known_refs():
    item = task()
    raw = valid_result(item)
    validation = validate_multimodal_source_result(item, raw)
    assert validation.valid is True

    raw["findings"][0]["evidence"][0]["page"] = None
    raw["engine_patches"][0]["evidence_refs"] = ["missing"]
    invalid = validate_multimodal_source_result(item, raw)
    assert invalid.valid is False
    assert any("positive PDF page" in error for error in invalid.errors)
    assert any("unknown evidence" in error for error in invalid.errors)


def test_verification_fingerprint_binds_approval_to_exact_result():
    item = task()
    raw = valid_result(item)
    verification = multimodal_verification_template(item, raw)
    assert verification["result_fingerprint"] == multimodal_result_fingerprint(raw)
    assert verification["patch_reviews"][0]["status"] == "unverified"

    raw["engine_patches"][0]["value"][0]["amount"] = 700.0
    with pytest.raises(ValueError, match="fingerprint"):
        promote_verified_multimodal_result(item, raw, verification)


def test_verified_patch_promotes_into_existing_agent_ingestion_contract():
    item = task()
    raw = valid_result(item)
    verification = multimodal_verification_template(item, raw)
    verification["verifier"] = "independent-reviewer"
    verification["verified_on"] = "2026-09-15"
    verification["patch_reviews"][0].update(
        {
            "status": "verified",
            "evidence_reopened": True,
            "reviewer_note": "Re-opened page 43 and confirmed the row.",
        }
    )

    promoted = promote_verified_multimodal_result(item, raw, verification)
    assert promoted["task_name"] == "capital_stack_auditor"
    assert promoted["patches"][0]["path"] == "debt_obligations"
    assert promoted["evidence"][0]["published_on"] == "2022-12-01"
    assert promoted["patches"][0]["evidence_refs"] == [promoted["evidence"][0]["evidence_id"]]
    assert "page 43" in promoted["evidence"][0]["locator"]

    agent = agent_result_from_dict(promoted)
    validation = validate_agent_result(agent, expected_analysis_date=item.analysis_date)
    assert validation.valid is True


def test_unverified_patch_is_not_promoted():
    item = task()
    raw = valid_result(item)
    verification = multimodal_verification_template(item, raw)
    verification["verifier"] = "reviewer"
    verification["verified_on"] = "2026-09-15"

    promoted = promote_verified_multimodal_result(item, raw, verification)
    assert promoted["patches"] == []
    assert promoted["evidence"] == []
    assert any("not independently verified" in x for x in promoted["unresolved"])


class FakeProvider:
    provider_name = "fake"
    model_name = "fake-vision"

    def read(self, item, *, assets, prompt, result_schema):
        assert assets[0].mime_type == "application/pdf"
        assert "Engine patches are proposals" in prompt
        assert result_schema["properties"]["task_id"]["const"] == item.task_id
        return valid_result(item)


def test_runner_executes_provider_then_attaches_frozen_task_snapshot():
    item = task()

    def loader(_):
        return SourceAssetBundle(
            (
                SourceAsset(
                    name="scan.pdf",
                    url=item.source_url,
                    mime_type="application/pdf",
                    data=b"%PDF-test",
                    kind="file",
                ),
            )
        )

    result = run_multimodal_source_task(item, FakeProvider(), asset_loader=loader)
    assert result["task_snapshot"]["task_id"] == item.task_id
    assert result["run_metadata"]["provider"] == "fake"
    assert result["run_metadata"]["asset_count"] == 1


class FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeHTTPResponse(self.payload)


def test_openai_adapter_sends_pdf_as_high_detail_file_and_parses_json():
    item = task()
    raw = valid_result(item)
    session = FakeSession(
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": __import__("json").dumps(raw)}
                    ],
                }
            ],
        }
    )
    provider = OpenAIResponsesMultimodalProvider(
        api_key="test-key",
        model="gpt-5.6-sol",
        session=session,
    )
    asset = SourceAsset(
        name="scan.pdf",
        url=item.source_url,
        mime_type="application/pdf",
        data=b"%PDF-test",
        kind="file",
    )
    result = provider.read(
        item,
        assets=(asset,),
        prompt="read source",
        result_schema=multimodal_result_schema(item),
    )
    assert result["task_id"] == item.task_id
    body = session.calls[0][1]["json"]
    assert body["store"] is False
    file_items = [
        content
        for content in body["input"][0]["content"]
        if content["type"] == "input_file"
    ]
    assert len(file_items) == 1
    assert file_items[0]["detail"] == "high"
    assert body["text"]["format"]["type"] == "json_schema"

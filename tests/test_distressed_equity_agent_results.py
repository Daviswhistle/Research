from datetime import date

from distressed_equity.agent_results import (
    agent_result_from_dict,
    ingest_agent_results,
    validate_agent_result,
)


def packet():
    return {
        "evidence": [],
        "screening_draft": {
            "identity": {
                "ticker": "TEST",
                "company_name": "Test Co",
                "analysis_date": "2022-12-31",
            },
            "screening_candidate_draft": {
                "ticker": "TEST",
                "company_name": "Test Co",
                "analysis_date": "2022-12-31",
                "capital_structure": {
                    "current_price": None,
                    "current_shares": 100.0,
                    "net_debt": 50.0,
                    "other_senior_claims": 0.0,
                },
                "peak_market_cap": None,
                "current_enterprise_value": None,
                "ttm_revenue": None,
                "has_real_customers": None,
                "is_pre_revenue": False,
                "has_historical_operating_profit": True,
                "starting_liquidity": 100.0,
                "available_credit": 0.0,
                "asset_monetization": 0.0,
                "restricted_cash": 0.0,
                "monthly_cash_burn": None,
                "recovery_month": None,
                "debt_obligations": [],
                "covenants": [],
                "base_scenario": None,
                "reference_scenario": None,
                "downside_scenario": None,
                "critical_assumptions_complete": False,
            },
        },
    }


def result(task, patches, evidence=None, analysis_date="2022-12-31"):
    return agent_result_from_dict(
        {
            "schema_version": "1",
            "task_name": task,
            "analysis_date": analysis_date,
            "status": "complete",
            "evidence": evidence
            or [
                {
                    "evidence_id": "e1",
                    "claim": "primary-source fact",
                    "source": "SEC filing",
                    "published_on": "2022-12-01",
                    "event_on": "2022-09-30",
                    "evidence_type": "fact",
                    "locator": "https://www.sec.gov/example",
                }
            ],
            "patches": patches,
            "unresolved": [],
            "warnings": [],
        }
    )


def test_future_evidence_invalidates_result():
    item = result(
        "historical_market_reconstructor",
        [
            {
                "path": "capital_structure.current_price",
                "value": 3.0,
                "evidence_refs": ["future"],
                "confidence": "high",
            }
        ],
        evidence=[
            {
                "evidence_id": "future",
                "claim": "later price reconstruction",
                "source": "source",
                "published_on": "2023-01-02",
                "evidence_type": "fact",
            }
        ],
    )
    validation = validate_agent_result(item, expected_analysis_date=date(2022, 12, 31))
    assert validation.valid is False
    assert any("future information" in error for error in validation.errors)


def test_task_cannot_patch_fields_outside_its_authority():
    item = result(
        "historical_market_reconstructor",
        [
            {
                "path": "capital_structure.net_debt",
                "value": 500.0,
                "evidence_refs": ["e1"],
            }
        ],
    )
    validation = validate_agent_result(item, expected_analysis_date=date(2022, 12, 31))
    assert validation.valid is False
    assert any("not allowed" in error for error in validation.errors)


def test_low_confidence_patch_is_evidence_only_by_default():
    item = result(
        "historical_market_reconstructor",
        [
            {
                "path": "capital_structure.current_price",
                "value": 3.0,
                "evidence_refs": ["e1"],
                "confidence": "low",
            }
        ],
    )
    report = ingest_agent_results(packet(), [item])
    assert report.screening_candidate_draft["capital_structure"]["current_price"] is None
    assert report.skipped_low_confidence == ("capital_structure.current_price",)
    assert len(report.evidence) == 1


def test_conflict_does_not_silently_overwrite_sec_prefill():
    item = result(
        "capital_stack_extractor",
        [
            {
                "path": "capital_structure.current_shares",
                "value": 120.0,
                "evidence_refs": ["e1"],
                "confidence": "high",
            }
        ],
    )
    report = ingest_agent_results(packet(), [item])
    assert report.screening_candidate_draft["capital_structure"]["current_shares"] == 100.0
    assert report.conflicts
    assert report.ready_for_screening is False


def test_valid_results_can_close_required_screening_fields():
    market = result(
        "historical_market_reconstructor",
        [
            {
                "path": "capital_structure.current_price",
                "value": 2.0,
                "evidence_refs": ["e1"],
                "confidence": "high",
            }
        ],
    )
    business = result(
        "impairment_and_normalization_researcher",
        [
            {
                "path": "base_scenario",
                "value": {
                    "enterprise_value": 900.0,
                    "exit_net_debt": 100.0,
                    "critical_assumptions": ["customer losses stop"],
                },
                "evidence_refs": ["e1"],
                "confidence": "medium",
            },
            {
                "path": "downside_scenario",
                "value": {
                    "enterprise_value": 100.0,
                    "exit_net_debt": 100.0,
                    "critical_assumptions": [],
                },
                "evidence_refs": ["e1"],
                "confidence": "medium",
            },
        ],
    )
    report = ingest_agent_results(packet(), [market, business])
    assert report.errors == ()
    assert report.conflicts == ()
    assert report.missing_for_screening == ()
    assert report.ready_for_screening is True
    assert report.screening_candidate_draft["capital_structure"]["current_price"] == 2.0
    assert report.screening_candidate_draft["base_scenario"]["enterprise_value"] == 900.0


def test_patch_requires_evidence_reference():
    item = result(
        "historical_market_reconstructor",
        [
            {
                "path": "capital_structure.current_price",
                "value": 3.0,
                "evidence_refs": [],
            }
        ],
    )
    validation = validate_agent_result(item, expected_analysis_date=date(2022, 12, 31))
    assert validation.valid is False
    assert any("no evidence_refs" in error for error in validation.errors)

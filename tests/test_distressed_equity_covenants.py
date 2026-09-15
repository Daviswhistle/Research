from datetime import date

import pytest

from distressed_equity.covenant_cli import build_covenant_agent_result
from distressed_equity.covenants import (
    calculate_covenant_headroom,
    covenant_model_from_dict,
    covenant_risk_from_result,
)


def leverage_model(base_ebitda=100.0, drawn=50.0):
    return {
        "definition": {
            "name": "Maximum Net Leverage",
            "kind": "max_net_leverage",
            "threshold": 5.0,
            "test_month": 3,
            "springing": True,
            "springing_revolver_drawn_pct": 0.35,
            "cure_available": True,
            "cure_cost": 25.0,
            "source_refs": ["e1"],
        },
        "ebitda_bridge": {
            "base_ebitda": base_ebitda,
            "add_backs": {"permitted cost savings": 20.0},
            "deductions": {"non-permitted gain": 10.0},
        },
        "inputs": {
            "gross_debt": 600.0,
            "unrestricted_cash": 50.0,
            "cash_netting_cap": 75.0,
            "revolver_drawn": drawn,
            "revolver_commitment": 100.0,
        },
    }


def test_net_leverage_headroom_uses_contractual_ebitda_bridge():
    definition, bridge, inputs = covenant_model_from_dict(leverage_model())
    result = calculate_covenant_headroom(definition, bridge, inputs)
    assert result.applies is True
    assert result.covenant_ebitda == 110.0
    assert result.net_debt == 550.0
    assert result.actual == 5.0
    assert result.minimum_ebitda_to_comply == 110.0
    assert result.ebitda_cushion == 0.0
    assert result.breached is False


def test_springing_covenant_does_not_apply_below_usage_trigger():
    definition, bridge, inputs = covenant_model_from_dict(leverage_model(drawn=20.0))
    result = calculate_covenant_headroom(definition, bridge, inputs)
    assert result.applies is False
    assert result.breached is False
    assert result.actual is not None


def test_breach_translates_to_screening_covenant_risk():
    definition, bridge, inputs = covenant_model_from_dict(leverage_model(base_ebitda=70.0))
    result = calculate_covenant_headroom(definition, bridge, inputs)
    assert result.breached is True
    risk = covenant_risk_from_result(result)
    assert risk["breach_month_if_unremedied"] == 3
    assert risk["cure_available"] is True


def test_covenant_cli_emits_ingestible_capital_stack_patch():
    payload = {
        "analysis_date": "2022-12-31",
        "evidence": [
            {
                "evidence_id": "e1",
                "claim": "Credit agreement defines maximum net leverage at 5.0x",
                "source": "SEC credit agreement exhibit",
                "published_on": "2022-11-01",
                "event_on": "2022-11-01",
                "evidence_type": "fact",
                "locator": "https://www.sec.gov/example",
            }
        ],
        "models": [leverage_model()],
    }
    output = build_covenant_agent_result(payload)
    patch = output["agent_result"]["patches"][0]
    assert patch["path"] == "covenants"
    assert patch["evidence_refs"] == ["e1"]
    assert patch["value"][0]["name"] == "Maximum Net Leverage"


def test_covenant_model_rejects_unknown_evidence_reference():
    payload = {
        "analysis_date": date(2022, 12, 31).isoformat(),
        "evidence": [
            {
                "evidence_id": "other",
                "claim": "fact",
                "source": "source",
                "published_on": "2022-12-01",
                "evidence_type": "fact",
            }
        ],
        "models": [leverage_model()],
    }
    with pytest.raises(ValueError, match="unknown evidence"):
        build_covenant_agent_result(payload)

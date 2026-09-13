from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import (
    CapitalStructure,
    CaseInput,
    FinancingAction,
    LiquidityProfile,
    ProbabilityView,
    Scenario,
)


def _scenario(raw: dict[str, Any]) -> Scenario:
    financing_raw = raw.get("financing", {})
    return Scenario(
        name=raw["name"],
        enterprise_value=float(raw["enterprise_value"]),
        exit_net_debt=float(raw.get("exit_net_debt", 0.0)),
        exit_senior_claims=float(raw.get("exit_senior_claims", 0.0)),
        financing=FinancingAction(
            capital_raised=float(financing_raw.get("capital_raised", 0.0)),
            issue_price=(
                float(financing_raw["issue_price"])
                if financing_raw.get("issue_price") is not None
                else None
            ),
        ),
        critical_assumptions=tuple(raw.get("critical_assumptions", [])),
        notes=tuple(raw.get("notes", [])),
    )


def case_from_dict(raw: dict[str, Any]) -> CaseInput:
    capital = raw["capital_structure"]
    liquidity = raw["liquidity"]
    probability = raw["probability_view"]
    return CaseInput(
        ticker=raw["ticker"],
        company_name=raw["company_name"],
        capital_structure=CapitalStructure(
            current_price=float(capital["current_price"]),
            current_shares=float(capital["current_shares"]),
            net_debt=float(capital.get("net_debt", 0.0)),
            other_senior_claims=float(capital.get("other_senior_claims", 0.0)),
        ),
        liquidity=LiquidityProfile(
            starting_liquidity=float(liquidity["starting_liquidity"]),
            monthly_free_cash_flow=tuple(float(v) for v in liquidity["monthly_free_cash_flow"]),
            mandatory_payments={int(k): float(v) for k, v in liquidity.get("mandatory_payments", {}).items()},
            recovery_month=(int(liquidity["recovery_month"]) if liquidity.get("recovery_month") is not None else None),
        ),
        scenarios=tuple(_scenario(v) for v in raw["scenarios"]),
        probability_view=ProbabilityView(
            target_cagr=float(probability["target_cagr"]),
            horizon_years=float(probability["horizon_years"]),
            success_scenario=probability.get("success_scenario", "base"),
            downside_scenario=probability.get("downside_scenario", "distress"),
            reasonable_probability_low=(
                float(probability["reasonable_probability_low"])
                if probability.get("reasonable_probability_low") is not None
                else None
            ),
            reasonable_probability_high=(
                float(probability["reasonable_probability_high"])
                if probability.get("reasonable_probability_high") is not None
                else None
            ),
        ),
        capture_reference_scenario=raw.get("capture_reference_scenario", "weak"),
        metadata=dict(raw.get("metadata", {})),
    )


def load_case(path: str | Path) -> CaseInput:
    with Path(path).open("r", encoding="utf-8") as f:
        return case_from_dict(json.load(f))

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import (
    CapitalStructure,
    CaseInput,
    CovenantRisk,
    DebtObligation,
    FinancingAction,
    LiquidityProfile,
    ProbabilityView,
    Scenario,
)
from .screening import ScreeningCandidate


def _financing(raw: dict[str, Any] | None) -> FinancingAction:
    raw = raw or {}
    return FinancingAction(
        capital_raised=float(raw.get("capital_raised", 0.0)),
        issue_price=(float(raw["issue_price"]) if raw.get("issue_price") is not None else None),
    )


def _scenario(raw: dict[str, Any]) -> Scenario:
    return Scenario(
        name=raw["name"],
        enterprise_value=float(raw["enterprise_value"]),
        exit_net_debt=float(raw.get("exit_net_debt", 0.0)),
        exit_senior_claims=float(raw.get("exit_senior_claims", 0.0)),
        financing=_financing(raw.get("financing")),
        critical_assumptions=tuple(raw.get("critical_assumptions", [])),
        notes=tuple(raw.get("notes", [])),
    )


def _debt_obligation(raw: dict[str, Any]) -> DebtObligation:
    return DebtObligation(
        name=raw["name"],
        amount=float(raw["amount"]),
        due_month=int(raw["due_month"]),
        kind=raw.get("kind", "debt_maturity"),
        cash_payment_required=bool(raw.get("cash_payment_required", False)),
        refinancing_required=bool(raw.get("refinancing_required", True)),
        seniority=raw.get("seniority"),
        secured=raw.get("secured"),
        notes=tuple(raw.get("notes", [])),
    )


def _covenant(raw: dict[str, Any]) -> CovenantRisk:
    return CovenantRisk(
        name=raw["name"],
        breach_month_if_unremedied=(
            int(raw["breach_month_if_unremedied"])
            if raw.get("breach_month_if_unremedied") is not None
            else None
        ),
        cure_available=raw.get("cure_available"),
        cure_cost=float(raw.get("cure_cost", 0.0)),
        test_month=(int(raw["test_month"]) if raw.get("test_month") is not None else None),
        unresolved=bool(raw.get("unresolved", False)),
        notes=tuple(raw.get("notes", [])),
    )


def _capital_structure(raw: dict[str, Any]) -> CapitalStructure:
    return CapitalStructure(
        current_price=float(raw["current_price"]),
        current_shares=float(raw["current_shares"]),
        net_debt=float(raw.get("net_debt", 0.0)),
        other_senior_claims=float(raw.get("other_senior_claims", 0.0)),
    )


def case_from_dict(raw: dict[str, Any]) -> CaseInput:
    capital = raw["capital_structure"]
    liquidity = raw["liquidity"]
    probability = raw["probability_view"]
    return CaseInput(
        ticker=raw["ticker"],
        company_name=raw["company_name"],
        capital_structure=_capital_structure(capital),
        liquidity=LiquidityProfile(
            starting_liquidity=float(liquidity["starting_liquidity"]),
            monthly_free_cash_flow=tuple(float(v) for v in liquidity["monthly_free_cash_flow"]),
            mandatory_payments={int(k): float(v) for k, v in liquidity.get("mandatory_payments", {}).items()},
            recovery_month=(int(liquidity["recovery_month"]) if liquidity.get("recovery_month") is not None else None),
            available_credit=float(liquidity.get("available_credit", 0.0)),
            asset_monetization=float(liquidity.get("asset_monetization", 0.0)),
            restricted_cash=float(liquidity.get("restricted_cash", 0.0)),
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
        debt_obligations=tuple(_debt_obligation(v) for v in raw.get("debt_obligations", [])),
        covenants=tuple(_covenant(v) for v in raw.get("covenants", [])),
        analysis_date=raw.get("analysis_date"),
        metadata=dict(raw.get("metadata", {})),
    )


def screening_candidate_from_dict(raw: dict[str, Any]) -> ScreeningCandidate:
    return ScreeningCandidate(
        ticker=raw["ticker"],
        company_name=raw["company_name"],
        capital_structure=_capital_structure(raw["capital_structure"]),
        base_scenario=_scenario({"name": "base", **raw["base_scenario"]}),
        downside_scenario=_scenario({"name": "downside", **raw["downside_scenario"]}),
        reference_scenario=(
            _scenario({"name": "reference", **raw["reference_scenario"]})
            if raw.get("reference_scenario") is not None
            else None
        ),
        peak_market_cap=(float(raw["peak_market_cap"]) if raw.get("peak_market_cap") is not None else None),
        current_enterprise_value=(
            float(raw["current_enterprise_value"])
            if raw.get("current_enterprise_value") is not None
            else None
        ),
        ttm_revenue=(float(raw["ttm_revenue"]) if raw.get("ttm_revenue") is not None else None),
        has_real_customers=raw.get("has_real_customers"),
        is_pre_revenue=raw.get("is_pre_revenue"),
        has_historical_operating_profit=raw.get("has_historical_operating_profit"),
        starting_liquidity=(
            float(raw["starting_liquidity"]) if raw.get("starting_liquidity") is not None else None
        ),
        monthly_cash_burn=(
            float(raw["monthly_cash_burn"]) if raw.get("monthly_cash_burn") is not None else None
        ),
        recovery_month=(int(raw["recovery_month"]) if raw.get("recovery_month") is not None else None),
        available_credit=float(raw.get("available_credit", 0.0)),
        asset_monetization=float(raw.get("asset_monetization", 0.0)),
        restricted_cash=float(raw.get("restricted_cash", 0.0)),
        debt_obligations=tuple(_debt_obligation(v) for v in raw.get("debt_obligations", [])),
        covenants=tuple(_covenant(v) for v in raw.get("covenants", [])),
        critical_assumptions_complete=bool(raw.get("critical_assumptions_complete", False)),
        target_cagr=float(raw.get("target_cagr", 0.15)),
        horizon_years=float(raw.get("horizon_years", 3.0)),
        analysis_date=raw.get("analysis_date"),
        metadata=dict(raw.get("metadata", {})),
    )


def load_case(path: str | Path) -> CaseInput:
    with Path(path).open("r", encoding="utf-8") as f:
        return case_from_dict(json.load(f))


def load_screening_universe(path: str | Path) -> tuple[ScreeningCandidate, ...]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        if path.suffix.lower() == ".jsonl":
            rows = [json.loads(line) for line in f if line.strip()]
        else:
            payload = json.load(f)
            rows = payload if isinstance(payload, list) else payload["candidates"]
    return tuple(screening_candidate_from_dict(row) for row in rows)
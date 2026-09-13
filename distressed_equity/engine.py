from __future__ import annotations

from dataclasses import dataclass
import math

from .models import CaseInput, Scenario


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    total_equity_value: float
    existing_common_value: float
    diluted_share_count: float
    existing_holder_fraction: float
    value_per_current_share: float
    equity_multiple: float
    critical_assumption_count: int


@dataclass(frozen=True)
class CaseResult:
    time_to_death_months: int | None
    survives_to_recovery_window: bool | None
    scenarios: dict[str, ScenarioResult]
    required_probability: float | None
    target_multiple: float
    probability_margin_low: float | None
    probability_margin_high: float | None
    common_equity_capture_ratio: float | None
    base_assumption_efficiency: float | None
    verdict: str


def time_to_liquidity_exhaustion(case: CaseInput) -> int | None:
    liquidity = case.liquidity.starting_liquidity
    if liquidity < 0:
        return 0
    for month, fcf in enumerate(case.liquidity.monthly_free_cash_flow, start=1):
        liquidity += fcf
        liquidity -= case.liquidity.mandatory_payments.get(month, 0.0)
        if liquidity < 0:
            return month
    return None


def value_scenario(case: CaseInput, scenario: Scenario) -> ScenarioResult:
    new_shares = scenario.financing.new_shares()
    diluted_shares = case.capital_structure.current_shares + new_shares
    holder_fraction = (
        case.capital_structure.current_shares / diluted_shares if diluted_shares > 0 else 0.0
    )
    total_equity = max(
        scenario.enterprise_value - scenario.exit_net_debt - scenario.exit_senior_claims,
        0.0,
    )
    existing_common = total_equity * holder_fraction
    per_current_share = (
        existing_common / case.capital_structure.current_shares
        if case.capital_structure.current_shares > 0
        else 0.0
    )
    market_cap = case.capital_structure.current_market_cap
    multiple = existing_common / market_cap if market_cap > 0 else math.nan
    return ScenarioResult(
        name=scenario.name,
        total_equity_value=total_equity,
        existing_common_value=existing_common,
        diluted_share_count=diluted_shares,
        existing_holder_fraction=holder_fraction,
        value_per_current_share=per_current_share,
        equity_multiple=multiple,
        critical_assumption_count=len(scenario.critical_assumptions),
    )


def required_probability(
    success_multiple: float, downside_multiple: float, target_multiple: float
) -> float | None:
    spread = success_multiple - downside_multiple
    if spread <= 0:
        return None
    return max(0.0, (target_multiple - downside_multiple) / spread)


def common_equity_capture_ratio(
    base: ScenarioResult, reference: ScenarioResult, base_scenario: Scenario, reference_scenario: Scenario
) -> float | None:
    delta_ev = base_scenario.enterprise_value - reference_scenario.enterprise_value
    if abs(delta_ev) < 1e-12:
        return None
    return (base.existing_common_value - reference.existing_common_value) / delta_ev


def analyze_case(case: CaseInput) -> CaseResult:
    scenario_inputs = {scenario.name: scenario for scenario in case.scenarios}
    scenario_results = {name: value_scenario(case, scenario) for name, scenario in scenario_inputs.items()}

    pv = case.probability_view
    if pv.success_scenario not in scenario_results:
        raise ValueError(f"missing success scenario: {pv.success_scenario}")
    if pv.downside_scenario not in scenario_results:
        raise ValueError(f"missing downside scenario: {pv.downside_scenario}")

    target_multiple = (1.0 + pv.target_cagr) ** pv.horizon_years
    rp = required_probability(
        scenario_results[pv.success_scenario].equity_multiple,
        scenario_results[pv.downside_scenario].equity_multiple,
        target_multiple,
    )

    ttd = time_to_liquidity_exhaustion(case)
    if case.liquidity.recovery_month is None:
        survives_recovery = None
    elif case.liquidity.recovery_month > len(case.liquidity.monthly_free_cash_flow):
        survives_recovery = None
    elif ttd is None:
        survives_recovery = True
    else:
        survives_recovery = ttd > case.liquidity.recovery_month

    probability_margin_low = None
    probability_margin_high = None
    if rp is not None and pv.reasonable_probability_low is not None:
        probability_margin_low = pv.reasonable_probability_low - rp
    if rp is not None and pv.reasonable_probability_high is not None:
        probability_margin_high = pv.reasonable_probability_high - rp

    capture = None
    if pv.success_scenario in scenario_inputs and case.capture_reference_scenario in scenario_inputs:
        capture = common_equity_capture_ratio(
            scenario_results[pv.success_scenario],
            scenario_results[case.capture_reference_scenario],
            scenario_inputs[pv.success_scenario],
            scenario_inputs[case.capture_reference_scenario],
        )

    base_result = scenario_results[pv.success_scenario]
    base_efficiency = (
        base_result.equity_multiple / base_result.critical_assumption_count
        if base_result.critical_assumption_count > 0
        else None
    )

    if survives_recovery is False:
        verdict = "PASS: recovery window exceeds liquidity runway"
    elif rp is None:
        verdict = "PASS: success case does not improve enough over downside"
    elif rp > 1:
        verdict = "PASS: target return is unattainable under selected success/downside cases"
    elif pv.reasonable_probability_low is not None and pv.reasonable_probability_low > rp:
        verdict = "DEEP DIVE: conservative probability range clears required probability"
    elif pv.reasonable_probability_high is not None and pv.reasonable_probability_high < rp:
        verdict = "PASS: even optimistic probability range misses required probability"
    else:
        verdict = "REVIEW: probability edge is unresolved"

    return CaseResult(
        time_to_death_months=ttd,
        survives_to_recovery_window=survives_recovery,
        scenarios=scenario_results,
        required_probability=rp,
        target_multiple=target_multiple,
        probability_margin_low=probability_margin_low,
        probability_margin_high=probability_margin_high,
        common_equity_capture_ratio=capture,
        base_assumption_efficiency=base_efficiency,
        verdict=verdict,
    )

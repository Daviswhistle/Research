from __future__ import annotations

from dataclasses import dataclass, field

from .engine import common_equity_capture_ratio, required_probability, value_scenario_for_capital
from .models import CapitalStructure, CovenantRisk, DebtObligation, Scenario

PASS = "PASS"
FAIL = "FAIL"
RESEARCH = "RESEARCH"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ScreeningConfig:
    min_drawdown_from_peak: float = 0.60
    max_equity_share_of_ev: float = 0.35
    min_base_equity_multiple: float = 3.0
    max_critical_assumptions: int = 2
    runway_projection_months: int = 60


@dataclass(frozen=True)
class ScreeningCandidate:
    ticker: str
    company_name: str
    capital_structure: CapitalStructure
    base_scenario: Scenario
    downside_scenario: Scenario
    reference_scenario: Scenario | None = None
    peak_market_cap: float | None = None
    current_enterprise_value: float | None = None
    ttm_revenue: float | None = None
    has_real_customers: bool | None = None
    is_pre_revenue: bool | None = None
    has_historical_operating_profit: bool | None = None
    starting_liquidity: float | None = None
    monthly_cash_burn: float | None = None
    recovery_month: int | None = None
    available_credit: float = 0.0
    asset_monetization: float = 0.0
    restricted_cash: float = 0.0
    debt_obligations: tuple[DebtObligation, ...] = ()
    covenants: tuple[CovenantRisk, ...] = ()
    critical_assumptions_complete: bool = False
    target_cagr: float = 0.15
    horizon_years: float = 3.0
    analysis_date: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ScreeningResult:
    ticker: str
    company_name: str
    priority: str
    distress_gate: str
    real_business_gate: str
    survival_gate: str
    base_payoff_gate: str
    assumption_gate: str
    drawdown_from_peak: float | None
    equity_share_of_ev: float | None
    time_to_death_months: int | None
    runway_known: bool
    runway_projection_months: int
    base_equity_multiple: float
    downside_equity_multiple: float
    required_probability: float | None
    critical_assumption_count: int
    common_equity_capture_ratio: float | None
    pre_recovery_refinancing: tuple[str, ...]
    pre_recovery_covenants: tuple[str, ...]
    blockers: tuple[str, ...]
    research_questions: tuple[str, ...]


def _distress_gate(candidate: ScreeningCandidate, config: ScreeningConfig) -> tuple[str, float | None, float | None]:
    market_cap = candidate.capital_structure.current_market_cap
    drawdown = None
    if candidate.peak_market_cap is not None and candidate.peak_market_cap > 0:
        drawdown = 1.0 - market_cap / candidate.peak_market_cap

    equity_share = None
    if candidate.current_enterprise_value is not None and candidate.current_enterprise_value > 0:
        equity_share = market_cap / candidate.current_enterprise_value

    drawdown_pass = drawdown is not None and drawdown >= config.min_drawdown_from_peak
    thin_equity_pass = equity_share is not None and equity_share <= config.max_equity_share_of_ev
    if drawdown_pass or thin_equity_pass:
        return PASS, drawdown, equity_share
    if drawdown is not None and equity_share is not None:
        return FAIL, drawdown, equity_share
    return UNKNOWN, drawdown, equity_share


def _real_business_gate(candidate: ScreeningCandidate) -> str:
    if candidate.is_pre_revenue is True:
        return FAIL
    if candidate.ttm_revenue is not None and candidate.ttm_revenue <= 0:
        return FAIL
    if candidate.ttm_revenue is not None and candidate.ttm_revenue > 0 and candidate.has_real_customers is True:
        return PASS
    if candidate.ttm_revenue is not None and candidate.ttm_revenue > 0:
        return RESEARCH
    return UNKNOWN


def _screening_runway(candidate: ScreeningCandidate, config: ScreeningConfig) -> tuple[bool, int | None]:
    if candidate.starting_liquidity is None or candidate.monthly_cash_burn is None:
        return False, None
    liquidity = (
        candidate.starting_liquidity
        + candidate.available_credit
        + candidate.asset_monetization
        - candidate.restricted_cash
    )
    if liquidity < 0:
        return True, 0

    mandatory: dict[int, float] = {}
    for obligation in candidate.debt_obligations:
        if obligation.cash_payment_required:
            mandatory[obligation.due_month] = mandatory.get(obligation.due_month, 0.0) + obligation.amount

    horizon = max(config.runway_projection_months, candidate.recovery_month or 0, *(mandatory.keys() or [0]))
    for month in range(1, horizon + 1):
        liquidity -= max(candidate.monthly_cash_burn, 0.0)
        if candidate.monthly_cash_burn < 0:
            liquidity += -candidate.monthly_cash_burn
        liquidity -= mandatory.get(month, 0.0)
        if liquidity < 0:
            return True, month
    return True, None


def _pre_recovery_refinancing(candidate: ScreeningCandidate) -> tuple[str, ...]:
    if candidate.recovery_month is None:
        return ()
    return tuple(
        obligation.name
        for obligation in candidate.debt_obligations
        if obligation.refinancing_required
        and not obligation.cash_payment_required
        and obligation.due_month <= candidate.recovery_month
    )


def _pre_recovery_covenants(candidate: ScreeningCandidate) -> tuple[str, ...]:
    if candidate.recovery_month is None:
        return ()
    return tuple(
        covenant.name
        for covenant in candidate.covenants
        if covenant.breach_month_if_unremedied is not None
        and covenant.breach_month_if_unremedied <= candidate.recovery_month
    )


def _survival_gate(
    candidate: ScreeningCandidate,
    config: ScreeningConfig,
) -> tuple[str, bool, int | None, tuple[str, ...], tuple[str, ...]]:
    known, ttd = _screening_runway(candidate, config)
    refinancing = _pre_recovery_refinancing(candidate)
    covenants = _pre_recovery_covenants(candidate)
    if candidate.recovery_month is None or not known:
        return UNKNOWN, known, ttd, refinancing, covenants
    if ttd is not None and ttd <= candidate.recovery_month:
        return FAIL, known, ttd, refinancing, covenants
    if refinancing or covenants:
        return RESEARCH, known, ttd, refinancing, covenants
    return PASS, known, ttd, refinancing, covenants


def screen_candidate(candidate: ScreeningCandidate, config: ScreeningConfig | None = None) -> ScreeningResult:
    config = config or ScreeningConfig()
    distress_gate, drawdown, equity_share = _distress_gate(candidate, config)
    real_business_gate = _real_business_gate(candidate)
    survival_gate, known_runway, ttd, refinancing, covenants = _survival_gate(candidate, config)

    base = value_scenario_for_capital(candidate.capital_structure, candidate.base_scenario)
    downside = value_scenario_for_capital(candidate.capital_structure, candidate.downside_scenario)
    base_payoff_gate = PASS if base.equity_multiple >= config.min_base_equity_multiple else FAIL

    if candidate.critical_assumptions_complete:
        assumption_gate = (
            PASS
            if base.critical_assumption_count <= config.max_critical_assumptions
            else RESEARCH
        )
    else:
        assumption_gate = UNKNOWN

    target_multiple = (1.0 + candidate.target_cagr) ** candidate.horizon_years
    rp = required_probability(base.equity_multiple, downside.equity_multiple, target_multiple)

    capture = None
    if candidate.reference_scenario is not None:
        reference = value_scenario_for_capital(candidate.capital_structure, candidate.reference_scenario)
        capture = common_equity_capture_ratio(
            base,
            reference,
            candidate.base_scenario,
            candidate.reference_scenario,
        )

    blockers: list[str] = []
    questions: list[str] = []
    if distress_gate == FAIL:
        blockers.append("not distressed enough under configured price/capital-structure gates")
    elif distress_gate == UNKNOWN:
        questions.append("reconstruct point-in-time peak market cap or enterprise value")
    if real_business_gate == FAIL:
        blockers.append("not a proven revenue-generating real business")
    elif real_business_gate != PASS:
        questions.append("verify real customers, revenue quality, and historical economics")
    if survival_gate == FAIL:
        blockers.append("liquidity exhausts before the modeled recovery window")
    elif survival_gate != PASS:
        questions.append("resolve liquidity runway before recovery")
    if refinancing:
        questions.append("underwrite pre-recovery refinancing: " + ", ".join(refinancing))
    if covenants:
        questions.append("underwrite pre-recovery covenant path: " + ", ".join(covenants))
    if base_payoff_gate == FAIL:
        blockers.append(
            f"base existing-common payoff {base.equity_multiple:.2f}x is below {config.min_base_equity_multiple:.2f}x"
        )
    if assumption_gate == UNKNOWN:
        questions.append("enumerate independent critical assumptions before comparing candidates")
    elif assumption_gate == RESEARCH:
        questions.append("reduce or falsify the base case's assumption stack")
    if rp is None or rp > 1:
        blockers.append("selected base/downside paths cannot clear the target-return hurdle")
    if capture is None:
        questions.append("build a weak/reference scenario to estimate common-equity capture")

    hard_gates = (distress_gate, real_business_gate, survival_gate, base_payoff_gate)
    if FAIL in hard_gates or (rp is None or rp > 1):
        priority = "DROP"
    elif all(gate == PASS for gate in hard_gates) and assumption_gate == PASS:
        priority = "DEEP_DIVE_CANDIDATE"
    else:
        priority = "RESEARCH"

    return ScreeningResult(
        ticker=candidate.ticker,
        company_name=candidate.company_name,
        priority=priority,
        distress_gate=distress_gate,
        real_business_gate=real_business_gate,
        survival_gate=survival_gate,
        base_payoff_gate=base_payoff_gate,
        assumption_gate=assumption_gate,
        drawdown_from_peak=drawdown,
        equity_share_of_ev=equity_share,
        time_to_death_months=ttd,
        runway_known=known_runway,
        runway_projection_months=config.runway_projection_months,
        base_equity_multiple=base.equity_multiple,
        downside_equity_multiple=downside.equity_multiple,
        required_probability=rp,
        critical_assumption_count=base.critical_assumption_count,
        common_equity_capture_ratio=capture,
        pre_recovery_refinancing=refinancing,
        pre_recovery_covenants=covenants,
        blockers=tuple(blockers),
        research_questions=tuple(dict.fromkeys(questions)),
    )


def screen_universe(
    candidates: tuple[ScreeningCandidate, ...],
    config: ScreeningConfig | None = None,
) -> tuple[ScreeningResult, ...]:
    config = config or ScreeningConfig()
    rank = {"DEEP_DIVE_CANDIDATE": 0, "RESEARCH": 1, "DROP": 2}
    results = [screen_candidate(candidate, config) for candidate in candidates]
    results.sort(key=lambda item: (rank[item.priority], -item.base_equity_multiple, item.ticker))
    return tuple(results)

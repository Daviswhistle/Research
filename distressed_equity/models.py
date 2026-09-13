from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CapitalStructure:
    current_price: float
    current_shares: float
    net_debt: float = 0.0
    other_senior_claims: float = 0.0

    @property
    def current_market_cap(self) -> float:
        return self.current_price * self.current_shares


@dataclass(frozen=True)
class FinancingAction:
    capital_raised: float = 0.0
    issue_price: float | None = None

    def new_shares(self) -> float:
        if self.capital_raised <= 0:
            return 0.0
        if self.issue_price is None or self.issue_price <= 0:
            raise ValueError("issue_price must be positive when capital_raised is positive")
        return self.capital_raised / self.issue_price


@dataclass(frozen=True)
class Scenario:
    name: str
    enterprise_value: float
    exit_net_debt: float
    exit_senior_claims: float = 0.0
    financing: FinancingAction = field(default_factory=FinancingAction)
    critical_assumptions: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class LiquidityProfile:
    starting_liquidity: float
    monthly_free_cash_flow: tuple[float, ...]
    mandatory_payments: dict[int, float] = field(default_factory=dict)
    recovery_month: int | None = None


@dataclass(frozen=True)
class ProbabilityView:
    target_cagr: float
    horizon_years: float
    success_scenario: str = "base"
    downside_scenario: str = "distress"
    reasonable_probability_low: float | None = None
    reasonable_probability_high: float | None = None


@dataclass(frozen=True)
class CaseInput:
    ticker: str
    company_name: str
    capital_structure: CapitalStructure
    liquidity: LiquidityProfile
    scenarios: tuple[Scenario, ...]
    probability_view: ProbabilityView
    capture_reference_scenario: str = "weak"
    metadata: dict[str, Any] = field(default_factory=dict)

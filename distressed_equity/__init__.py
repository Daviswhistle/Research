"""Deterministic distressed-equity convexity research engine."""

from .agent_results import (
    AgentResult,
    IngestionReport,
    PatchProposal,
    agent_result_from_dict,
    agent_result_template,
    ingest_agent_results,
    validate_agent_result,
)
from .base_rates import BaseRateCase, BaseRateLibrary, BaseRateSummary
from .capital_stack_diff import CapitalStackDiffReport, diff_capital_stack_packet
from .covenants import (
    CovenantDefinition,
    CovenantEbitdaBridge,
    CovenantHeadroomResult,
    CovenantInputs,
    calculate_covenant_headroom,
    covenant_model_from_dict,
)
from .engine import (
    analyze_case,
    required_probability,
    time_to_liquidity_exhaustion,
    value_scenario,
)
from .io import case_from_dict, load_case, load_screening_universe, screening_candidate_from_dict
from .market import MarketSnapshot, PricePoint, SecurityIdentity
from .orchestration import ResearchBundle, build_research_bundle, ingest_research_bundle
from .replay import DistressScanConfig, MarketDistressSeed, run_historical_replay
from .screening import ScreeningConfig, screen_candidate, screen_universe

__all__ = [
    "AgentResult",
    "analyze_case",
    "agent_result_from_dict",
    "agent_result_template",
    "BaseRateCase",
    "BaseRateLibrary",
    "BaseRateSummary",
    "build_research_bundle",
    "calculate_covenant_headroom",
    "CapitalStackDiffReport",
    "case_from_dict",
    "CovenantDefinition",
    "CovenantEbitdaBridge",
    "CovenantHeadroomResult",
    "CovenantInputs",
    "covenant_model_from_dict",
    "diff_capital_stack_packet",
    "DistressScanConfig",
    "ingest_agent_results",
    "ingest_research_bundle",
    "IngestionReport",
    "load_case",
    "load_screening_universe",
    "MarketDistressSeed",
    "MarketSnapshot",
    "PatchProposal",
    "PricePoint",
    "required_probability",
    "ResearchBundle",
    "run_historical_replay",
    "screen_candidate",
    "screen_universe",
    "screening_candidate_from_dict",
    "ScreeningConfig",
    "SecurityIdentity",
    "time_to_liquidity_exhaustion",
    "validate_agent_result",
    "value_scenario",
]

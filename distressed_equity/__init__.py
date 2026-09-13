"""Deterministic distressed-equity convexity research engine."""

from .base_rates import BaseRateCase, BaseRateLibrary, BaseRateSummary
from .engine import (
    analyze_case,
    required_probability,
    time_to_liquidity_exhaustion,
    value_scenario,
)
from .io import case_from_dict, load_case, load_screening_universe, screening_candidate_from_dict
from .market import MarketSnapshot, PricePoint, SecurityIdentity
from .replay import DistressScanConfig, MarketDistressSeed, run_historical_replay
from .screening import ScreeningConfig, screen_candidate, screen_universe

__all__ = [
    "analyze_case",
    "BaseRateCase",
    "BaseRateLibrary",
    "BaseRateSummary",
    "case_from_dict",
    "DistressScanConfig",
    "load_case",
    "load_screening_universe",
    "MarketDistressSeed",
    "MarketSnapshot",
    "PricePoint",
    "required_probability",
    "run_historical_replay",
    "screen_candidate",
    "screen_universe",
    "screening_candidate_from_dict",
    "ScreeningConfig",
    "SecurityIdentity",
    "time_to_liquidity_exhaustion",
    "value_scenario",
]

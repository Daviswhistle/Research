"""Deterministic distressed-equity convexity research engine."""

from .engine import (
    analyze_case,
    required_probability,
    time_to_liquidity_exhaustion,
    value_scenario,
)
from .io import case_from_dict, load_case, load_screening_universe, screening_candidate_from_dict
from .screening import ScreeningConfig, screen_candidate, screen_universe

__all__ = [
    "analyze_case",
    "case_from_dict",
    "load_case",
    "load_screening_universe",
    "required_probability",
    "screen_candidate",
    "screen_universe",
    "screening_candidate_from_dict",
    "ScreeningConfig",
    "time_to_liquidity_exhaustion",
    "value_scenario",
]

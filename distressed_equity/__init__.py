"""Deterministic distressed-equity convexity research engine."""

from .engine import analyze_case, required_probability, time_to_liquidity_exhaustion, value_scenario
from .io import case_from_dict, load_case

__all__ = [
    "analyze_case",
    "case_from_dict",
    "load_case",
    "required_probability",
    "time_to_liquidity_exhaustion",
    "value_scenario",
]

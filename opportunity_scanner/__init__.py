"""Opportunity scanner for ranking cheap, falsifiable profit experiments."""

from .filters import MinimumEvidenceGate, ProhibitedGate, ValidationBudgetGate
from .filters import PermissionGate as PermissionGate  # deprecated alias
from .scoring import OpportunityAxisScorer
from .selector import ParetoLayerSelector
from .source import JsonlOpportunitySource

__all__ = [
    "JsonlOpportunitySource",
    "MinimumEvidenceGate",
    "ProhibitedGate",
    "PermissionGate",
    "ValidationBudgetGate",
    "OpportunityAxisScorer",
    "ParetoLayerSelector",
]

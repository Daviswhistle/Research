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
from .covenant_sensitivity import (
    CovenantSensitivityCase,
    CovenantSensitivityReport,
    calculate_covenant_addback_sensitivity,
)
from .covenants import (
    CovenantDefinition,
    CovenantEbitdaBridge,
    CovenantHeadroomResult,
    CovenantInputs,
    calculate_covenant_headroom,
    covenant_model_from_dict,
)
from .debt_instruments import (
    DebtInstrumentLedger,
    DebtInstrumentSnapshot,
    DebtInstrumentVersion,
    build_debt_instrument_ledger,
    debt_snapshots_from_dict,
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
from .sec_instruments import (
    DebtInstrumentSourceCandidate,
    FilingDocument,
    InstrumentFieldProposal,
    InstrumentSourceSpan,
    SecInstrumentPacket,
    build_sec_instrument_packet,
    instrument_verification_template,
)
from .source_graph import (
    ExpandedInstrumentPacket,
    SourceDocumentGraph,
    SourceGraphEdge,
    SourceGraphNode,
    SourceReference,
    expand_instrument_packet_with_references,
    extract_source_references,
    resolve_source_document_graph,
)

__all__ = [
    "AgentResult",
    "analyze_case",
    "agent_result_from_dict",
    "agent_result_template",
    "BaseRateCase",
    "BaseRateLibrary",
    "BaseRateSummary",
    "build_debt_instrument_ledger",
    "build_research_bundle",
    "build_sec_instrument_packet",
    "calculate_covenant_addback_sensitivity",
    "calculate_covenant_headroom",
    "CapitalStackDiffReport",
    "case_from_dict",
    "CovenantDefinition",
    "CovenantEbitdaBridge",
    "CovenantHeadroomResult",
    "CovenantInputs",
    "CovenantSensitivityCase",
    "CovenantSensitivityReport",
    "covenant_model_from_dict",
    "DebtInstrumentLedger",
    "DebtInstrumentSnapshot",
    "DebtInstrumentSourceCandidate",
    "DebtInstrumentVersion",
    "debt_snapshots_from_dict",
    "diff_capital_stack_packet",
    "DistressScanConfig",
    "ExpandedInstrumentPacket",
    "expand_instrument_packet_with_references",
    "extract_source_references",
    "FilingDocument",
    "ingest_agent_results",
    "ingest_research_bundle",
    "IngestionReport",
    "InstrumentFieldProposal",
    "InstrumentSourceSpan",
    "instrument_verification_template",
    "load_case",
    "load_screening_universe",
    "MarketDistressSeed",
    "MarketSnapshot",
    "PatchProposal",
    "PricePoint",
    "required_probability",
    "ResearchBundle",
    "resolve_source_document_graph",
    "run_historical_replay",
    "screen_candidate",
    "screen_universe",
    "screening_candidate_from_dict",
    "ScreeningConfig",
    "SecInstrumentPacket",
    "SecurityIdentity",
    "SourceDocumentGraph",
    "SourceGraphEdge",
    "SourceGraphNode",
    "SourceReference",
    "time_to_liquidity_exhaustion",
    "validate_agent_result",
    "value_scenario",
]

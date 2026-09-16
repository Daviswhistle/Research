"""Deterministic distressed-equity convexity research engine."""

# Install structured table / native-PDF extraction before downstream modules bind
# sec_instruments helpers through `from ... import ...`.
from . import sec_instruments as _sec_instruments
from .structured_debt_extraction import install_structured_debt_extraction as _install_structured_debt_extraction
from .native_pdf_debt_extraction import install_native_pdf_debt_extraction as _install_native_pdf_debt_extraction
from .document_debt_extraction import install_document_aware_extraction as _install_document_aware_extraction
from .complex_table_single_column_hardening import install_single_instrument_transposed_table_hardening as _install_single_instrument_transposed_table_hardening
from .multimodal_source_reader import install_multimodal_source_manifest as _install_multimodal_source_manifest

_install_structured_debt_extraction(_sec_instruments)
_install_native_pdf_debt_extraction(_sec_instruments)
_install_document_aware_extraction(_sec_instruments)
_install_single_instrument_transposed_table_hardening()
_install_multimodal_source_manifest(_sec_instruments)

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
from .contract_graph import enhance_expanded_packet_with_contract_identity
from .contract_identity import (
    ContractIdentity,
    ContractSearchResult,
    extract_contract_identities,
    reverse_search_contract_identity,
)
from .contract_parties import (
    ContractParty,
    contract_parties_compatible,
    extract_contract_parties,
    normalize_party_name,
    party_fingerprint,
)
from .cross_cik import (
    CrossCikExpandedPacket,
    CrossCikGraph,
    CrossCikReference,
    CrossCikResolution,
    LegalEntityNode,
    LegalEntityRelation,
    LegalEntityRole,
    cross_cik_graph_to_dict,
    expand_packet_with_cross_cik,
    extract_cross_cik_references,
    extract_explicit_legal_entity_evidence,
    resolve_cross_cik_graph,
)
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
from .foreign_contract_graph import (
    ForeignContractExpansion,
    ForeignContractGraph,
    expand_foreign_contract_identities,
    foreign_contract_expansion_to_dict,
)
from .io import case_from_dict, load_case, load_screening_universe, screening_candidate_from_dict
from .legal_name_alias import (
    LegalNameAliasGraph,
    LegalNameRecord,
    LegalNameTransition,
    alias_groups_from_graphs,
    build_legal_name_alias_graph,
    build_legal_name_alias_graph_from_submissions,
    build_legal_name_alias_graphs,
    legal_name_alias_graph_to_dict,
)
from .legal_name_header import (
    CompleteSubmissionFormerName,
    CompleteSubmissionNameEvidence,
    complete_submission_text_url,
    extract_complete_submission_name_evidence,
    fetch_complete_submission_name_evidence,
)
from .legal_name_reconcile import (
    LegalNameSourceComparison,
    ReconciledLegalNameAliasGraph,
    reconcile_legal_name_sources,
)
from .market import MarketSnapshot, PricePoint, SecurityIdentity
from .multimodal_source_reader import (
    MultimodalSourceTask,
    build_multimodal_source_tasks,
    multimodal_source_manifest,
    multimodal_source_result_template,
    multimodal_source_task_to_dict,
)
from .named_entity_closure import (
    NamedEntityCrossCikClosure,
    NamedEntityFixedPointClosure,
    close_named_entity_cross_cik,
    close_named_entity_fixed_point,
    merge_named_entity_graphs,
)
from .named_entity_contracts import (
    ExternalEntityCandidate,
    NamedEntityContractExpansion,
    NamedEntityContractGraph,
    NamedEntityContractResolution,
    named_entity_contract_graph_to_dict,
    resolve_named_entity_contracts,
)
from .orchestration import ResearchBundle, build_research_bundle, ingest_research_bundle
from .replay import DistressScanConfig, MarketDistressSeed, run_historical_replay
from .screening import ScreeningConfig, screen_candidate, screen_universe
from .sec_cik_lookup import (
    SEC_CIK_LOOKUP_URL,
    SecCikLookupMatch,
    fetch_cik_lookup_matches,
    parse_cik_lookup_matches,
)
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
    "alias_groups_from_graphs",
    "BaseRateCase",
    "BaseRateLibrary",
    "BaseRateSummary",
    "build_debt_instrument_ledger",
    "build_legal_name_alias_graph",
    "build_legal_name_alias_graph_from_submissions",
    "build_legal_name_alias_graphs",
    "build_multimodal_source_tasks",
    "build_research_bundle",
    "build_sec_instrument_packet",
    "calculate_covenant_addback_sensitivity",
    "calculate_covenant_headroom",
    "CapitalStackDiffReport",
    "case_from_dict",
    "close_named_entity_cross_cik",
    "close_named_entity_fixed_point",
    "CompleteSubmissionFormerName",
    "CompleteSubmissionNameEvidence",
    "complete_submission_text_url",
    "ContractIdentity",
    "ContractParty",
    "ContractSearchResult",
    "contract_parties_compatible",
    "CovenantDefinition",
    "CovenantEbitdaBridge",
    "CovenantHeadroomResult",
    "CovenantInputs",
    "CovenantSensitivityCase",
    "CovenantSensitivityReport",
    "covenant_model_from_dict",
    "CrossCikExpandedPacket",
    "CrossCikGraph",
    "CrossCikReference",
    "CrossCikResolution",
    "cross_cik_graph_to_dict",
    "DebtInstrumentLedger",
    "DebtInstrumentSnapshot",
    "DebtInstrumentSourceCandidate",
    "DebtInstrumentVersion",
    "debt_snapshots_from_dict",
    "diff_capital_stack_packet",
    "DistressScanConfig",
    "enhance_expanded_packet_with_contract_identity",
    "ExpandedInstrumentPacket",
    "expand_foreign_contract_identities",
    "expand_instrument_packet_with_references",
    "expand_packet_with_cross_cik",
    "ExternalEntityCandidate",
    "extract_complete_submission_name_evidence",
    "extract_contract_identities",
    "extract_contract_parties",
    "extract_cross_cik_references",
    "extract_explicit_legal_entity_evidence",
    "extract_source_references",
    "fetch_cik_lookup_matches",
    "fetch_complete_submission_name_evidence",
    "FilingDocument",
    "ForeignContractExpansion",
    "ForeignContractGraph",
    "foreign_contract_expansion_to_dict",
    "ingest_agent_results",
    "ingest_research_bundle",
    "IngestionReport",
    "InstrumentFieldProposal",
    "InstrumentSourceSpan",
    "instrument_verification_template",
    "LegalEntityNode",
    "LegalEntityRelation",
    "LegalEntityRole",
    "LegalNameAliasGraph",
    "LegalNameRecord",
    "LegalNameSourceComparison",
    "LegalNameTransition",
    "legal_name_alias_graph_to_dict",
    "load_case",
    "load_screening_universe",
    "MarketDistressSeed",
    "MarketSnapshot",
    "merge_named_entity_graphs",
    "MultimodalSourceTask",
    "multimodal_source_manifest",
    "multimodal_source_result_template",
    "multimodal_source_task_to_dict",
    "NamedEntityContractExpansion",
    "NamedEntityContractGraph",
    "NamedEntityContractResolution",
    "NamedEntityCrossCikClosure",
    "NamedEntityFixedPointClosure",
    "named_entity_contract_graph_to_dict",
    "normalize_party_name",
    "parse_cik_lookup_matches",
    "party_fingerprint",
    "PatchProposal",
    "PricePoint",
    "ReconciledLegalNameAliasGraph",
    "reconcile_legal_name_sources",
    "required_probability",
    "ResearchBundle",
    "resolve_cross_cik_graph",
    "resolve_named_entity_contracts",
    "resolve_source_document_graph",
    "reverse_search_contract_identity",
    "run_historical_replay",
    "screen_candidate",
    "screen_universe",
    "screening_candidate_from_dict",
    "ScreeningConfig",
    "SEC_CIK_LOOKUP_URL",
    "SecCikLookupMatch",
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

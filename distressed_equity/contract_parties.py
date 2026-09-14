from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


@dataclass(frozen=True, order=True)
class ContractParty:
    """Role-aware legal-entity fingerprint used only for conservative contract matching."""

    role: str
    name: str
    normalized_name: str


_LEGAL_SUFFIX = (
    r"(?:L\.?L\.?C\.?|L\.?P\.?|Inc(?:orporated)?\.?|Corp(?:oration)?\.?|"
    r"Ltd\.?|Limited|P\.?L\.?C\.?|N\.?A\.?)"
)
_PARTY_NAME = rf"(?P<name>[A-Z][A-Za-z0-9&'’.,()\-/ ]{{1,120}}?{_LEGAL_SUFFIX})"
_ROLE = r"(?P<role>co[- ]borrower|borrower|issuer|parent\s+guarantor|guarantor)"

# Common agreement preamble forms:
#   ABC Borrower LLC, as Borrower
#   ABC Borrower LLC, a Delaware limited liability company, as Borrower
#   ABC Borrower LLC (the "Borrower")
#   Borrower: ABC Borrower LLC
_ROLE_AFTER_NAME_RE = re.compile(
    rf"{_PARTY_NAME}(?:\s*,[^.;]{{0,90}}?)?\s*,?\s+as\s+(?:the\s+)?{_ROLE}\b",
    re.IGNORECASE,
)
_ALIAS_AFTER_NAME_RE = re.compile(
    rf"{_PARTY_NAME}\s*\(\s*(?:the\s+)?[\"'“”]?{_ROLE}[\"'“”]?\s*\)",
    re.IGNORECASE,
)
_ROLE_BEFORE_NAME_RE = re.compile(
    rf"\b{_ROLE}\s*[:\-]\s*{_PARTY_NAME}",
    re.IGNORECASE,
)

_GENERIC_PARTY_NAMES = {
    "the company",
    "company",
    "the borrower",
    "borrower",
    "the issuer",
    "issuer",
    "the guarantor",
    "guarantor",
}


def normalize_party_role(role: str) -> str:
    cleaned = re.sub(r"\s+", " ", role.lower().replace("-", " ")).strip()
    if cleaned in {"co borrower", "borrower"}:
        return "borrower"
    if cleaned == "issuer":
        return "issuer"
    if cleaned in {"parent guarantor", "guarantor"}:
        return "guarantor"
    return cleaned.replace(" ", "_")


def normalize_party_name(name: str) -> str:
    """Normalize punctuation and common legal suffix spelling without fuzzy matching."""

    cleaned = name.lower().replace("&", " and ")
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", cleaned)
    cleaned = re.sub(r"\bl\s+l\s+c\b", "llc", cleaned)
    cleaned = re.sub(r"\bl\s+p\b", "lp", cleaned)
    cleaned = re.sub(r"\bp\s+l\s+c\b", "plc", cleaned)
    cleaned = re.sub(r"\bn\s+a\b", "na", cleaned)
    cleaned = re.sub(r"\bincorporated\b", "inc", cleaned)
    cleaned = re.sub(r"\bcorporation\b", "corp", cleaned)
    cleaned = re.sub(r"\blimited\b", "ltd", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _party_from_match(match: re.Match[str]) -> ContractParty | None:
    name = re.sub(r"\s+", " ", match.group("name")).strip(" ,")
    normalized = normalize_party_name(name)
    if not normalized or normalized in _GENERIC_PARTY_NAMES:
        return None
    return ContractParty(
        role=normalize_party_role(match.group("role")),
        name=name,
        normalized_name=normalized,
    )


def extract_contract_parties(text: str) -> tuple[ContractParty, ...]:
    """Extract only explicitly role-labelled borrower/issuer/guarantor legal entities."""

    parties: dict[tuple[str, str], ContractParty] = {}
    for pattern in (_ROLE_AFTER_NAME_RE, _ALIAS_AFTER_NAME_RE, _ROLE_BEFORE_NAME_RE):
        for match in pattern.finditer(text):
            party = _party_from_match(match)
            if party is None:
                continue
            parties[(party.role, party.normalized_name)] = party
    return tuple(sorted(parties.values(), key=lambda item: (item.role, item.normalized_name)))


def party_fingerprint(parties: Iterable[ContractParty]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted({(item.role, item.normalized_name) for item in parties}))


def contract_parties_compatible(
    target_parties: Iterable[ContractParty],
    candidate_parties: Iterable[ContractParty],
) -> bool:
    """Hard-filter party compatibility for locator-less fallback matching.

    If the source contract mention names a borrower/issuer, at least one same-role
    legal entity must be present in the candidate exhibit. A source that only
    identifies guarantors requires a guarantor overlap. Guarantors are also used
    as a disambiguator when both sides explicitly expose them.

    When the source has no explicit party fingerprint, this layer is neutral and
    the existing contract-kind/date rules decide the match.
    """

    target = tuple(target_parties)
    candidate = tuple(candidate_parties)
    if not target:
        return True

    target_primary = {(p.role, p.normalized_name) for p in target if p.role in {"borrower", "issuer"}}
    candidate_primary = {(p.role, p.normalized_name) for p in candidate if p.role in {"borrower", "issuer"}}
    target_guarantors = {p.normalized_name for p in target if p.role == "guarantor"}
    candidate_guarantors = {p.normalized_name for p in candidate if p.role == "guarantor"}

    if target_primary:
        if not candidate_primary or target_primary.isdisjoint(candidate_primary):
            return False
        if target_guarantors and candidate_guarantors and target_guarantors.isdisjoint(candidate_guarantors):
            return False
        return True

    if target_guarantors:
        return bool(candidate_guarantors and not target_guarantors.isdisjoint(candidate_guarantors))

    return True

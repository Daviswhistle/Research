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
_CONTRACT_TERMS = (
    "credit agreement",
    "loan agreement",
    "indenture",
    "note purchase agreement",
    "security agreement",
    "guaranty agreement",
    "guarantee agreement",
)

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


def _trim_captured_party_name(raw: str) -> str:
    name = re.sub(r"\s+", " ", raw).strip(" ,")
    for separator in (". ", "; "):
        if separator in name:
            name = name.rsplit(separator, 1)[-1].strip(" ,")
    lower = name.lower()
    for marker in (" among ", " between ", " by ", " with "):
        position = lower.rfind(marker)
        if position >= 0:
            name = name[position + len(marker) :].strip(" ,")
            lower = name.lower()
    name = re.sub(r"^(?:and|among|between|by|with)\s+", "", name, flags=re.IGNORECASE).strip(" ,")
    return name


def _cross_sentence_contract_capture(raw: str) -> bool:
    """Reject a party regex that crossed a prior contract sentence boundary."""

    if ". " not in raw:
        return False
    prefix = raw.rsplit(". ", 1)[0].lower()
    return any(term in prefix for term in _CONTRACT_TERMS)


def _party_from_match(match: re.Match[str]) -> ContractParty | None:
    raw_name = match.group("name")
    if _cross_sentence_contract_capture(raw_name):
        return None
    name = _trim_captured_party_name(raw_name)
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


def _names_equivalent(
    left: str,
    right: str,
    alias_groups: Iterable[Iterable[str]],
) -> bool:
    if left == right:
        return True
    for group in alias_groups:
        normalized = set(group)
        if left in normalized and right in normalized:
            return True
    return False


def _role_sets_overlap(
    left: set[tuple[str, str]],
    right: set[tuple[str, str]],
    alias_groups: Iterable[Iterable[str]],
) -> bool:
    for left_role, left_name in left:
        for right_role, right_name in right:
            if left_role == right_role and _names_equivalent(left_name, right_name, alias_groups):
                return True
    return False


def _name_sets_overlap(
    left: set[str],
    right: set[str],
    alias_groups: Iterable[Iterable[str]],
) -> bool:
    return any(_names_equivalent(a, b, alias_groups) for a in left for b in right)


def contract_parties_compatible(
    target_parties: Iterable[ContractParty],
    candidate_parties: Iterable[ContractParty],
    *,
    alias_groups: Iterable[Iterable[str]] = (),
) -> bool:
    """Hard-filter party compatibility for locator-less fallback matching.

    Alias groups are accepted only when supplied by a deterministic external
    identity source such as the SEC CIK former-name graph. This function never
    creates fuzzy aliases by itself.
    """

    target = tuple(target_parties)
    candidate = tuple(candidate_parties)
    if not target:
        return True

    alias_groups = tuple(tuple(group) for group in alias_groups)
    target_primary = {(p.role, p.normalized_name) for p in target if p.role in {"borrower", "issuer"}}
    candidate_primary = {(p.role, p.normalized_name) for p in candidate if p.role in {"borrower", "issuer"}}
    target_guarantors = {p.normalized_name for p in target if p.role == "guarantor"}
    candidate_guarantors = {p.normalized_name for p in candidate if p.role == "guarantor"}

    if target_primary:
        if not candidate_primary or not _role_sets_overlap(target_primary, candidate_primary, alias_groups):
            return False
        if target_guarantors and candidate_guarantors and not _name_sets_overlap(
            target_guarantors, candidate_guarantors, alias_groups
        ):
            return False
        return True

    if target_guarantors:
        return bool(
            candidate_guarantors
            and _name_sets_overlap(target_guarantors, candidate_guarantors, alias_groups)
        )

    return True

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import re
from typing import Iterable

from .sec import SecClient, SecFiling
from .sec_debt import html_to_text
from .sec_instruments import FilingDocument, _get_text, filing_index_url, parse_filing_documents

_MONTHS = {
    name.lower(): number
    for number, name in enumerate(
        (
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ),
        1,
    )
}

_CONTRACT_TITLE_RE = re.compile(
    r"\b(?P<title>(?:(?:amended\s+and\s+restated|amended|restated|first\s+lien|second\s+lien|"
    r"senior|secured|unsecured|revolving|term|asset[- ]based)\s+){0,5}"
    r"(?:credit\s+agreement|loan\s+agreement|term\s+loan\s+agreement|"
    r"revolving\s+credit\s+agreement|indenture|note\s+purchase\s+agreement|"
    r"security\s+agreement|guaranty\s+agreement|guarantee\s+agreement))\b",
    re.IGNORECASE,
)
_CONTRACT_DATE_RE = re.compile(
    r"(?:,?\s*(?:dated|dated\s+as\s+of|effective\s+as\s+of|entered\s+into\s+as\s+of)\s+)"
    r"(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"(?P<day>\d{1,2}),\s+(?P<year>20\d{2})\b",
    re.IGNORECASE,
)
_NUMERIC_CONTRACT_DATE_RE = re.compile(
    r"(?:,?\s*(?:dated|dated\s+as\s+of|effective\s+as\s+of|entered\s+into\s+as\s+of)\s+)"
    r"(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>20\d{2})\b",
    re.IGNORECASE,
)
_MODIFICATION_TERMS = ("amendment", "waiver", "supplement", "joinder", "consent")


@dataclass(frozen=True)
class ContractIdentity:
    title: str
    kind: str
    execution_date: date


@dataclass(frozen=True)
class ContractSearchResult:
    identity: ContractIdentity
    candidates: tuple[FilingDocument, ...]
    searched_filings: tuple[str, ...]
    warnings: tuple[str, ...]


def _canonical_kind(title: str) -> str:
    lower = re.sub(r"\s+", " ", title.lower()).strip()
    if "indenture" in lower:
        return "indenture"
    if "note purchase" in lower:
        return "note_purchase_agreement"
    if "security agreement" in lower:
        return "security_agreement"
    if "guaranty agreement" in lower or "guarantee agreement" in lower:
        return "guarantee_agreement"
    if "revolving credit agreement" in lower:
        return "revolving_credit_agreement"
    if "term loan agreement" in lower:
        return "term_loan_agreement"
    if "loan agreement" in lower:
        return "loan_agreement"
    return "credit_agreement"


def _is_restated_title(title: str) -> bool:
    lower = re.sub(r"\s+", " ", title.lower()).strip()
    return "amended and restated" in lower or lower.startswith("restated ")


def _document_is_modification(document: FilingDocument) -> bool:
    text = f"{document.description} {document.document}".lower()
    return any(term in text for term in _MODIFICATION_TERMS)


def _parse_date_after(text: str, start: int, *, max_gap: int = 80) -> date | None:
    tail = text[start : start + max_gap]
    match = _CONTRACT_DATE_RE.search(tail)
    if match:
        try:
            return date(
                int(match.group("year")),
                _MONTHS[match.group("month").lower()],
                int(match.group("day")),
            )
        except ValueError:
            return None
    match = _NUMERIC_CONTRACT_DATE_RE.search(tail)
    if match:
        try:
            return date(int(match.group("year")), int(match.group("month")), int(match.group("day")))
        except ValueError:
            return None
    return None


def extract_contract_identities(text: str) -> tuple[ContractIdentity, ...]:
    plain = html_to_text(text)
    identities: list[ContractIdentity] = []
    seen: set[tuple[str, date, bool]] = set()
    for match in _CONTRACT_TITLE_RE.finditer(plain):
        execution_date = _parse_date_after(plain, match.end())
        if execution_date is None:
            continue
        title = re.sub(r"\s+", " ", match.group("title")).strip()
        kind = _canonical_kind(title)
        key = (kind, execution_date, _is_restated_title(title))
        if key in seen:
            continue
        seen.add(key)
        identities.append(ContractIdentity(title=title, kind=kind, execution_date=execution_date))
    return tuple(identities)


def _identity_matches(target: ContractIdentity, text: str) -> bool:
    target_restated = _is_restated_title(target.title)
    for identity in extract_contract_identities(text):
        if (
            identity.execution_date == target.execution_date
            and identity.kind == target.kind
            and _is_restated_title(identity.title) == target_restated
        ):
            return True
    return False


def reverse_search_contract_identity(
    client: SecClient,
    *,
    identity: ContractIdentity,
    filings: Iterable[SecFiling],
    source_published_on: date,
    max_filings: int = 40,
    days_before_execution: int = 30,
    days_after_execution: int = 550,
    index_cache: dict[str, tuple[FilingDocument, ...]] | None = None,
    text_cache: dict[str, str] | None = None,
) -> ContractSearchResult:
    """Search historical SEC exhibits for a unique contract-title + execution-date match.

    The execution date is treated as contract identity evidence, not filing date.
    A result can therefore be filed later (for example in a 10-Q), but never after
    the referencing source document. Multiple matching exhibits are intentionally
    returned as ambiguous candidates rather than ranked to a winner.

    Modification exhibits are excluded when resolving an original agreement, so
    an amendment that merely quotes the original agreement's title/date cannot be
    mistaken for the original contract. Restated agreements are matched only to
    restated contract identities.
    """

    if max_filings <= 0:
        raise ValueError("max_filings must be positive")
    if days_before_execution < 0 or days_after_execution < 0:
        raise ValueError("contract search windows cannot be negative")

    index_cache = index_cache if index_cache is not None else {}
    text_cache = text_cache if text_cache is not None else {}
    lower_bound = identity.execution_date - timedelta(days=days_before_execution)
    upper_bound = min(source_published_on, identity.execution_date + timedelta(days=days_after_execution))
    candidates_filings = [
        filing
        for filing in filings
        if lower_bound <= filing.filing_date <= upper_bound
        and filing.filing_date <= source_published_on
    ]
    candidates_filings.sort(
        key=lambda filing: (
            abs((filing.filing_date - identity.execution_date).days),
            filing.filing_date,
            filing.accession_number,
        )
    )
    candidates_filings = candidates_filings[:max_filings]

    matches: list[FilingDocument] = []
    warnings: list[str] = []
    searched: list[str] = []
    target_restated = _is_restated_title(identity.title)
    for filing in candidates_filings:
        searched.append(filing.accession_number)
        documents = index_cache.get(filing.accession_number)
        if documents is None:
            try:
                index_html = _get_text(client, filing_index_url(filing))
                documents = parse_filing_documents(index_html, filing)
                index_cache[filing.accession_number] = documents
            except Exception as exc:
                warnings.append(f"failed contract-identity index lookup for {filing.accession_number}: {exc}")
                continue

        debt_documents = [
            document
            for document in documents
            if document.document_type.upper().startswith(("EX-4", "EX-10"))
            and (target_restated or not _document_is_modification(document))
        ]
        for document in debt_documents:
            try:
                document_text = text_cache.get(document.url)
                if document_text is None:
                    document_text = _get_text(client, document.url)
                    text_cache[document.url] = document_text
            except Exception as exc:
                warnings.append(f"failed contract-identity document lookup for {document.document}: {exc}")
                continue
            if _identity_matches(identity, document_text):
                matches.append(document)

    unique: dict[str, FilingDocument] = {item.url: item for item in matches}
    return ContractSearchResult(
        identity=identity,
        candidates=tuple(unique.values()),
        searched_filings=tuple(searched),
        warnings=tuple(dict.fromkeys(warnings)),
    )

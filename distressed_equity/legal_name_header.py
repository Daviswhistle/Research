from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re

from .contract_parties import normalize_party_name
from .sec import SEC_WEB_BASE, SecClient, SecFiling, normalize_cik
from .sec_instruments import _get_text


_ENTITY_SECTION_RE = re.compile(
    r"(?mi)^[ \t]*(?:FILER|ISSUER|SUBJECT COMPANY|REPORTING-OWNER|FILED BY):[ \t]*$"
)
_CIK_RE = re.compile(r"CENTRAL INDEX KEY:\s*(?P<cik>\d{1,10})", re.IGNORECASE)
_CURRENT_NAME_RE = re.compile(
    r"COMPANY CONFORMED NAME:\s*(?P<name>[^\r\n]+)",
    re.IGNORECASE,
)
_FORMER_NAME_RE = re.compile(
    r"FORMER CONFORMED NAME:\s*(?P<name>[^\r\n]+)"
    r"(?P<body>.{0,500}?)"
    r"DATE OF NAME CHANGE:\s*(?P<date>\d{8})",
    re.IGNORECASE | re.DOTALL,
)
_FORMER_NAME_WITHOUT_DATE_RE = re.compile(
    r"FORMER CONFORMED NAME:\s*(?P<name>[^\r\n]+)",
    re.IGNORECASE,
)
_SEC_HEADER_RE = re.compile(r"<SEC-HEADER>(?P<header>.*?)</SEC-HEADER>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class CompleteSubmissionFormerName:
    name: str
    normalized_name: str
    change_on: date | None


@dataclass(frozen=True)
class CompleteSubmissionNameEvidence:
    cik: str
    accession_number: str
    filing_date: date
    source_url: str
    current_name: str | None
    normalized_current_name: str | None
    former_names: tuple[CompleteSubmissionFormerName, ...]
    warnings: tuple[str, ...]


def complete_submission_text_url(filing: SecFiling) -> str:
    cik10 = normalize_cik(filing.cik)
    accession = filing.accession_number.strip()
    if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
        raise ValueError(f"invalid SEC accession number: {filing.accession_number}")
    return (
        f"{SEC_WEB_BASE}/Archives/edgar/data/{int(cik10)}/"
        f"{accession.replace('-', '')}/{accession}.txt"
    )


def _parse_yyyymmdd(value: str) -> date | None:
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    except (TypeError, ValueError):
        return None


def _header_text(raw_text: str) -> tuple[str, tuple[str, ...]]:
    match = _SEC_HEADER_RE.search(raw_text)
    if match:
        return match.group("header"), ()
    before_document = raw_text.split("<DOCUMENT>", 1)[0]
    if "CENTRAL INDEX KEY" in before_document.upper():
        return before_document, ("complete submission text lacked explicit SEC-HEADER markers; pre-DOCUMENT text used",)
    return "", ("complete submission text did not contain a parseable SEC header",)


def _entity_sections(header: str) -> tuple[str, ...]:
    markers = list(_ENTITY_SECTION_RE.finditer(header))
    if not markers:
        return (header,) if header else ()
    sections: list[str] = []
    prefix = header[: markers[0].start()]
    if "CENTRAL INDEX KEY" in prefix.upper():
        sections.append(prefix)
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(header)
        sections.append(header[marker.start() : end])
    return tuple(sections)


def extract_complete_submission_name_evidence(
    raw_text: str,
    *,
    filing: SecFiling,
    target_cik: str | int | None = None,
    source_url: str | None = None,
) -> CompleteSubmissionNameEvidence:
    cik10 = normalize_cik(target_cik if target_cik is not None else filing.cik)
    url = source_url or complete_submission_text_url(filing)
    header, header_warnings = _header_text(raw_text)
    warnings = list(header_warnings)

    matching_sections: list[str] = []
    for section in _entity_sections(header):
        ciks = {normalize_cik(match.group("cik")) for match in _CIK_RE.finditer(section)}
        if cik10 in ciks:
            matching_sections.append(section)

    if not matching_sections:
        warnings.append(f"target CIK {cik10} was not found in the complete-submission SEC header")

    current_names: dict[str, str] = {}
    former_names: dict[tuple[str, date | None], CompleteSubmissionFormerName] = {}
    for section in matching_sections:
        for match in _CURRENT_NAME_RE.finditer(section):
            name = re.sub(r"\s+", " ", match.group("name")).strip(" \t")
            normalized = normalize_party_name(name)
            if normalized:
                current_names.setdefault(normalized, name)

        dated_spans: list[tuple[int, int]] = []
        for match in _FORMER_NAME_RE.finditer(section):
            dated_spans.append(match.span())
            name = re.sub(r"\s+", " ", match.group("name")).strip(" \t")
            normalized = normalize_party_name(name)
            change_on = _parse_yyyymmdd(match.group("date"))
            if not normalized:
                continue
            if change_on is None:
                warnings.append(f"invalid DATE OF NAME CHANGE for {name}: {match.group('date')}")
                continue
            if change_on > filing.filing_date:
                warnings.append(
                    f"future DATE OF NAME CHANGE ignored for {name}: {change_on.isoformat()} > filing {filing.filing_date.isoformat()}"
                )
                continue
            former_names[(normalized, change_on)] = CompleteSubmissionFormerName(
                name=name,
                normalized_name=normalized,
                change_on=change_on,
            )

        # A rare/older header can expose a former name without a change date. The
        # filing itself proves only that the name was already former by filing date;
        # preserve the identity evidence but do not invent an exact transition date.
        for match in _FORMER_NAME_WITHOUT_DATE_RE.finditer(section):
            if any(start <= match.start() < end for start, end in dated_spans):
                continue
            name = re.sub(r"\s+", " ", match.group("name")).strip(" \t")
            normalized = normalize_party_name(name)
            if normalized and not any(key[0] == normalized for key in former_names):
                former_names[(normalized, None)] = CompleteSubmissionFormerName(
                    name=name,
                    normalized_name=normalized,
                    change_on=None,
                )

    current_name: str | None = None
    normalized_current: str | None = None
    if current_names:
        normalized_current, current_name = next(iter(current_names.items()))
        if len(current_names) > 1:
            warnings.append(
                "multiple COMPANY CONFORMED NAME values were found for the target CIK: "
                + ", ".join(sorted(current_names.values()))
            )

    former = tuple(
        sorted(
            former_names.values(),
            key=lambda item: (item.change_on or date.max, item.normalized_name),
        )
    )
    return CompleteSubmissionNameEvidence(
        cik=cik10,
        accession_number=filing.accession_number,
        filing_date=filing.filing_date,
        source_url=url,
        current_name=current_name,
        normalized_current_name=normalized_current,
        former_names=former,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def fetch_complete_submission_name_evidence(
    client: SecClient,
    *,
    filing: SecFiling,
    target_cik: str | int | None = None,
) -> CompleteSubmissionNameEvidence:
    url = complete_submission_text_url(filing)
    raw_text = _get_text(client, url)
    return extract_complete_submission_name_evidence(
        raw_text,
        filing=filing,
        target_cik=target_cik,
        source_url=url,
    )

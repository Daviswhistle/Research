from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from html.parser import HTMLParser
import re
from typing import Any, Iterable
from urllib.parse import urljoin

from .agents import AgentTask
from .sec import SEC_WEB_BASE, SecClient, SecFiling
from .sec_debt import html_to_text


_DEBT_DESCRIPTION_TERMS = (
    "indenture",
    "credit agreement",
    "loan agreement",
    "term loan",
    "revolving credit",
    "revolver",
    "note purchase",
    "senior notes",
    "secured notes",
    "supplemental indenture",
    "amendment",
    "waiver",
    "security agreement",
    "guarantee",
    "guaranty",
    "pledge agreement",
)
_DEBT_TEXT_TERMS = _DEBT_DESCRIPTION_TERMS + (
    "aggregate principal amount",
    "maturity date",
    "borrowings under",
    "commitments under",
    "interest rate",
    "secured by",
    "first lien",
    "second lien",
    "sofr",
)
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
_MONEY_RE = re.compile(
    r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(billion|million|thousand|bn|mm|m|b|k)?",
    re.IGNORECASE,
)
_NOTES_TITLE_RE = re.compile(
    r"(?P<coupon>[0-9]+(?:\.[0-9]+)?)\s*%\s+"
    r"(?P<label>(?:(?:senior|subordinated|secured|unsecured)\s+){0,3}notes?)\s+"
    r"(?:due|maturing)\s+(?P<year>20[0-9]{2})",
    re.IGNORECASE,
)
_FACILITY_RE = re.compile(
    r"\b(?P<label>(?:(?:senior|secured|unsecured|asset[- ]based)\s+){0,3}"
    r"(?:revolving\s+credit\s+facility|credit\s+facility|term\s+loan\s+facility|term\s+loan))\b",
    re.IGNORECASE,
)
_PRINCIPAL_RE = re.compile(
    r"(?:aggregate\s+principal\s+amount|principal\s+amount)\s+(?:of\s+)?(?P<amount>\$\s*[0-9][0-9,]*(?:\.[0-9]+)?\s*(?:billion|million|thousand|bn|mm|m|b|k)?)",
    re.IGNORECASE,
)
_COMMITMENT_RE = re.compile(
    r"(?:aggregate\s+commitments?|commitments?|commitment\s+amount|facility\s+size)\s+(?:of\s+|equal\s+to\s+)?(?P<amount>\$\s*[0-9][0-9,]*(?:\.[0-9]+)?\s*(?:billion|million|thousand|bn|mm|m|b|k)?)",
    re.IGNORECASE,
)
_MATURITY_DATE_RE = re.compile(
    r"(?:matures?|maturity\s+date(?:\s+is)?|due)\s+(?:on\s+)?"
    r"(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"(?P<day>[0-9]{1,2}),\s+(?P<year>20[0-9]{2})",
    re.IGNORECASE,
)
_CUSIP_RE = re.compile(r"\bCUSIP(?:\s+(?:No\.?|Number))?\s*[:#]?\s*([0-9A-Z]{9})\b", re.IGNORECASE)
_ISIN_RE = re.compile(r"\bISIN\s*[:#]?\s*([A-Z]{2}[A-Z0-9]{10})\b", re.IGNORECASE)
_SOFR_SPREAD_RE = re.compile(
    r"\bSOFR(?:\s+Rate)?\s*(?:plus|\+)\s*(?P<spread>[0-9]+(?:\.[0-9]+)?)\s*%",
    re.IGNORECASE,
)
_BPS_SPREAD_RE = re.compile(
    r"\b(?:SOFR|LIBOR|base\s+rate)[^.;]{0,80}?(?P<spread>[0-9]{2,4})\s+basis\s+points\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FilingDocument:
    sequence: int | None
    description: str
    document: str
    document_type: str
    size: int | None
    url: str
    source_accession: str
    filing_date: date
    filing_form: str


@dataclass(frozen=True)
class InstrumentSourceSpan:
    span_id: str
    source_accession: str
    published_on: date
    document_url: str
    document_type: str
    description: str
    text: str


@dataclass(frozen=True)
class InstrumentFieldProposal:
    field: str
    value: Any
    confidence: str
    source_span_id: str
    basis: str


@dataclass(frozen=True)
class DebtInstrumentSourceCandidate:
    candidate_id: str
    source_accession: str
    as_of_date: date
    document_url: str
    document_type: str
    document_description: str
    proposals: tuple[InstrumentFieldProposal, ...]
    source_span_ids: tuple[str, ...]
    snapshot_template: dict[str, Any]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SecInstrumentPacket:
    ticker: str | None
    company_name: str
    analysis_date: date
    documents: tuple[FilingDocument, ...]
    spans: tuple[InstrumentSourceSpan, ...]
    candidates: tuple[DebtInstrumentSourceCandidate, ...]
    warnings: tuple[str, ...]


class _FilingIndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[list[str], list[str]]] = []
        self._in_row = False
        self._in_cell = False
        self._cell_parts: list[str] = []
        self._cells: list[str] = []
        self._hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._in_row = True
            self._cells = []
            self._hrefs = []
        elif self._in_row and tag in {"td", "th"}:
            self._in_cell = True
            self._cell_parts = []
        elif self._in_row and tag == "a":
            href = next((value for key, value in attrs if key.lower() == "href"), None)
            if href:
                self._hrefs.append(href)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._in_row and tag in {"td", "th"} and self._in_cell:
            self._cells.append(re.sub(r"\s+", " ", "".join(self._cell_parts)).strip())
            self._cell_parts = []
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if self._cells:
                self.rows.append((list(self._cells), list(self._hrefs)))
            self._in_row = False
            self._in_cell = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_parts.append(data)


def filing_index_url(filing: SecFiling) -> str:
    accession = filing.accession_number.replace("-", "")
    return (
        f"{SEC_WEB_BASE}/Archives/edgar/data/{int(filing.cik)}/{accession}/"
        f"{filing.accession_number}-index.html"
    )


def _get_text(client: SecClient, url: str, *, accept: str = "text/html,text/plain;q=0.9,*/*;q=0.1") -> str:
    client._throttle()
    response = client.session.get(
        url,
        headers={
            "User-Agent": client.user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": accept,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def parse_filing_documents(index_html: str, filing: SecFiling) -> tuple[FilingDocument, ...]:
    parser = _FilingIndexParser()
    parser.feed(index_html)
    parser.close()
    base = filing_index_url(filing)
    documents: list[FilingDocument] = []
    for cells, hrefs in parser.rows:
        if len(cells) < 5:
            continue
        sequence_raw, description, document, document_type, size_raw = cells[:5]
        if not document or not document_type or document.lower() == "document":
            continue
        href = next((item for item in hrefs if document in item or item.endswith(document)), None)
        if not href:
            continue
        try:
            sequence = int(sequence_raw)
        except (TypeError, ValueError):
            sequence = None
        digits = re.sub(r"[^0-9]", "", size_raw)
        size = int(digits) if digits else None
        documents.append(
            FilingDocument(
                sequence=sequence,
                description=description,
                document=document,
                document_type=document_type,
                size=size,
                url=urljoin(base, href),
                source_accession=filing.accession_number,
                filing_date=filing.filing_date,
                filing_form=filing.form,
            )
        )
    return tuple(documents)


def debt_document_score(document: FilingDocument) -> int:
    doc_type = document.document_type.upper()
    text = f"{document.description} {document.document}".lower()
    if doc_type.startswith(("EX-31", "EX-32", "EX-101")):
        return -100
    score = 0
    if doc_type.startswith("EX-4"):
        score += 5
    elif doc_type.startswith("EX-10"):
        score += 2
    elif doc_type.startswith("EX-2"):
        score += 1
    else:
        return -100
    score += 7 * sum(term in text for term in _DEBT_DESCRIPTION_TERMS)
    if "amend" in text or "supplement" in text or "waiver" in text:
        score += 2
    return score


def select_debt_documents(documents: Iterable[FilingDocument], *, limit: int = 8) -> tuple[FilingDocument, ...]:
    ranked = [(debt_document_score(item), item) for item in documents]
    ranked = [item for item in ranked if item[0] > 0]
    ranked.sort(key=lambda item: (-item[0], item[1].sequence or 10_000, item[1].document))
    return tuple(item for _, item in ranked[: max(limit, 0)])


def _money_value(raw: str) -> float:
    match = _MONEY_RE.search(raw)
    if not match:
        raise ValueError(f"not a dollar amount: {raw}")
    number = float(match.group(1).replace(",", ""))
    suffix = (match.group(2) or "").lower()
    multiplier = {
        "billion": 1_000_000_000,
        "bn": 1_000_000_000,
        "b": 1_000_000_000,
        "million": 1_000_000,
        "mm": 1_000_000,
        "m": 1_000_000,
        "thousand": 1_000,
        "k": 1_000,
        "": 1,
    }[suffix]
    return number * multiplier


def _candidate_blocks(text: str) -> Iterable[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    seen: set[str] = set()
    for idx in range(len(lines)):
        block = " ".join(lines[max(0, idx - 1) : min(len(lines), idx + 3)])
        block = re.sub(r"\s+", " ", block).strip()
        if len(block) < 45 or block in seen:
            continue
        seen.add(block)
        lower = block.lower()
        if any(term in lower for term in _DEBT_TEXT_TERMS) or _NOTES_TITLE_RE.search(block) or _FACILITY_RE.search(block):
            yield block[:2400]


def _infer_instrument_type(name: str, text: str) -> str | None:
    lower = f"{name} {text}".lower()
    if "revolving credit" in lower or "revolver" in lower:
        return "revolving_credit_facility"
    if "term loan" in lower:
        return "term_loan"
    if "notes" in lower or "note" in lower:
        return "notes"
    if "credit agreement" in lower or "credit facility" in lower:
        return "credit_facility"
    if "indenture" in lower:
        return "indenture_notes"
    return None


def _date_from_match(match: re.Match[str]) -> str:
    month = _MONTHS[match.group("month").lower()]
    return date(int(match.group("year")), month, int(match.group("day"))).isoformat()


def _proposals_for_block(span_id: str, block: str, document: FilingDocument) -> tuple[InstrumentFieldProposal, ...]:
    proposals: list[InstrumentFieldProposal] = []
    notes_match = _NOTES_TITLE_RE.search(block)
    facility_match = _FACILITY_RE.search(block)
    proposed_name = None
    if notes_match:
        proposed_name = re.sub(r"\s+", " ", notes_match.group(0)).strip()
        proposals.extend(
            [
                InstrumentFieldProposal("name", proposed_name, "high", span_id, "explicit note title with coupon and due year"),
                InstrumentFieldProposal("instrument_type", "notes", "high", span_id, "explicit notes title"),
                InstrumentFieldProposal("coupon_pct", float(notes_match.group("coupon")), "high", span_id, "coupon embedded in notes title"),
                InstrumentFieldProposal("maturity_year", int(notes_match.group("year")), "high", span_id, "due year embedded in notes title"),
            ]
        )
    elif facility_match:
        proposed_name = re.sub(r"\s+", " ", facility_match.group("label")).strip()
        instrument_type = _infer_instrument_type(proposed_name, block)
        proposals.append(InstrumentFieldProposal("name", proposed_name, "medium", span_id, "facility name phrase"))
        if instrument_type:
            proposals.append(InstrumentFieldProposal("instrument_type", instrument_type, "medium", span_id, "facility phrase classification"))
    else:
        description_lower = document.description.lower()
        if any(term in description_lower for term in _DEBT_DESCRIPTION_TERMS):
            proposed_name = document.description.strip()
            instrument_type = _infer_instrument_type(proposed_name, block)
            proposals.append(InstrumentFieldProposal("name", proposed_name, "medium", span_id, "debt-relevant SEC exhibit description"))
            if instrument_type:
                proposals.append(InstrumentFieldProposal("instrument_type", instrument_type, "medium", span_id, "exhibit description/text classification"))

    principal_match = _PRINCIPAL_RE.search(block)
    if principal_match:
        proposals.append(
            InstrumentFieldProposal(
                "principal",
                _money_value(principal_match.group("amount")),
                "high",
                span_id,
                "explicit aggregate/principal amount phrase",
            )
        )
    commitment_match = _COMMITMENT_RE.search(block)
    if commitment_match:
        proposals.append(
            InstrumentFieldProposal(
                "commitment",
                _money_value(commitment_match.group("amount")),
                "high",
                span_id,
                "explicit commitment/facility-size phrase",
            )
        )
    maturity_match = _MATURITY_DATE_RE.search(block)
    if maturity_match:
        proposals.append(InstrumentFieldProposal("maturity_date", _date_from_match(maturity_match), "high", span_id, "explicit maturity date phrase"))
        proposals.append(InstrumentFieldProposal("maturity_year", int(maturity_match.group("year")), "high", span_id, "explicit maturity date phrase"))

    cusip = _CUSIP_RE.search(block)
    if cusip:
        proposals.append(InstrumentFieldProposal("cusip", cusip.group(1).upper(), "high", span_id, "explicit CUSIP label"))
    isin = _ISIN_RE.search(block)
    if isin:
        proposals.append(InstrumentFieldProposal("isin", isin.group(1).upper(), "high", span_id, "explicit ISIN label"))

    sofr = _SOFR_SPREAD_RE.search(block)
    bps = _BPS_SPREAD_RE.search(block)
    if sofr:
        proposals.append(InstrumentFieldProposal("benchmark", "SOFR", "high", span_id, "explicit SOFR pricing phrase"))
        proposals.append(InstrumentFieldProposal("spread_bps", float(sofr.group("spread")) * 100.0, "high", span_id, "SOFR percentage spread converted to bps"))
    elif bps:
        benchmark = "SOFR" if "sofr" in block.lower() else ("LIBOR" if "libor" in block.lower() else "base_rate")
        proposals.append(InstrumentFieldProposal("benchmark", benchmark, "medium", span_id, "benchmark in pricing phrase"))
        proposals.append(InstrumentFieldProposal("spread_bps", float(bps.group("spread")), "medium", span_id, "explicit basis-point spread"))

    lower = block.lower()
    if "senior secured" in lower or "first lien" in lower:
        proposals.append(InstrumentFieldProposal("seniority", "senior", "medium", span_id, "senior secured/first-lien language"))
        proposals.append(InstrumentFieldProposal("secured", True, "medium", span_id, "secured/lien language"))
    elif "senior unsecured" in lower:
        proposals.append(InstrumentFieldProposal("seniority", "senior", "medium", span_id, "senior unsecured language"))
        proposals.append(InstrumentFieldProposal("secured", False, "medium", span_id, "unsecured language"))

    return tuple(proposals)


def extract_source_candidates(
    document_text: str,
    document: FilingDocument,
    *,
    max_candidates: int = 20,
) -> tuple[tuple[InstrumentSourceSpan, ...], tuple[DebtInstrumentSourceCandidate, ...]]:
    text = html_to_text(document_text)
    lower = text.lower()
    if not any(term in lower for term in _DEBT_TEXT_TERMS) and not _NOTES_TITLE_RE.search(text) and not _FACILITY_RE.search(text):
        return (), ()

    spans: list[InstrumentSourceSpan] = []
    candidates: list[DebtInstrumentSourceCandidate] = []
    seen_keys: set[tuple[str, str]] = set()
    for block in _candidate_blocks(text):
        span_id = f"{document.source_accession}:{document.sequence or 0}:s{len(spans) + 1}"
        proposals = _proposals_for_block(span_id, block, document)
        if not proposals:
            continue
        span = InstrumentSourceSpan(
            span_id=span_id,
            source_accession=document.source_accession,
            published_on=document.filing_date,
            document_url=document.url,
            document_type=document.document_type,
            description=document.description,
            text=block,
        )
        name = next((str(item.value) for item in proposals if item.field == "name"), document.description or document.document)
        instrument_type = next((str(item.value) for item in proposals if item.field == "instrument_type"), "")
        dedupe_key = (name.lower().strip(), instrument_type.lower().strip())
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        spans.append(span)

        template: dict[str, Any] = {
            "as_of_date": document.filing_date.isoformat(),
            "source_accession": document.source_accession,
            "name": name,
            "instrument_type": instrument_type or None,
            "principal": None,
            "maturity_date": None,
            "maturity_year": None,
            "coupon_pct": None,
            "benchmark": None,
            "spread_bps": None,
            "seniority": None,
            "secured": None,
            "currency": "USD",
            "cusip": None,
            "isin": None,
            "commitment": None,
            "drawn": None,
            "available": None,
            "source_refs": [span_id],
            "notes": ["review all deterministic field proposals against the cited SEC exhibit span before ledger ingestion"],
        }
        for proposal in proposals:
            if proposal.confidence == "high" and proposal.field in template:
                template[proposal.field] = proposal.value

        candidates.append(
            DebtInstrumentSourceCandidate(
                candidate_id=f"{document.source_accession}:{document.sequence or 0}:c{len(candidates) + 1}",
                source_accession=document.source_accession,
                as_of_date=document.filing_date,
                document_url=document.url,
                document_type=document.document_type,
                document_description=document.description,
                proposals=proposals,
                source_span_ids=(span_id,),
                snapshot_template=template,
                warnings=("candidate fields are navigation/extraction aids, not confirmed legal terms",),
            )
        )
        if len(candidates) >= max_candidates:
            break
    return tuple(spans), tuple(candidates)


def build_sec_instrument_packet(
    client: SecClient,
    *,
    ticker: str | None,
    company_name: str,
    analysis_date: date,
    filings: Iterable[SecFiling],
    filing_limit: int = 6,
    max_exhibits_per_filing: int = 8,
    max_candidates_per_exhibit: int = 20,
) -> SecInstrumentPacket:
    documents: list[FilingDocument] = []
    spans: list[InstrumentSourceSpan] = []
    candidates: list[DebtInstrumentSourceCandidate] = []
    warnings: list[str] = [
        "SEC exhibit field proposals are deterministic extraction candidates, not confirmed debt schedule rows.",
        "Only source-verified snapshots should be passed into the stable debt-instrument ledger.",
    ]
    selected_filings = [
        filing
        for filing in filings
        if filing.filing_date <= analysis_date and filing.form in {"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A"}
    ][: max(filing_limit, 0)]

    for filing in selected_filings:
        try:
            index_html = _get_text(client, filing_index_url(filing))
            filing_documents = parse_filing_documents(index_html, filing)
            selected_documents = select_debt_documents(filing_documents, limit=max_exhibits_per_filing)
            documents.extend(selected_documents)
        except Exception as exc:
            warnings.append(f"failed to enumerate exhibits for {filing.accession_number}: {exc}")
            continue

        for document in selected_documents:
            try:
                document_html = _get_text(client, document.url)
                extracted_spans, extracted_candidates = extract_source_candidates(
                    document_html,
                    document,
                    max_candidates=max_candidates_per_exhibit,
                )
                spans.extend(extracted_spans)
                candidates.extend(extracted_candidates)
            except Exception as exc:
                warnings.append(f"failed to extract debt fields from {document.document}: {exc}")

    return SecInstrumentPacket(
        ticker=ticker,
        company_name=company_name,
        analysis_date=analysis_date,
        documents=tuple(documents),
        spans=tuple(spans),
        candidates=tuple(candidates),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def build_instrument_verification_task(packet: SecInstrumentPacket) -> AgentTask:
    return AgentTask(
        name="debt_instrument_verifier",
        objective=(
            f"Verify SEC exhibit-derived debt instrument candidates for {packet.company_name} and return ledger-ready instrument snapshots."
        ),
        guardrails=(
            f"Do not use information published after {packet.analysis_date.isoformat()}.",
            "Treat every deterministic field proposal as unconfirmed until its cited SEC exhibit span supports the exact instrument and term.",
            "Do not merge two instruments merely because their names are similar; preserve explicit CUSIP/ISIN when available.",
            "Separate principal, commitment, drawn amount and availability; do not substitute one for another.",
            "Return unresolved fields as null instead of guessing.",
        ),
        required_output=(
            "Ledger-ready instruments[] using the debt-instrument snapshot schema",
            "Source span IDs for every instrument",
            "Explicit unresolved/ambiguous instrument identities",
        ),
    )


def instrument_verification_template(packet: SecInstrumentPacket) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "analysis_date": packet.analysis_date.isoformat(),
        "company_name": packet.company_name,
        "instruments": [dict(candidate.snapshot_template) for candidate in packet.candidates],
        "unresolved": [],
        "warnings": [
            "Delete duplicate candidate rows and verify every retained field against source_refs before using this file with distressed-equity-debt-ledger."
        ],
    }


def sec_instrument_packet_to_dict(packet: SecInstrumentPacket) -> dict[str, Any]:
    def convert(value: Any) -> Any:
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, tuple):
            return [convert(item) for item in value]
        if isinstance(value, list):
            return [convert(item) for item in value]
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        if hasattr(value, "__dataclass_fields__"):
            return convert(asdict(value))
        return value

    return convert(packet)

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
    cluster_status: str = "single_span"
    cluster_basis: tuple[str, ...] = ()


@dataclass(frozen=True)
class SecInstrumentPacket:
    ticker: str | None
    company_name: str
    analysis_date: date
    documents: tuple[FilingDocument, ...]
    spans: tuple[InstrumentSourceSpan, ...]
    candidates: tuple[DebtInstrumentSourceCandidate, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _SpanFragment:
    span: InstrumentSourceSpan
    proposals: tuple[InstrumentFieldProposal, ...]
    order: int


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


def _text_key(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def _proposal_values(
    proposals: Iterable[InstrumentFieldProposal],
    field: str,
    *,
    confidence: str | None = None,
) -> set[Any]:
    return {
        item.value
        for item in proposals
        if item.field == field and (confidence is None or item.confidence == confidence)
    }


def _proposal_names(
    proposals: Iterable[InstrumentFieldProposal],
    *,
    basis: str | None = None,
) -> set[str]:
    return {
        _text_key(item.value)
        for item in proposals
        if item.field == "name" and (basis is None or item.basis == basis)
    }


def _instrument_families(proposals: Iterable[InstrumentFieldProposal]) -> set[str]:
    families: set[str] = set()
    for value in _proposal_values(proposals, "instrument_type"):
        text = str(value)
        if text in {"notes", "indenture_notes"}:
            families.add("notes")
        elif text == "revolving_credit_facility":
            families.add("revolver")
        elif text == "term_loan":
            families.add("term_loan")
        elif text == "credit_facility":
            families.add("credit")
        elif text:
            families.add(text)
    return families


def _families_conflict(left: set[str], right: set[str]) -> bool:
    if not left or not right:
        return False
    for a in left:
        for b in right:
            if a == b:
                return False
            if {a, b} <= {"credit", "revolver", "term_loan"} and "credit" in {a, b}:
                return False
    return True


def _proposal_conflict(
    left: Iterable[InstrumentFieldProposal],
    right: Iterable[InstrumentFieldProposal],
) -> bool:
    left = tuple(left)
    right = tuple(right)
    for field in ("cusip", "isin", "maturity_date", "maturity_year"):
        a = _proposal_values(left, field, confidence="high")
        b = _proposal_values(right, field, confidence="high")
        if a and b and a.isdisjoint(b):
            return True

    a_coupon = _proposal_values(left, "coupon_pct", confidence="high")
    b_coupon = _proposal_values(right, "coupon_pct", confidence="high")
    if a_coupon and b_coupon and a_coupon.isdisjoint(b_coupon):
        if "notes" in _instrument_families(left) or "notes" in _instrument_families(right):
            return True

    left_note_names = _proposal_names(left, basis="explicit note title with coupon and due year")
    right_note_names = _proposal_names(right, basis="explicit note title with coupon and due year")
    if left_note_names and right_note_names and left_note_names.isdisjoint(right_note_names):
        return True

    return _families_conflict(_instrument_families(left), _instrument_families(right))


def _document_identity(document: FilingDocument) -> tuple[str, str, float | None, int | None] | None:
    note = _NOTES_TITLE_RE.search(document.description)
    if note:
        return (
            "notes",
            _text_key(note.group(0)),
            float(note.group("coupon")),
            int(note.group("year")),
        )
    facility = _FACILITY_RE.search(document.description)
    if facility:
        label = re.sub(r"\s+", " ", facility.group("label")).strip()
        return (
            _infer_instrument_type(label, document.description) or "credit_facility",
            _text_key(label),
            None,
            None,
        )
    return None


def _cluster_context(
    document: FilingDocument,
    fragments: tuple[_SpanFragment, ...],
) -> dict[str, Any]:
    note_names: set[str] = set()
    facility_names: set[str] = set()
    for fragment in fragments:
        note_names.update(
            _proposal_names(fragment.proposals, basis="explicit note title with coupon and due year")
        )
        facility_names.update(_proposal_names(fragment.proposals, basis="facility name phrase"))
    return {
        "document_identity": _document_identity(document),
        "unique_note_name": next(iter(note_names)) if len(note_names) == 1 else None,
        "unique_facility_name": next(iter(facility_names)) if len(facility_names) == 1 else None,
    }


def _fragment_matches_note_anchor(fragment: _SpanFragment, anchor_name: str | None) -> bool:
    families = _instrument_families(fragment.proposals)
    if families and families.isdisjoint({"notes"}):
        return False
    explicit = _proposal_names(fragment.proposals, basis="explicit note title with coupon and due year")
    return not explicit or (anchor_name is not None and anchor_name in explicit)


def _fragment_matches_facility_anchor(fragment: _SpanFragment, anchor_name: str | None) -> bool:
    families = _instrument_families(fragment.proposals)
    if families and "notes" in families:
        return False
    explicit = _proposal_names(fragment.proposals, basis="facility name phrase")
    return not explicit or (anchor_name is not None and anchor_name in explicit)


def _pair_link(
    left: _SpanFragment,
    right: _SpanFragment,
    context: dict[str, Any],
) -> tuple[int, str | None]:
    if _proposal_conflict(left.proposals, right.proposals):
        return 0, None

    for field in ("cusip", "isin"):
        a = _proposal_values(left.proposals, field, confidence="high")
        b = _proposal_values(right.proposals, field, confidence="high")
        if a and b and not a.isdisjoint(b):
            return 100, f"same_explicit_{field}"

    left_note = _proposal_names(left.proposals, basis="explicit note title with coupon and due year")
    right_note = _proposal_names(right.proposals, basis="explicit note title with coupon and due year")
    if left_note and right_note and not left_note.isdisjoint(right_note):
        return 95, "same_explicit_note_title"

    document_identity = context.get("document_identity")
    if document_identity and document_identity[0] == "notes":
        anchor_name = document_identity[1]
        if _fragment_matches_note_anchor(left, anchor_name) and _fragment_matches_note_anchor(right, anchor_name):
            return 90, "specific_document_note_identity"

    unique_note = context.get("unique_note_name")
    if unique_note and _fragment_matches_note_anchor(left, unique_note) and _fragment_matches_note_anchor(right, unique_note):
        return 85, "unique_explicit_note_identity_in_document"

    left_facility = _proposal_names(left.proposals, basis="facility name phrase")
    right_facility = _proposal_names(right.proposals, basis="facility name phrase")
    if left_facility and right_facility and not left_facility.isdisjoint(right_facility):
        return 80, "same_explicit_facility_label"

    if document_identity and document_identity[0] != "notes":
        anchor_name = document_identity[1]
        if _fragment_matches_facility_anchor(left, anchor_name) and _fragment_matches_facility_anchor(right, anchor_name):
            return 75, "specific_document_facility_identity"

    unique_facility = context.get("unique_facility_name")
    if unique_facility and _fragment_matches_facility_anchor(left, unique_facility) and _fragment_matches_facility_anchor(right, unique_facility):
        return 70, "unique_explicit_facility_identity_in_document"

    left_year = _proposal_values(left.proposals, "maturity_year", confidence="high")
    right_year = _proposal_values(right.proposals, "maturity_year", confidence="high")
    left_coupon = _proposal_values(left.proposals, "coupon_pct", confidence="high")
    right_coupon = _proposal_values(right.proposals, "coupon_pct", confidence="high")
    if (
        left_year
        and right_year
        and not left_year.isdisjoint(right_year)
        and left_coupon
        and right_coupon
        and not left_coupon.isdisjoint(right_coupon)
    ):
        return 75, "same_note_coupon_and_maturity"

    return 0, None


def _cluster_fragments(
    document: FilingDocument,
    fragments: tuple[_SpanFragment, ...],
) -> tuple[tuple[tuple[_SpanFragment, ...], tuple[str, ...]], ...]:
    if not fragments:
        return ()
    context = _cluster_context(document, fragments)
    parent = list(range(len(fragments)))
    members: dict[int, set[int]] = {index: {index} for index in range(len(fragments))}
    reasons: dict[int, set[str]] = {index: set() for index in range(len(fragments))}

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def cluster_proposals(root: int) -> tuple[InstrumentFieldProposal, ...]:
        return tuple(
            proposal
            for index in members[root]
            for proposal in fragments[index].proposals
        )

    edges: list[tuple[int, int, int, str]] = []
    adjacency: dict[int, list[tuple[int, int]]] = {index: [] for index in range(len(fragments))}
    for left_index in range(len(fragments)):
        for right_index in range(left_index + 1, len(fragments)):
            score, reason = _pair_link(fragments[left_index], fragments[right_index], context)
            if score >= 70 and reason:
                edges.append((score, left_index, right_index, reason))
                adjacency[left_index].append((score, right_index))
                adjacency[right_index].append((score, left_index))

    ambiguous_edges: set[tuple[int, int]] = set()
    for index, neighbors in adjacency.items():
        if len(neighbors) < 2:
            continue
        best_score = max(score for score, _ in neighbors)
        best = [other for score, other in neighbors if score == best_score]
        if len(best) < 2:
            continue
        if any(
            _proposal_conflict(fragments[a].proposals, fragments[b].proposals)
            for offset, a in enumerate(best)
            for b in best[offset + 1 :]
        ):
            for other in best:
                ambiguous_edges.add(tuple(sorted((index, other))))

    for score, left_index, right_index, reason in sorted(
        edges,
        key=lambda item: (-item[0], item[1], item[2], item[3]),
    ):
        if tuple(sorted((left_index, right_index))) in ambiguous_edges:
            continue
        left_root = find(left_index)
        right_root = find(right_index)
        if left_root == right_root:
            reasons[left_root].add(reason)
            continue
        if _proposal_conflict(cluster_proposals(left_root), cluster_proposals(right_root)):
            continue
        if min(members[left_root]) > min(members[right_root]):
            left_root, right_root = right_root, left_root
        parent[right_root] = left_root
        members[left_root].update(members.pop(right_root))
        reasons[left_root].update(reasons.pop(right_root))
        reasons[left_root].add(reason)

    output: list[tuple[tuple[_SpanFragment, ...], tuple[str, ...]]] = []
    for root in sorted(members, key=lambda item: min(fragments[index].order for index in members[item])):
        cluster = tuple(sorted((fragments[index] for index in members[root]), key=lambda item: item.order))
        output.append((cluster, tuple(sorted(reasons[root]))))
    return tuple(output)


def _unique_proposals(
    proposals: Iterable[InstrumentFieldProposal],
) -> tuple[InstrumentFieldProposal, ...]:
    output: list[InstrumentFieldProposal] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for item in proposals:
        key = (
            item.field,
            repr(item.value),
            item.confidence,
            item.source_span_id,
            item.basis,
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return tuple(output)


def _candidate_name_and_type(
    proposals: tuple[InstrumentFieldProposal, ...],
    document: FilingDocument,
) -> tuple[str, str | None]:
    explicit_note_names = [
        str(item.value)
        for item in proposals
        if item.field == "name" and item.basis == "explicit note title with coupon and due year"
    ]
    facility_names = [
        str(item.value)
        for item in proposals
        if item.field == "name" and item.basis == "facility name phrase"
    ]
    fallback_names = [str(item.value) for item in proposals if item.field == "name"]
    name = (
        explicit_note_names[0]
        if explicit_note_names
        else facility_names[0]
        if facility_names
        else fallback_names[0]
        if fallback_names
        else document.description or document.document
    )

    high_types = [str(item.value) for item in proposals if item.field == "instrument_type" and item.confidence == "high"]
    other_types = [str(item.value) for item in proposals if item.field == "instrument_type"]
    instrument_type = high_types[0] if high_types else (other_types[0] if other_types else _infer_instrument_type(name, ""))
    return name, instrument_type


def _template_for_cluster(
    document: FilingDocument,
    proposals: tuple[InstrumentFieldProposal, ...],
    source_span_ids: tuple[str, ...],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    name, instrument_type = _candidate_name_and_type(proposals, document)
    template: dict[str, Any] = {
        "as_of_date": document.filing_date.isoformat(),
        "source_accession": document.source_accession,
        "name": name,
        "instrument_type": instrument_type,
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
        "source_refs": list(source_span_ids),
        "notes": ["review all deterministic field proposals against every cited SEC exhibit span before ledger ingestion"],
    }
    warnings: list[str] = []
    for field in template:
        if field in {"as_of_date", "source_accession", "name", "instrument_type", "currency", "source_refs", "notes"}:
            continue
        values = _proposal_values(proposals, field, confidence="high")
        if len(values) == 1:
            template[field] = next(iter(values))
        elif len(values) > 1:
            warnings.append(
                f"cluster contains conflicting high-confidence {field} proposals; snapshot field left unresolved"
            )
    return template, tuple(warnings)


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
    if max_candidates <= 0:
        return (), ()

    fragments: list[_SpanFragment] = []
    max_fragments = max(40, max_candidates * 8)
    for block in _candidate_blocks(text):
        span_id = f"{document.source_accession}:{document.sequence or 0}:s{len(fragments) + 1}"
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
        fragments.append(_SpanFragment(span=span, proposals=proposals, order=len(fragments)))
        if len(fragments) >= max_fragments:
            break

    clustered = _cluster_fragments(document, tuple(fragments))[:max_candidates]
    candidates: list[DebtInstrumentSourceCandidate] = []
    selected_spans: list[InstrumentSourceSpan] = []
    seen_span_ids: set[str] = set()
    for cluster, basis in clustered:
        source_span_ids = tuple(fragment.span.span_id for fragment in cluster)
        proposals = _unique_proposals(
            proposal
            for fragment in cluster
            for proposal in fragment.proposals
        )
        template, template_warnings = _template_for_cluster(document, proposals, source_span_ids)
        for fragment in cluster:
            if fragment.span.span_id not in seen_span_ids:
                selected_spans.append(fragment.span)
                seen_span_ids.add(fragment.span.span_id)

        warnings = ["candidate fields are navigation/extraction aids, not confirmed legal terms"]
        if len(cluster) > 1:
            warnings.append(
                f"clustered {len(cluster)} source spans within one SEC exhibit; verify the shared instrument identity before ledger ingestion"
            )
        warnings.extend(template_warnings)
        candidates.append(
            DebtInstrumentSourceCandidate(
                candidate_id=f"{document.source_accession}:{document.sequence or 0}:c{len(candidates) + 1}",
                source_accession=document.source_accession,
                as_of_date=document.filing_date,
                document_url=document.url,
                document_type=document.document_type,
                document_description=document.description,
                proposals=proposals,
                source_span_ids=source_span_ids,
                snapshot_template=template,
                warnings=tuple(dict.fromkeys(warnings)),
                cluster_status="linked_spans" if len(cluster) > 1 else "single_span",
                cluster_basis=basis,
            )
        )
    return tuple(selected_spans), tuple(candidates)


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
            "When one candidate cites multiple source spans, verify that the cluster evidence actually refers to one legal instrument before retaining merged fields.",
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
            "Delete duplicate candidate rows and verify every retained field and multi-span cluster against source_refs before using this file with distressed-equity-debt-ledger."
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

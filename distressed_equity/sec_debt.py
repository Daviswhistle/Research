from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from html.parser import HTMLParser
import re
from typing import Any, Iterable

from .agents import AgentTask
from .sec import SecClient, SecFiling

BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "tr",
    "td",
    "th",
    "table",
    "section",
    "article",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
}
SKIP_TAGS = {"script", "style", "noscript"}

CATEGORY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "springing_maturity",
        (
            "springing maturity",
            "springing maturities",
            "springing due",
        ),
    ),
    (
        "covenant",
        (
            "covenant",
            "leverage ratio",
            "fixed charge coverage",
            "minimum liquidity",
            "minimum cash",
            "financial maintenance",
        ),
    ),
    (
        "revolver",
        (
            "revolving credit",
            "revolving facility",
            "revolver",
            "credit facility",
            "borrowing base",
            "availability under",
        ),
    ),
    (
        "maturity",
        (
            "maturity",
            "matures",
            "maturing",
            "due on",
            "due in",
            "senior notes",
            "term loan",
            "notes due",
        ),
    ),
    (
        "collateral_seniority",
        (
            "secured by",
            "collateral",
            "first lien",
            "second lien",
            "senior secured",
            "senior unsecured",
            "guaranteed by",
            "guarantor",
        ),
    ),
    (
        "interest",
        (
            "interest rate",
            "per annum",
            "sofr",
            "libor",
            "base rate plus",
        ),
    ),
)

MONEY_RE = re.compile(
    r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(billion|million|thousand|bn|mm|m|b|k)?",
    re.IGNORECASE,
)
YEAR_RE = re.compile(r"\b(20[0-9]{2})\b")
PERCENT_RE = re.compile(r"\b([0-9]+(?:\.[0-9]+)?)\s*%")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth == 0 and tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth == 0 and tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


@dataclass(frozen=True)
class AmountCandidate:
    amount: float
    currency: str = "USD"
    raw: str = ""


@dataclass(frozen=True)
class CapitalStackSnippet:
    snippet_id: str
    category: str
    text: str
    source_url: str
    published_on: date
    form: str
    accession_number: str
    amount_candidates: tuple[AmountCandidate, ...] = ()
    year_candidates: tuple[int, ...] = ()
    percentage_candidates: tuple[float, ...] = ()


@dataclass(frozen=True)
class CapitalStackPacket:
    ticker: str | None
    company_name: str
    analysis_date: date
    snippets: tuple[CapitalStackSnippet, ...]
    source_filings: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]


def html_to_text(document: str) -> str:
    parser = _TextExtractor()
    parser.feed(document)
    parser.close()
    text = parser.text().replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _amount_candidates(text: str) -> tuple[AmountCandidate, ...]:
    multipliers = {
        "billion": 1_000_000_000,
        "bn": 1_000_000_000,
        "b": 1_000_000_000,
        "million": 1_000_000,
        "mm": 1_000_000,
        "m": 1_000_000,
        "thousand": 1_000,
        "k": 1_000,
        None: 1,
        "": 1,
    }
    values: list[AmountCandidate] = []
    for match in MONEY_RE.finditer(text):
        number = float(match.group(1).replace(",", ""))
        suffix = (match.group(2) or "").lower()
        values.append(
            AmountCandidate(
                amount=number * multipliers[suffix],
                raw=match.group(0),
            )
        )
    return tuple(values)


def _categories(text: str) -> tuple[str, ...]:
    lower = text.lower()
    return tuple(
        category
        for category, phrases in CATEGORY_PATTERNS
        if any(phrase in lower for phrase in phrases)
    )


def _candidate_blocks(text: str) -> Iterable[str]:
    # SEC filings frequently flatten tables into line-oriented text. Use a
    # sliding 3-line window so a debt label and its amount/date remain together.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    seen: set[str] = set()
    for idx, line in enumerate(lines):
        window = " ".join(lines[max(0, idx - 1) : min(len(lines), idx + 2)])
        normalized = re.sub(r"\s+", " ", window).strip()
        if len(normalized) < 35 or normalized in seen:
            continue
        seen.add(normalized)
        yield normalized[:1800]


def extract_capital_stack_snippets(
    document: str,
    filing: SecFiling,
    *,
    max_snippets: int = 80,
) -> tuple[CapitalStackSnippet, ...]:
    if max_snippets <= 0:
        return ()
    if not filing.archive_url:
        return ()
    text = html_to_text(document)
    snippets: list[CapitalStackSnippet] = []
    for block in _candidate_blocks(text):
        categories = _categories(block)
        if not categories:
            continue
        years = tuple(dict.fromkeys(int(item) for item in YEAR_RE.findall(block)))
        percentages = tuple(
            dict.fromkeys(float(item) for item in PERCENT_RE.findall(block))
        )
        amounts = _amount_candidates(block)
        for category in categories:
            snippet_id = f"{filing.accession_number}:{len(snippets) + 1}"
            snippets.append(
                CapitalStackSnippet(
                    snippet_id=snippet_id,
                    category=category,
                    text=block,
                    source_url=filing.archive_url,
                    published_on=filing.filing_date,
                    form=filing.form,
                    accession_number=filing.accession_number,
                    amount_candidates=amounts,
                    year_candidates=years,
                    percentage_candidates=percentages,
                )
            )
            if len(snippets) >= max_snippets:
                return tuple(snippets)
    return tuple(snippets)


def fetch_filing_document(client: SecClient, filing: SecFiling) -> str:
    """Fetch one primary filing document through the SEC client's fair-access throttle."""

    url = filing.archive_url
    if not url:
        raise ValueError("filing has no primary-document archive URL")
    client._throttle()  # package-internal reuse of the same fair-access limiter
    response = client.session.get(
        url,
        headers={
            "User-Agent": client.user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def build_capital_stack_packet(
    client: SecClient,
    *,
    ticker: str | None,
    company_name: str,
    analysis_date: date,
    filings: Iterable[SecFiling],
    filing_limit: int = 3,
    max_snippets_per_filing: int = 50,
) -> CapitalStackPacket:
    snippets: list[CapitalStackSnippet] = []
    sources: list[dict[str, Any]] = []
    warnings: list[str] = [
        "Keyword snippets are candidate evidence, not authoritative debt schedule rows.",
        "Amounts/years/percentages are regex candidates and must be tied to the correct instrument by an auditor.",
        "The extractor does not infer refinancing success, covenant headroom, or debt classification from keyword proximity alone.",
    ]
    selected = [
        filing
        for filing in filings
        if filing.form in {"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K"}
        and filing.filing_date <= analysis_date
        and filing.archive_url
    ][:filing_limit]
    for filing in selected:
        try:
            document = fetch_filing_document(client, filing)
            extracted = extract_capital_stack_snippets(
                document,
                filing,
                max_snippets=max_snippets_per_filing,
            )
            snippets.extend(extracted)
            sources.append(
                {
                    "form": filing.form,
                    "filing_date": filing.filing_date.isoformat(),
                    "accession_number": filing.accession_number,
                    "url": filing.archive_url,
                    "snippet_count": len(extracted),
                }
            )
        except Exception as exc:  # network/document failures should remain visible, not abort all sources
            warnings.append(
                f"failed to extract {filing.form} {filing.accession_number}: {exc}"
            )
    return CapitalStackPacket(
        ticker=ticker,
        company_name=company_name,
        analysis_date=analysis_date,
        snippets=tuple(snippets),
        source_filings=tuple(sources),
        warnings=tuple(warnings),
    )


def build_capital_stack_agent_task(packet: CapitalStackPacket) -> AgentTask:
    category_counts: dict[str, int] = {}
    for snippet in packet.snippets:
        category_counts[snippet.category] = category_counts.get(snippet.category, 0) + 1
    context = ", ".join(f"{key}={value}" for key, value in sorted(category_counts.items())) or "no snippets"
    return AgentTask(
        name="capital_stack_extractor",
        objective=(
            f"Turn the SEC capital-stack snippet packet for {packet.company_name} into a validated point-in-time capital stack. "
            "Use the snippets only as navigation/evidence; open the source filing when context is ambiguous."
        ),
        guardrails=(
            f"Do not use information published after {packet.analysis_date.isoformat()}.",
            "Return structured agent-result schema_version=1.",
            "Every patch must cite one or more evidence_id values from claims you record.",
            "Do not convert regex amount/year candidates into a debt row unless the surrounding filing text identifies the instrument.",
            "Do not assume a maturity will refinance merely because the company later survived.",
            "Keep lease, debt, preferred, earn-out, TRA and contingent claims distinct to avoid double counting.",
            f"Snippet inventory: {context}.",
        ),
        required_output=(
            "Unrestricted cash and restricted cash",
            "Revolver commitment, drawn amount and actually available capacity",
            "Debt obligations with instrument name, amount, maturity timing, seniority, security and refinancing requirement",
            "Covenants/springing maturities with breach trigger, cure path and timing",
            "Other senior/contingent claims and potential dilution",
            "Unresolved ambiguities rather than guessed values",
        ),
    )


def capital_stack_packet_to_dict(packet: CapitalStackPacket) -> dict[str, Any]:
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

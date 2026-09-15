from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import os
import threading
import time
from typing import Any, Callable, Iterable

import requests

from .evidence import EvidenceRecord

SEC_DATA_BASE = "https://data.sec.gov"
SEC_WEB_BASE = "https://www.sec.gov"
DEFAULT_FORMS = ("10-K", "10-Q", "8-K", "10-K/A", "10-Q/A")


@dataclass(frozen=True)
class SecFiling:
    cik: str
    accession_number: str
    filing_date: date
    form: str
    primary_document: str | None = None
    report_date: date | None = None
    acceptance_datetime: str | None = None
    items: str | None = None

    @property
    def archive_url(self) -> str | None:
        if not self.primary_document:
            return None
        accession = self.accession_number.replace("-", "")
        return (
            f"{SEC_WEB_BASE}/Archives/edgar/data/{int(self.cik)}/"
            f"{accession}/{self.primary_document}"
        )


@dataclass(frozen=True)
class SecFact:
    metric: str
    namespace: str
    tag: str
    label: str | None
    value: float
    unit: str
    filed: date
    period_end: date
    period_start: date | None = None
    form: str | None = None
    accession_number: str | None = None
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    frame: str | None = None


@dataclass(frozen=True)
class SecPointInTimeSnapshot:
    ticker: str | None
    cik: str
    company_name: str
    analysis_date: date
    filings: tuple[SecFiling, ...]
    facts: dict[str, SecFact]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class FactAlias:
    namespace: str
    tag: str
    preferred_units: tuple[str, ...] = ("USD",)


FACT_ALIASES: dict[str, tuple[FactAlias, ...]] = {
    "revenue": (
        FactAlias("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        FactAlias("us-gaap", "Revenues"),
        FactAlias("us-gaap", "SalesRevenueNet"),
    ),
    "operating_income": (FactAlias("us-gaap", "OperatingIncomeLoss"),),
    "cash": (
        FactAlias("us-gaap", "CashAndCashEquivalentsAtCarryingValue"),
        FactAlias("us-gaap", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
    ),
    "operating_cash_flow": (
        FactAlias("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
    ),
    "capex": (FactAlias("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),),
    "debt_current": (
        FactAlias("us-gaap", "LongTermDebtCurrent"),
        FactAlias("us-gaap", "LongTermDebtAndFinanceLeaseObligationsCurrent"),
        FactAlias("us-gaap", "ShortTermBorrowings"),
    ),
    "debt_noncurrent": (
        FactAlias("us-gaap", "LongTermDebtNoncurrent"),
        FactAlias("us-gaap", "LongTermDebtAndFinanceLeaseObligationsNoncurrent"),
    ),
    "shares_outstanding": (
        FactAlias("dei", "EntityCommonStockSharesOutstanding", ("shares",)),
    ),
}


def normalize_cik(cik: str | int) -> str:
    digits = "".join(ch for ch in str(cik) if ch.isdigit())
    if not digits:
        raise ValueError("CIK must contain digits")
    if len(digits) > 10:
        raise ValueError("CIK must be at most 10 digits")
    return digits.zfill(10)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value[:10])


def _point_in_time_company_name(
    submissions: dict[str, Any],
    analysis_date: date,
    fallback: str,
) -> tuple[str, bool]:
    """Return a cutoff-safe name using submissions metadata only.

    Today's `submissions.name` can be a post-cutoff rename. If former-name
    metadata shows a future boundary, use the former name active at the cutoff
    when one is explicit; otherwise withhold today's name and fall back to CIK.
    Richer complete-submission header reconciliation happens in the legal-name
    layer used by orchestration.
    """

    current_name = str(submissions.get("name") or "").strip()
    former = submissions.get("formerNames") or []
    parsed: list[tuple[str, date | None, date | None]] = []
    if isinstance(former, list):
        for row in former:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            try:
                start = _parse_date(row.get("from"))
                end = _parse_date(row.get("to"))
            except ValueError:
                continue
            if start and end and end < start:
                continue
            parsed.append((name, start, end))

    active = [
        item
        for item in parsed
        if (item[1] is None or item[1] <= analysis_date)
        and (item[2] is None or analysis_date < item[2])
    ]
    if active:
        return max(active, key=lambda item: item[1] or date.min)[0], True

    future_boundary = any(
        (start is not None and start > analysis_date)
        or (end is not None and end > analysis_date)
        for _name, start, end in parsed
    )
    if future_boundary:
        return fallback, True
    return current_name or fallback, False


def _columnar_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    if not data:
        return []
    lengths = [len(value) for value in data.values() if isinstance(value, list)]
    if not lengths:
        return []
    count = min(lengths)
    return [
        {
            key: value[idx]
            for key, value in data.items()
            if isinstance(value, list) and idx < len(value)
        }
        for idx in range(count)
    ]


def _filing_from_row(cik: str, row: dict[str, Any]) -> SecFiling | None:
    accession = row.get("accessionNumber")
    filing_date = _parse_date(row.get("filingDate"))
    form = row.get("form")
    if not accession or filing_date is None or not form:
        return None
    return SecFiling(
        cik=cik,
        accession_number=str(accession),
        filing_date=filing_date,
        form=str(form),
        primary_document=(str(row["primaryDocument"]) if row.get("primaryDocument") else None),
        report_date=_parse_date(row.get("reportDate")),
        acceptance_datetime=(str(row["acceptanceDateTime"]) if row.get("acceptanceDateTime") else None),
        items=(str(row["items"]) if row.get("items") else None),
    )


def _fact_rows(
    companyfacts: dict[str, Any], alias: FactAlias
) -> tuple[str | None, list[tuple[int, str, dict[str, Any]]]]:
    concept = companyfacts.get("facts", {}).get(alias.namespace, {}).get(alias.tag)
    if not isinstance(concept, dict):
        return None, []
    units = concept.get("units", {})
    if not isinstance(units, dict):
        return concept.get("label"), []
    ordered_units = list(alias.preferred_units) + [
        key for key in units if key not in alias.preferred_units
    ]
    rows: list[tuple[int, str, dict[str, Any]]] = []
    for unit_rank, unit in enumerate(ordered_units):
        values = units.get(unit)
        if isinstance(values, list):
            rows.extend(
                (unit_rank, unit, value)
                for value in values
                if isinstance(value, dict)
            )
    return concept.get("label"), rows


def select_point_in_time_fact(
    companyfacts: dict[str, Any],
    metric: str,
    analysis_date: date,
    forms: Iterable[str] = DEFAULT_FORMS,
) -> SecFact | None:
    """Select the newest standardized fact that was actually knowable by cutoff.

    Alias priority is only a tie-breaker. This prevents a formerly used preferred
    US-GAAP tag from hiding a newer period reported under another standard alias.
    """

    allowed_forms = set(forms)
    candidates: list[
        tuple[date, date, date, int, int, FactAlias, str | None, str, dict[str, Any]]
    ] = []
    for alias_rank, alias in enumerate(FACT_ALIASES.get(metric, ())):
        label, rows = _fact_rows(companyfacts, alias)
        for unit_rank, unit, row in rows:
            filed = _parse_date(row.get("filed"))
            end = _parse_date(row.get("end"))
            start = _parse_date(row.get("start")) or date.min
            form = row.get("form")
            value = row.get("val")
            if filed is None or end is None:
                continue
            if filed > analysis_date or end > analysis_date:
                continue
            if form and allowed_forms and form not in allowed_forms:
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            candidates.append(
                (end, filed, start, -alias_rank, -unit_rank, alias, label, unit, row)
            )
    if not candidates:
        return None

    end, filed, start, _alias_rank, _unit_rank, alias, label, unit, row = max(
        candidates, key=lambda item: item[:5]
    )
    return SecFact(
        metric=metric,
        namespace=alias.namespace,
        tag=alias.tag,
        label=label,
        value=float(row["val"]),
        unit=unit,
        filed=filed,
        period_end=end,
        period_start=(None if start == date.min else start),
        form=(str(row["form"]) if row.get("form") else None),
        accession_number=(str(row["accn"]) if row.get("accn") else None),
        fiscal_year=(int(row["fy"]) if isinstance(row.get("fy"), int) else None),
        fiscal_period=(str(row["fp"]) if row.get("fp") else None),
        frame=(str(row["frame"]) if row.get("frame") else None),
    )


class SecClient:
    """Small, fair-access SEC client for historical point-in-time snapshots."""

    def __init__(
        self,
        user_agent: str | None = None,
        *,
        session: requests.Session | Any | None = None,
        min_interval_seconds: float = 0.12,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.user_agent = (user_agent or os.getenv("SEC_USER_AGENT", "")).strip()
        if not self.user_agent:
            raise ValueError(
                "SEC_USER_AGENT is required; identify your application and provide contact information"
            )
        if min_interval_seconds < 0.1:
            raise ValueError(
                "min_interval_seconds must be at least 0.1 to respect SEC fair-access limits"
            )
        self.session = session or requests.Session()
        self.min_interval_seconds = min_interval_seconds
        self.clock = clock
        self.sleeper = sleeper
        self._last_request_at: float | None = None
        self._lock = threading.Lock()
        self._ticker_map: dict[str, tuple[str, str]] | None = None

    def _throttle(self) -> None:
        with self._lock:
            now = self.clock()
            if self._last_request_at is not None:
                wait = self.min_interval_seconds - (now - self._last_request_at)
                if wait > 0:
                    self.sleeper(wait)
                    now = self.clock()
            self._last_request_at = now

    def _get_json(self, url: str) -> dict[str, Any]:
        self._throttle()
        response = self.session.get(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json",
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"SEC endpoint returned non-object JSON: {url}")
        return payload

    def ticker_map(self, *, refresh: bool = False) -> dict[str, tuple[str, str]]:
        if self._ticker_map is not None and not refresh:
            return self._ticker_map
        payload = self._get_json(f"{SEC_WEB_BASE}/files/company_tickers.json")
        mapping: dict[str, tuple[str, str]] = {}
        for row in payload.values():
            if (
                not isinstance(row, dict)
                or not row.get("ticker")
                or row.get("cik_str") is None
            ):
                continue
            ticker = str(row["ticker"]).upper()
            mapping[ticker] = (
                normalize_cik(row["cik_str"]),
                str(row.get("title", "")),
            )
        self._ticker_map = mapping
        return mapping

    def resolve_ticker(self, ticker: str) -> tuple[str, str]:
        key = ticker.upper().strip()
        try:
            return self.ticker_map()[key]
        except KeyError as exc:
            raise KeyError(f"ticker not found in SEC mapping: {ticker}") from exc

    def submissions(self, cik: str | int) -> dict[str, Any]:
        cik10 = normalize_cik(cik)
        return self._get_json(f"{SEC_DATA_BASE}/submissions/CIK{cik10}.json")

    def companyfacts(self, cik: str | int) -> dict[str, Any]:
        cik10 = normalize_cik(cik)
        return self._get_json(
            f"{SEC_DATA_BASE}/api/xbrl/companyfacts/CIK{cik10}.json"
        )

    def _filings_from_submissions_payload(
        self,
        cik10: str,
        payload: dict[str, Any],
        analysis_date: date,
        forms: Iterable[str],
    ) -> tuple[SecFiling, ...]:
        rows = _columnar_rows(payload.get("filings", {}).get("recent", {}))
        for history in payload.get("filings", {}).get("files", []):
            if not isinstance(history, dict) or not history.get("name"):
                continue
            start = _parse_date(history.get("filingFrom"))
            if start is not None and start > analysis_date:
                continue
            history_payload = self._get_json(
                f"{SEC_DATA_BASE}/submissions/{history['name']}"
            )
            rows.extend(_columnar_rows(history_payload))

        allowed = set(forms)
        filings: list[SecFiling] = []
        seen: set[str] = set()
        for row in rows:
            filing = _filing_from_row(cik10, row)
            if filing is None or filing.filing_date > analysis_date:
                continue
            if allowed and filing.form not in allowed:
                continue
            if filing.accession_number in seen:
                continue
            seen.add(filing.accession_number)
            filings.append(filing)
        filings.sort(
            key=lambda item: (item.filing_date, item.accession_number), reverse=True
        )
        return tuple(filings)

    def filings_as_of(
        self,
        cik: str | int,
        analysis_date: date,
        forms: Iterable[str] = DEFAULT_FORMS,
    ) -> tuple[SecFiling, ...]:
        cik10 = normalize_cik(cik)
        payload = self.submissions(cik10)
        return self._filings_from_submissions_payload(
            cik10, payload, analysis_date, forms
        )

    def snapshot(
        self,
        *,
        analysis_date: date,
        ticker: str | None = None,
        cik: str | int | None = None,
        forms: Iterable[str] = DEFAULT_FORMS,
        filing_limit: int = 20,
    ) -> SecPointInTimeSnapshot:
        if (ticker is None) == (cik is None):
            raise ValueError("provide exactly one of ticker or cik")
        if filing_limit < 0:
            raise ValueError("filing_limit cannot be negative")

        resolved_name = ""
        resolved_ticker = ticker.upper().strip() if ticker else None
        if ticker is not None:
            cik10, resolved_name = self.resolve_ticker(ticker)
        else:
            cik10 = normalize_cik(cik or "")

        submissions = self.submissions(cik10)
        company_name, name_was_historical_or_withheld = _point_in_time_company_name(
            submissions,
            analysis_date,
            cik10,
        )
        filings = self._filings_from_submissions_payload(
            cik10, submissions, analysis_date, forms
        )
        facts_payload = self.companyfacts(cik10)
        facts: dict[str, SecFact] = {}
        missing: list[str] = []
        for metric in FACT_ALIASES:
            fact = select_point_in_time_fact(
                facts_payload, metric, analysis_date, forms
            )
            if fact is None:
                missing.append(metric)
            else:
                facts[metric] = fact

        warnings = [
            "SEC companyfacts uses standardized XBRL concepts; company-specific extension facts may be absent.",
            "Duration facts such as revenue, CFO and capex are raw reported periods, not normalized quarterly values.",
            "SEC ticker/CIK association files are a lookup aid and SEC does not guarantee their accuracy or scope.",
        ]
        if name_was_historical_or_withheld:
            warnings.append(
                "SEC current company name was not blindly backfilled across the historical cutoff; former-name metadata or CIK fallback was used."
            )
        if missing:
            warnings.append(
                "No standardized point-in-time fact found for: " + ", ".join(missing)
            )
        return SecPointInTimeSnapshot(
            ticker=resolved_ticker,
            cik=cik10,
            company_name=company_name,
            analysis_date=analysis_date,
            filings=filings[:filing_limit],
            facts=facts,
            warnings=tuple(warnings),
        )


def snapshot_to_evidence(
    snapshot: SecPointInTimeSnapshot,
) -> tuple[EvidenceRecord, ...]:
    evidence: list[EvidenceRecord] = []
    for filing in snapshot.filings:
        evidence.append(
            EvidenceRecord(
                claim=f"{snapshot.company_name} filed {filing.form}",
                source="SEC EDGAR filing",
                published_on=filing.filing_date,
                evidence_type="fact",
                event_on=filing.report_date,
                locator=filing.archive_url,
                notes=filing.accession_number,
            )
        )
    for metric, fact in snapshot.facts.items():
        evidence.append(
            EvidenceRecord(
                claim=f"{metric}={fact.value:g} {fact.unit} ({fact.tag})",
                source="SEC XBRL companyfacts",
                published_on=fact.filed,
                evidence_type="fact",
                event_on=fact.period_end,
                locator=(
                    f"{SEC_WEB_BASE}/Archives/edgar/data/{int(snapshot.cik)}/"
                    f"{fact.accession_number.replace('-', '')}/"
                    if fact.accession_number
                    else None
                ),
                notes=(
                    f"{fact.namespace}:{fact.tag}; form={fact.form}; "
                    f"start={fact.period_start}; end={fact.period_end}"
                ),
            )
        )
    return tuple(evidence)


def snapshot_to_dict(snapshot: SecPointInTimeSnapshot) -> dict[str, Any]:
    def convert(value: Any) -> Any:
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, tuple):
            return [convert(item) for item in value]
        if isinstance(value, list):
            return [convert(item) for item in value]
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        return value

    return convert(asdict(snapshot))

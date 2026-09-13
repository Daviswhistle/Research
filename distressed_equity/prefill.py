from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

from .agents import AgentTask
from .sec import SecFact, SecPointInTimeSnapshot


def _fact_dict(fact: SecFact | None) -> dict[str, Any] | None:
    if fact is None:
        return None
    payload = asdict(fact)
    for key in ("filed", "period_end", "period_start"):
        if isinstance(payload.get(key), date):
            payload[key] = payload[key].isoformat()
    return payload


def _latest_filing(snapshot: SecPointInTimeSnapshot, forms: tuple[str, ...]):
    return next((filing for filing in snapshot.filings if filing.form in forms), None)


def _matching_raw_fcf_context(snapshot: SecPointInTimeSnapshot) -> dict[str, Any] | None:
    cfo = snapshot.facts.get("operating_cash_flow")
    capex = snapshot.facts.get("capex")
    if cfo is None or capex is None:
        return None
    if (
        cfo.unit != capex.unit
        or cfo.period_start != capex.period_start
        or cfo.period_end != capex.period_end
    ):
        return None
    return {
        "value": cfo.value - capex.value,
        "unit": cfo.unit,
        "period_start": cfo.period_start.isoformat() if cfo.period_start else None,
        "period_end": cfo.period_end.isoformat(),
        "label": "reported CFO minus reported capex; not normalized owner cash flow",
    }


def _gross_debt_context(snapshot: SecPointInTimeSnapshot) -> dict[str, Any] | None:
    current = snapshot.facts.get("debt_current")
    noncurrent = snapshot.facts.get("debt_noncurrent")
    if current is None and noncurrent is None:
        return None
    facts = [fact for fact in (current, noncurrent) if fact is not None]
    if len(facts) == 2 and (
        facts[0].unit != facts[1].unit or facts[0].period_end != facts[1].period_end
    ):
        return {
            "value": None,
            "reason": "current and noncurrent debt facts do not share a period end/unit",
            "components": [_fact_dict(fact) for fact in facts],
        }
    return {
        "value": sum(fact.value for fact in facts),
        "unit": facts[0].unit,
        "period_end": facts[0].period_end.isoformat(),
        "components": [_fact_dict(fact) for fact in facts],
        "caveat": "standardized debt concepts can differ in lease inclusion and do not replace a note-level capital-stack audit",
    }


def build_screening_draft(snapshot: SecPointInTimeSnapshot) -> dict[str, Any]:
    """Create a deliberately incomplete scanner seed from safe SEC facts.

    Nulls are intentional. The draft is not accepted by `screening_candidate_from_dict`
    until market, liquidity, recovery and scenario fields are researched.
    """

    shares = snapshot.facts.get("shares_outstanding")
    cash = snapshot.facts.get("cash")
    revenue = snapshot.facts.get("revenue")
    operating_income = snapshot.facts.get("operating_income")

    pure_cash = (
        cash
        if cash is not None and cash.tag == "CashAndCashEquivalentsAtCarryingValue"
        else None
    )
    latest_10k = _latest_filing(snapshot, ("10-K", "10-K/A"))
    latest_10q = _latest_filing(snapshot, ("10-Q", "10-Q/A"))
    recent_8k = [filing for filing in snapshot.filings if filing.form == "8-K"][:5]

    draft = {
        "ticker": snapshot.ticker,
        "company_name": snapshot.company_name,
        "analysis_date": snapshot.analysis_date.isoformat(),
        "capital_structure": {
            "current_price": None,
            "current_shares": shares.value if shares else None,
            "net_debt": None,
            "other_senior_claims": None,
        },
        "peak_market_cap": None,
        "current_enterprise_value": None,
        "ttm_revenue": None,
        "has_real_customers": None,
        "is_pre_revenue": (False if revenue is not None and revenue.value > 0 else None),
        "has_historical_operating_profit": (
            True if operating_income is not None and operating_income.value > 0 else None
        ),
        "starting_liquidity": pure_cash.value if pure_cash else None,
        "available_credit": None,
        "asset_monetization": None,
        "restricted_cash": None,
        "monthly_cash_burn": None,
        "recovery_month": None,
        "debt_obligations": [],
        "covenants": [],
        "base_scenario": None,
        "reference_scenario": None,
        "downside_scenario": None,
        "critical_assumptions_complete": False,
    }

    unresolved = [
        "point-in-time share price and peak market capitalization",
        "enterprise value and net debt after note-level capital-stack reconciliation",
        "TTM revenue and owner-cash-flow normalization",
        "real-customer/revenue-quality verification",
        "available revolver, restricted cash and realizable asset monetization",
        "debt maturity, springing maturity and covenant schedule",
        "monthly burn/runway and plausible recovery window",
        "bottom-up downside/reference/base scenarios",
        "independent critical-assumption inventory",
    ]
    if shares is None:
        unresolved.append("fully diluted/common shares outstanding at the cutoff")
    if pure_cash is None:
        unresolved.append("unrestricted cash available to common-equity survival")

    return {
        "identity": {
            "ticker": snapshot.ticker,
            "cik": snapshot.cik,
            "company_name": snapshot.company_name,
            "analysis_date": snapshot.analysis_date.isoformat(),
        },
        "screening_candidate_draft": draft,
        "reported_context": {
            "revenue": _fact_dict(revenue),
            "operating_income": _fact_dict(operating_income),
            "cash": _fact_dict(cash),
            "gross_debt_context": _gross_debt_context(snapshot),
            "operating_cash_flow": _fact_dict(snapshot.facts.get("operating_cash_flow")),
            "capex": _fact_dict(snapshot.facts.get("capex")),
            "raw_fcf_context": _matching_raw_fcf_context(snapshot),
            "shares_outstanding": _fact_dict(shares),
        },
        "filing_context": {
            "latest_10k": (
                {"filing_date": latest_10k.filing_date.isoformat(), "url": latest_10k.archive_url}
                if latest_10k
                else None
            ),
            "latest_10q": (
                {"filing_date": latest_10q.filing_date.isoformat(), "url": latest_10q.archive_url}
                if latest_10q
                else None
            ),
            "recent_8k": [
                {"filing_date": filing.filing_date.isoformat(), "url": filing.archive_url}
                for filing in recent_8k
            ],
        },
        "unresolved_required_fields": unresolved,
        "warnings": list(snapshot.warnings),
    }


def build_sec_prefill_tasks(snapshot: SecPointInTimeSnapshot) -> tuple[AgentTask, ...]:
    filing_urls = [filing.archive_url for filing in snapshot.filings if filing.archive_url]
    source_note = "Point-in-time SEC filings: " + ", ".join(filing_urls[:8])
    guardrails = (
        f"Do not use information published after {snapshot.analysis_date.isoformat()}.",
        "Separate facts, estimates, inferences and unknowns.",
        "Every material claim must include source, publication date and event/effective date.",
        "Do not infer refinancing success merely because the company later survived.",
        "Do not create a success probability before the causal path and base rates are researched.",
        source_note,
    )
    return (
        AgentTask(
            name="capital_stack_extractor",
            objective=f"Reconstruct {snapshot.company_name}'s capital stack and liquidity exactly as knowable at the cutoff.",
            guardrails=guardrails,
            required_output=(
                "Cash, restricted cash and revolver availability",
                "Debt tranches, maturity dates, coupons, collateral and seniority",
                "Covenants, springing maturities, minimum-liquidity tests and cure mechanics",
                "Lease, preferred, earn-out, TRA and other senior/contingent claims",
                "Potential dilution sources and fully diluted share count",
            ),
        ),
        AgentTask(
            name="historical_market_reconstructor",
            objective="Reconstruct the market-implied distress at the analysis date without using later prices.",
            guardrails=guardrails,
            required_output=(
                "Cutoff-date share price and market capitalization",
                "Prior peak market capitalization and drawdown",
                "Point-in-time enterprise value using reconciled net debt",
                "Bond prices/yields or other market distress evidence if available then",
            ),
        ),
        AgentTask(
            name="impairment_and_normalization_researcher",
            objective="Explain why the business deteriorated and rebuild normalized owner economics from the bottom up.",
            guardrails=guardrails,
            required_output=(
                "Temporary/cyclical versus structural/permanent driver table",
                "Revenue quality, customers, volume/price and unit-economics evidence",
                "Maintenance capex, interest, cash lease, tax and working-capital normalization",
                "Plausible recovery window with observable falsification triggers",
                "Independent critical assumptions required for a base recovery",
            ),
        ),
    )

from datetime import date

from distressed_equity.prefill import build_screening_draft, build_sec_prefill_tasks
from distressed_equity.sec import SecFact, SecFiling, SecPointInTimeSnapshot


def fact(metric, tag, value, *, start=None, end="2022-09-30", unit="USD"):
    return SecFact(
        metric=metric,
        namespace="us-gaap" if metric != "shares_outstanding" else "dei",
        tag=tag,
        label=metric,
        value=value,
        unit=unit,
        filed=date(2022, 11, 4),
        period_end=date.fromisoformat(end),
        period_start=date.fromisoformat(start) if start else None,
        form="10-Q",
        accession_number="0001690820-22-000003",
    )


def snapshot_with_facts(cash_tag="CashAndCashEquivalentsAtCarryingValue"):
    return SecPointInTimeSnapshot(
        ticker="TEST",
        cik="0001690820",
        company_name="Test Company",
        analysis_date=date(2022, 12, 31),
        filings=(
            SecFiling(
                cik="0001690820",
                accession_number="0001690820-22-000003",
                filing_date=date(2022, 11, 4),
                form="10-Q",
                primary_document="q3.htm",
                report_date=date(2022, 9, 30),
            ),
            SecFiling(
                cik="0001690820",
                accession_number="0001690820-22-000001",
                filing_date=date(2022, 2, 15),
                form="10-K",
                primary_document="annual.htm",
                report_date=date(2021, 12, 31),
            ),
        ),
        facts={
            "revenue": fact("revenue", "Revenues", 400, start="2022-07-01"),
            "operating_income": fact("operating_income", "OperatingIncomeLoss", 10, start="2022-07-01"),
            "cash": fact("cash", cash_tag, 150),
            "debt_current": fact("debt_current", "LongTermDebtCurrent", 50),
            "debt_noncurrent": fact("debt_noncurrent", "LongTermDebtNoncurrent", 300),
            "operating_cash_flow": fact(
                "operating_cash_flow", "NetCashProvidedByUsedInOperatingActivities", 100, start="2022-01-01"
            ),
            "capex": fact("capex", "PaymentsToAcquirePropertyPlantAndEquipment", 40, start="2022-01-01"),
            "shares_outstanding": fact(
                "shares_outstanding", "EntityCommonStockSharesOutstanding", 90, unit="shares"
            ),
        },
        warnings=(),
    )


def test_prefill_only_promotes_safe_fields():
    seed = build_screening_draft(snapshot_with_facts())
    draft = seed["screening_candidate_draft"]
    assert draft["capital_structure"]["current_shares"] == 90
    assert draft["capital_structure"]["current_price"] is None
    assert draft["capital_structure"]["net_debt"] is None
    assert draft["starting_liquidity"] == 150
    assert draft["ttm_revenue"] is None
    assert draft["is_pre_revenue"] is False
    assert draft["has_historical_operating_profit"] is True
    assert seed["reported_context"]["gross_debt_context"]["value"] == 350
    assert seed["reported_context"]["raw_fcf_context"]["value"] == 60
    assert "point-in-time share price and peak market capitalization" in seed["unresolved_required_fields"]


def test_restricted_cash_style_tag_is_not_promoted_to_available_liquidity():
    seed = build_screening_draft(
        snapshot_with_facts("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents")
    )
    assert seed["screening_candidate_draft"]["starting_liquidity"] is None
    assert "unrestricted cash available to common-equity survival" in seed["unresolved_required_fields"]


def test_sec_prefill_tasks_keep_historical_cutoff_and_refinancing_guardrail():
    tasks = build_sec_prefill_tasks(snapshot_with_facts())
    assert [task.name for task in tasks] == [
        "capital_stack_extractor",
        "historical_market_reconstructor",
        "impairment_and_normalization_researcher",
    ]
    all_guardrails = " ".join(item for task in tasks for item in task.guardrails)
    assert "2022-12-31" in all_guardrails
    assert "refinancing" in all_guardrails.lower()

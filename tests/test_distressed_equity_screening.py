from datetime import date

from distressed_equity.agents import build_screening_tasks
from distressed_equity.evidence import EvidenceRecord, validate_point_in_time
from distressed_equity.io import screening_candidate_from_dict
from distressed_equity.screening import screen_candidate, screen_universe


def candidate_raw(**overrides):
    raw = {
        "ticker": "CVX",
        "company_name": "Convex Co",
        "capital_structure": {"current_price": 2, "current_shares": 100, "net_debt": 100},
        "peak_market_cap": 1000,
        "current_enterprise_value": 300,
        "ttm_revenue": 1200,
        "has_real_customers": True,
        "is_pre_revenue": False,
        "has_historical_operating_profit": True,
        "starting_liquidity": 200,
        "monthly_cash_burn": 5,
        "recovery_month": 18,
        "base_scenario": {
            "enterprise_value": 1200,
            "exit_net_debt": 200,
            "critical_assumptions": ["customer losses stop", "gross margin normalizes"],
        },
        "reference_scenario": {"enterprise_value": 600, "exit_net_debt": 250},
        "downside_scenario": {"enterprise_value": 150, "exit_net_debt": 150},
        "critical_assumptions_complete": True,
        "target_cagr": 0.15,
        "horizon_years": 3,
        "analysis_date": "2026-09-14",
    }
    raw.update(overrides)
    return raw


def test_clean_convex_candidate_reaches_deep_dive():
    result = screen_candidate(screening_candidate_from_dict(candidate_raw()))
    assert result.priority == "DEEP_DIVE_CANDIDATE"
    assert result.base_equity_multiple == 5.0
    assert result.critical_assumption_count == 2
    assert result.required_probability is not None
    assert result.required_probability < 0.4
    assert result.common_equity_capture_ratio is not None


def test_recovery_after_liquidity_exhaustion_is_dropped():
    result = screen_candidate(
        screening_candidate_from_dict(
            candidate_raw(starting_liquidity=30, monthly_cash_burn=5, recovery_month=18)
        )
    )
    assert result.survival_gate == "FAIL"
    assert result.time_to_death_months == 7
    assert result.priority == "DROP"


def test_pre_recovery_refinancing_routes_to_research_not_false_pass():
    raw = candidate_raw(
        debt_obligations=[
            {
                "name": "term loan",
                "amount": 500,
                "due_month": 12,
                "refinancing_required": True,
                "cash_payment_required": False,
            }
        ]
    )
    candidate = screening_candidate_from_dict(raw)
    result = screen_candidate(candidate)
    assert result.survival_gate == "RESEARCH"
    assert result.priority == "RESEARCH"
    tasks = build_screening_tasks(candidate, result)
    assert any(task.name == "survival_auditor" for task in tasks)
    assert any(task.name == "capital_stack_auditor" for task in tasks)


def test_missing_assumption_inventory_stays_unknown():
    raw = candidate_raw(critical_assumptions_complete=False)
    result = screen_candidate(screening_candidate_from_dict(raw))
    assert result.assumption_gate == "UNKNOWN"
    assert result.priority == "RESEARCH"


def test_screen_universe_orders_deep_dive_before_research_and_drop():
    deep = screening_candidate_from_dict(candidate_raw(ticker="DEEP"))
    research = screening_candidate_from_dict(
        candidate_raw(
            ticker="RESEARCH",
            critical_assumptions_complete=False,
        )
    )
    drop = screening_candidate_from_dict(
        candidate_raw(
            ticker="DROP",
            base_scenario={
                "enterprise_value": 500,
                "exit_net_debt": 200,
                "critical_assumptions": ["one"],
            },
        )
    )
    results = screen_universe((drop, research, deep))
    assert [result.ticker for result in results] == ["DEEP", "RESEARCH", "DROP"]


def test_future_evidence_is_rejected_in_point_in_time_replay():
    records = (
        EvidenceRecord(
            claim="refinancing completed",
            source="8-K",
            published_on=date(2023, 1, 15),
            evidence_type="fact",
        ),
    )
    violations = validate_point_in_time(records, date(2022, 12, 31))
    assert len(violations) == 1
    assert "future information" in violations[0]

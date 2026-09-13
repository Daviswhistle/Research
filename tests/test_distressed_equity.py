from distressed_equity.engine import analyze_case, required_probability
from distressed_equity.io import case_from_dict


def sample_case():
    return case_from_dict(
        {
            "ticker": "TEST",
            "company_name": "Test Co",
            "capital_structure": {"current_price": 3, "current_shares": 100},
            "liquidity": {
                "starting_liquidity": 100,
                "monthly_free_cash_flow": [-10, -10, -10, -10, -10, -10, 5, 5, 5, 5, 5, 5],
                "mandatory_payments": {"4": 20},
                "recovery_month": 6,
            },
            "scenarios": [
                {"name": "distress", "enterprise_value": 20, "exit_net_debt": 20},
                {"name": "weak", "enterprise_value": 250, "exit_net_debt": 100},
                {
                    "name": "base",
                    "enterprise_value": 1000,
                    "exit_net_debt": 100,
                    "critical_assumptions": ["customer losses stop", "margin normalizes"],
                },
                {"name": "strong", "enterprise_value": 2000, "exit_net_debt": 100},
            ],
            "probability_view": {
                "target_cagr": 0.15,
                "horizon_years": 3,
                "success_scenario": "base",
                "downside_scenario": "distress",
                "reasonable_probability_low": 0.55,
                "reasonable_probability_high": 0.7,
            },
        }
    )


def test_required_probability_matches_inverse_logic():
    target = 1.15**3
    assert required_probability(3.0, 0.0, target) == target / 3.0


def test_liquidity_and_verdict():
    result = analyze_case(sample_case())
    assert result.time_to_death_months is None
    assert result.survives_to_recovery_window is True
    assert result.scenarios["base"].equity_multiple == 3.0
    assert result.required_probability is not None
    assert result.verdict.startswith("DEEP DIVE")


def test_dilution_reduces_existing_holder_value():
    raw = {
        "ticker": "DIL",
        "company_name": "Dilution Co",
        "capital_structure": {"current_price": 1, "current_shares": 100},
        "liquidity": {"starting_liquidity": 10, "monthly_free_cash_flow": [0], "recovery_month": 1},
        "scenarios": [
            {"name": "distress", "enterprise_value": 0, "exit_net_debt": 0},
            {"name": "weak", "enterprise_value": 100, "exit_net_debt": 0},
            {
                "name": "base",
                "enterprise_value": 200,
                "exit_net_debt": 0,
                "financing": {"capital_raised": 100, "issue_price": 1},
            },
        ],
        "probability_view": {"target_cagr": 0, "horizon_years": 1},
    }
    result = analyze_case(case_from_dict(raw))
    assert result.scenarios["base"].existing_holder_fraction == 0.5
    assert result.scenarios["base"].existing_common_value == 100
    assert result.scenarios["base"].equity_multiple == 1.0


def test_time_to_death_blocks_case_before_recovery():
    case = sample_case()
    raw = {
        "ticker": case.ticker,
        "company_name": case.company_name,
        "capital_structure": {"current_price": 3, "current_shares": 100},
        "liquidity": {
            "starting_liquidity": 15,
            "monthly_free_cash_flow": [-10, -10, -10],
            "recovery_month": 3,
        },
        "scenarios": [
            {"name": "distress", "enterprise_value": 0, "exit_net_debt": 0},
            {"name": "weak", "enterprise_value": 400, "exit_net_debt": 0},
            {"name": "base", "enterprise_value": 1500, "exit_net_debt": 0},
        ],
        "probability_view": {"target_cagr": 0.15, "horizon_years": 3},
    }
    result = analyze_case(case_from_dict(raw))
    assert result.time_to_death_months == 2
    assert result.survives_to_recovery_window is False
    assert result.verdict.startswith("PASS")


def test_recovery_beyond_forecast_horizon_is_unresolved():
    raw = {
        "ticker": "HORIZON",
        "company_name": "Horizon Co",
        "capital_structure": {"current_price": 1, "current_shares": 100},
        "liquidity": {"starting_liquidity": 100, "monthly_free_cash_flow": [-1] * 12, "recovery_month": 24},
        "scenarios": [
            {"name": "distress", "enterprise_value": 0, "exit_net_debt": 0},
            {"name": "weak", "enterprise_value": 100, "exit_net_debt": 0},
            {"name": "base", "enterprise_value": 300, "exit_net_debt": 0},
        ],
        "probability_view": {"target_cagr": 0.1, "horizon_years": 2},
    }
    result = analyze_case(case_from_dict(raw))
    assert result.survives_to_recovery_window is None


def test_required_probability_is_zero_when_downside_already_clears_target():
    assert required_probability(4.0, 2.0, 1.5) == 0.0

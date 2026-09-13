from __future__ import annotations

from datetime import date

import pytest

from distressed_equity.base_rates import BaseRateCase, BaseRateLibrary


def test_base_rate_library_excludes_outcomes_not_known_by_cutoff():
    library = BaseRateLibrary(
        (
            BaseRateCase(
                case_id="old-win",
                ticker="AAA",
                analysis_date=date(2015, 1, 1),
                outcome_known_date=date(2018, 1, 1),
                impairment_type="cyclical",
                leverage_bucket="high",
                survived_12m=True,
                existing_common_survived_12m=True,
                normalized_within_3y=True,
                equity_multiple_3y=5.0,
            ),
            BaseRateCase(
                case_id="future-loss",
                ticker="BBB",
                analysis_date=date(2021, 1, 1),
                outcome_known_date=date(2024, 1, 1),
                impairment_type="cyclical",
                leverage_bucket="high",
                survived_12m=False,
                existing_common_survived_12m=False,
                normalized_within_3y=False,
                equity_multiple_3y=0.0,
            ),
        )
    )
    summary = library.summarize(
        date(2022, 12, 31),
        impairment_type="cyclical",
        leverage_bucket="high",
    )
    assert summary.available_case_count == 1
    assert summary.matched_case_count == 1
    assert summary.survived_12m_rate == 1.0
    assert summary.three_x_within_3y_rate == 1.0


def test_base_rate_summary_uses_separate_denominators_for_missing_outcomes():
    library = BaseRateLibrary(
        (
            BaseRateCase(
                case_id="a",
                ticker="A",
                analysis_date=date(2010, 1, 1),
                outcome_known_date=date(2014, 1, 1),
                survived_12m=True,
                existing_common_survived_12m=None,
                normalized_within_3y=True,
                equity_multiple_3y=4.0,
            ),
            BaseRateCase(
                case_id="b",
                ticker="B",
                analysis_date=date(2010, 1, 1),
                outcome_known_date=date(2014, 1, 1),
                survived_12m=False,
                existing_common_survived_12m=False,
                normalized_within_3y=None,
                equity_multiple_3y=1.0,
            ),
        )
    )
    summary = library.summarize(date(2020, 1, 1))
    assert summary.survived_12m_rate == pytest.approx(0.5)
    assert summary.existing_common_survival_rate == 0.0
    assert summary.normalized_within_3y_rate == 1.0
    assert summary.three_x_within_3y_rate == pytest.approx(0.5)
    assert summary.median_equity_multiple_3y == pytest.approx(2.5)


def test_base_rate_can_exclude_same_case_from_its_own_calibration():
    case = BaseRateCase(
        case_id="self",
        ticker="SELF",
        analysis_date=date(2010, 1, 1),
        outcome_known_date=date(2015, 1, 1),
        survived_12m=True,
    )
    library = BaseRateLibrary((case,))
    summary = library.summarize(date(2020, 1, 1), exclude_case_ids=("self",))
    assert summary.available_case_count == 0
    assert summary.matched_case_count == 0
    assert summary.survived_12m_rate is None

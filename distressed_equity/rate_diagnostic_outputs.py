from __future__ import annotations

from .market_base_rates import MarketOutcomeBaseRateComparison
from .rate_diagnostics import binomial_rate_diagnostic, diagnostic_to_dict
from .source_outcomes import SourceOutcomeBaseRateComparison
from .survival_features import FeatureStrataComparison


def _diag(rate: float | None, resolved_n: int, small_sample_n: int) -> dict[str, object]:
    return diagnostic_to_dict(
        binomial_rate_diagnostic(rate, resolved_n, small_sample_n=small_sample_n)
    )


def market_rate_diagnostics(
    comparison: MarketOutcomeBaseRateComparison,
    *,
    small_sample_n: int = 30,
) -> dict[str, object]:
    return {
        "interval": "Wilson 95%",
        "small_sample_n": small_sample_n,
        "groups": [
            {
                "group": group.group,
                "same_security_active_12m": _diag(
                    group.same_security_active_12m_rate,
                    group.resolved_12m_membership_count,
                    small_sample_n,
                ),
                "same_security_active_3y": _diag(
                    group.same_security_active_3y_rate,
                    group.resolved_3y_membership_count,
                    small_sample_n,
                ),
                "three_x_3y_endpoint": _diag(
                    group.three_x_3y_endpoint_rate,
                    group.resolved_3y_endpoint_multiple_count,
                    small_sample_n,
                ),
                "three_x_observed_within_3y": _diag(
                    group.three_x_observed_within_3y_rate,
                    group.resolved_3y_max_multiple_count,
                    small_sample_n,
                ),
            }
            for group in comparison.groups
        ],
    }


def source_rate_diagnostics(
    comparison: SourceOutcomeBaseRateComparison,
    *,
    small_sample_n: int = 30,
) -> dict[str, object]:
    return {
        "interval": "Wilson 95%",
        "small_sample_n": small_sample_n,
        "groups": [
            {
                "group": group.group,
                "survived_12m": _diag(
                    group.survived_12m_rate,
                    group.survived_12m_resolved_count,
                    small_sample_n,
                ),
                "existing_common_survival": _diag(
                    group.existing_common_survival_rate,
                    group.common_12m_resolved_count,
                    small_sample_n,
                ),
                "normalized_within_3y": _diag(
                    group.normalized_within_3y_rate,
                    group.normalized_3y_resolved_count,
                    small_sample_n,
                ),
                "three_x_3y": _diag(
                    group.three_x_3y_rate,
                    group.equity_multiple_3y_resolved_count,
                    small_sample_n,
                ),
            }
            for group in comparison.groups
        ],
    }


def feature_strata_rate_diagnostics(
    comparison: FeatureStrataComparison,
    *,
    small_sample_n: int = 30,
) -> dict[str, object]:
    return {
        "interval": "Wilson 95%",
        "small_sample_n": small_sample_n,
        "strata": [
            {
                "dimension": item.dimension,
                "bucket": item.bucket,
                "credit_group": item.credit_group,
                "survived_12m": _diag(
                    item.survived_12m_rate,
                    item.survived_12m_resolved_count,
                    small_sample_n,
                ),
                "existing_common_survival": _diag(
                    item.existing_common_survival_rate,
                    item.common_12m_resolved_count,
                    small_sample_n,
                ),
                "normalized_within_3y": _diag(
                    item.normalized_within_3y_rate,
                    item.normalized_3y_resolved_count,
                    small_sample_n,
                ),
                "three_x_3y": _diag(
                    item.three_x_3y_rate,
                    item.equity_multiple_3y_resolved_count,
                    small_sample_n,
                ),
            }
            for item in comparison.strata
        ],
    }

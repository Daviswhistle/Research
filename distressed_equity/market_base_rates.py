from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median
from typing import Iterable

from .credit_replay import JointDistressSeed, JointReplayRun
from .market_outcomes import MarketObservableOutcome, MarketOutcomeCohort


@dataclass(frozen=True)
class MarketOutcomeRateGroup:
    group: str
    case_count: int
    resolved_12m_membership_count: int
    same_security_active_12m_rate: float | None
    resolved_3y_membership_count: int
    same_security_active_3y_rate: float | None
    resolved_3y_endpoint_multiple_count: int
    three_x_3y_endpoint_rate: float | None
    median_3y_endpoint_multiple: float | None
    resolved_3y_max_multiple_count: int
    three_x_observed_within_3y_rate: float | None
    median_max_observed_multiple_within_3y: float | None
    case_keys: tuple[str, ...]


@dataclass(frozen=True)
class MarketOutcomeBaseRateComparison:
    groups: tuple[MarketOutcomeRateGroup, ...]
    semantics: tuple[str, ...]


def _case_key(security_id: str, analysis_date: object) -> str:
    return f"{security_id}|{analysis_date}"


def _rate(values: Iterable[bool]) -> float | None:
    rows = tuple(values)
    if not rows:
        return None
    return sum(rows) / len(rows)


def _summarize(
    group_name: str,
    rows: Iterable[tuple[JointDistressSeed, MarketObservableOutcome]],
) -> MarketOutcomeRateGroup:
    items = tuple(rows)
    membership_12m = [outcome.same_security_active_12m for _, outcome in items if outcome.same_security_active_12m is not None]
    membership_3y = [outcome.same_security_active_3y for _, outcome in items if outcome.same_security_active_3y is not None]
    endpoint = [
        float(outcome.adjusted_price_multiple_3y)
        for _, outcome in items
        if outcome.adjusted_price_multiple_3y is not None
    ]
    max_multiples = [
        float(outcome.max_adjusted_price_multiple_within_3y)
        for _, outcome in items
        if outcome.max_adjusted_price_multiple_within_3y is not None
    ]
    return MarketOutcomeRateGroup(
        group=group_name,
        case_count=len(items),
        resolved_12m_membership_count=len(membership_12m),
        same_security_active_12m_rate=_rate(bool(value) for value in membership_12m),
        resolved_3y_membership_count=len(membership_3y),
        same_security_active_3y_rate=_rate(bool(value) for value in membership_3y),
        resolved_3y_endpoint_multiple_count=len(endpoint),
        three_x_3y_endpoint_rate=_rate(value >= 3.0 for value in endpoint),
        median_3y_endpoint_multiple=(median(endpoint) if endpoint else None),
        resolved_3y_max_multiple_count=len(max_multiples),
        three_x_observed_within_3y_rate=_rate(value >= 3.0 for value in max_multiples),
        median_max_observed_multiple_within_3y=(median(max_multiples) if max_multiples else None),
        case_keys=tuple(
            _case_key(item.equity.security.security_id, item.equity.analysis_date.isoformat())
            for item, _ in items
        ),
    )


def compare_market_outcome_base_rates(
    joint: JointReplayRun,
    outcomes: MarketOutcomeCohort,
) -> MarketOutcomeBaseRateComparison:
    """Compare market-observable outcomes across credit-evidence groups.

    This is deliberately not a legal/business survival table. It only summarizes
    permanent-security membership and observed adjusted-price paths.
    """

    joint_by_key: dict[str, JointDistressSeed] = {}
    for item in joint.candidates:
        key = _case_key(item.equity.security.security_id, item.equity.analysis_date.isoformat())
        if key in joint_by_key:
            raise ValueError(f"duplicate joint replay case key: {key}")
        joint_by_key[key] = item

    outcome_by_key: dict[str, MarketObservableOutcome] = {}
    for outcome in outcomes.outcomes:
        key = _case_key(outcome.security_id, outcome.analysis_date.isoformat())
        if key in outcome_by_key:
            raise ValueError(f"duplicate market outcome case key: {key}")
        outcome_by_key[key] = outcome

    if set(joint_by_key) != set(outcome_by_key):
        missing_outcomes = sorted(set(joint_by_key) - set(outcome_by_key))
        missing_joint = sorted(set(outcome_by_key) - set(joint_by_key))
        raise ValueError(
            "joint replay and market outcomes must describe the same case set; "
            f"missing_outcomes={missing_outcomes}, missing_joint={missing_joint}"
        )

    buckets: dict[str, list[tuple[JointDistressSeed, MarketObservableOutcome]]] = {
        "fresh_credit_stress": [],
        "fresh_credit_no_stress": [],
        "no_fresh_credit": [],
    }
    for key in sorted(joint_by_key):
        item = joint_by_key[key]
        outcome = outcome_by_key[key]
        if item.fresh_credit_instrument_count <= 0:
            bucket = "no_fresh_credit"
        elif item.any_credit_stress:
            bucket = "fresh_credit_stress"
        else:
            bucket = "fresh_credit_no_stress"
        buckets[bucket].append((item, outcome))

    return MarketOutcomeBaseRateComparison(
        groups=tuple(_summarize(name, buckets[name]) for name in (
            "fresh_credit_stress",
            "fresh_credit_no_stress",
            "no_fresh_credit",
        )),
        semantics=(
            "same_security_active rates describe permanent-security membership in the point-in-time market universe, not corporate or legal common-equity survival",
            "three_x_3y_endpoint_rate uses the observed +3y adjusted-price endpoint multiple among cases where that endpoint is resolved",
            "three_x_observed_within_3y_rate uses the maximum observed adjusted-price multiple within the full 3y horizon and is not an endpoint-return rate",
            "no_fresh_credit is a coverage/liquidity category and must not be interpreted as no credit distress",
        ),
    )


def market_outcome_base_rate_to_dict(comparison: MarketOutcomeBaseRateComparison) -> dict[str, object]:
    return {
        "groups": [asdict(group) for group in comparison.groups],
        "semantics": list(comparison.semantics),
    }

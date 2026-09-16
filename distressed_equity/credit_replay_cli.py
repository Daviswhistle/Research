from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

from .credit_replay import (
    CreditReplayConfig,
    CsvCreditIdentityIndex,
    JointReplayRun,
    joint_replay_to_dict,
    run_joint_credit_replay,
)
from .csv_bond_market import CsvBondMarketProvider
from .csv_market import CsvMarketProvider
from .market_base_rates import (
    MarketOutcomeBaseRateComparison,
    compare_market_outcome_base_rates,
    market_outcome_base_rate_to_dict,
)
from .market_outcomes import (
    MarketOutcomeCohort,
    MarketOutcomeConfig,
    label_market_outcome_cohort,
    market_outcome_cohort_to_dict,
)
from .rate_diagnostic_outputs import (
    feature_strata_rate_diagnostics,
    market_rate_diagnostics,
    source_rate_diagnostics,
)
from .replay import DistressScanConfig, run_historical_replay
from .source_outcomes import (
    CsvSourceBackedOutcomeIndex,
    SourceOutcomeBaseRateComparison,
    compare_source_outcome_base_rates,
    source_outcome_base_rate_to_dict,
)
from .survival_features import (
    CsvSurvivalFeatureIndex,
    FeatureStrataComparison,
    compare_feature_strata_base_rates,
    feature_strata_to_dict,
)


_DEFAULT_FEATURE_DIMENSIONS = (
    "liquidity_runway",
    "nearest_maturity",
    "covenant_headroom",
    "net_leverage",
    "impairment_type",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Point-in-time bulk replay combining equity distress with corporate-credit stress"
    )
    parser.add_argument("--analysis-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--securities-csv", required=True, help="Point-in-time equity security master")
    parser.add_argument("--prices-csv", required=True, help="Normalized adjusted/raw equity price history")
    parser.add_argument("--credit-links-csv", required=True, help="Point-in-time security_id -> CUSIP/ISIN membership links")
    parser.add_argument("--bond-observations-csv", required=True, help="Daily normalized corporate-bond observations")
    parser.add_argument("--exchange", action="append", default=[])
    parser.add_argument("--min-drawdown", type=float, default=0.60)
    parser.add_argument("--min-price", type=float, default=0.10)
    parser.add_argument("--lookback-years", type=int, default=5)
    parser.add_argument("--max-securities", type=int)
    parser.add_argument("--credit-lookback-days", type=int, default=365)
    parser.add_argument("--credit-max-staleness-days", type=int, default=30)
    parser.add_argument("--credit-price-below", type=float, default=80.0)
    parser.add_argument("--credit-yield-at-or-above", type=float, default=15.0)
    parser.add_argument("--credit-spread-at-or-above", type=float, default=1000.0)
    parser.add_argument(
        "--outcome-cutoff",
        help=(
            "Optional YYYY-MM-DD date through which later data may be used for outcome analysis. "
            "It gates both market-observable +12m/+3y outcomes and source-backed legal/business labels."
        ),
    )
    parser.add_argument(
        "--outcome-horizon-grace-days",
        type=int,
        default=31,
        help="Maximum days after a +12m/+3y target to find the first adjusted price observation",
    )
    parser.add_argument(
        "--source-outcomes-csv",
        help=(
            "Optional verified legal/business outcome labels. Requires --outcome-cutoff; labels known after that date "
            "are excluded by outcome_known_date."
        ),
    )
    parser.add_argument(
        "--survival-features-csv",
        help=(
            "Optional verified T0 survival feature snapshots. Requires --source-outcomes-csv and --outcome-cutoff; "
            "evidence_known_date must be on or before each T0 analysis date."
        ),
    )
    parser.add_argument(
        "--feature-strata-dimensions",
        default=",".join(_DEFAULT_FEATURE_DIMENSIONS),
        help=(
            "Comma-separated T0 dimensions: liquidity_runway,nearest_maturity,covenant_headroom,"
            "net_leverage,impairment_type"
        ),
    )
    parser.add_argument(
        "--rate-small-sample-n",
        type=int,
        default=30,
        help=(
            "Resolved-denominator threshold below which empirical binomial rate diagnostics carry a small-sample warning; "
            "Wilson 95%% intervals are reported regardless"
        ),
    )
    parser.add_argument("--output", "-o", help="JSON output; stdout when omitted")
    parser.add_argument("--markdown-output", help="Optional human-readable cohort summary")
    return parser


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def _mult(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}x"


def _feature_dimensions(raw: str) -> tuple[str, ...]:
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not values:
        raise ValueError("--feature-strata-dimensions must contain at least one dimension")
    return values


def _markdown(
    run: JointReplayRun,
    outcomes: MarketOutcomeCohort | None = None,
    base_rates: MarketOutcomeBaseRateComparison | None = None,
    source_base_rates: SourceOutcomeBaseRateComparison | None = None,
    feature_strata: FeatureStrataComparison | None = None,
    *,
    rate_small_sample_n: int = 30,
) -> str:
    lines = [
        f"# Joint equity / credit distress replay — {run.analysis_date.isoformat()}",
        "",
        f"- equity provider: `{run.equity_provider}`",
        f"- credit provider: `{run.credit_provider}`",
        f"- equity distress candidates: **{run.equity_candidate_count:,}**",
        f"- with point-in-time credit links: **{run.candidates_with_credit_links:,}**",
        f"- with fresh credit evidence: **{run.candidates_with_fresh_credit:,}**",
        f"- with at least one credit-stress signal: **{run.candidates_with_credit_stress:,}**",
        "",
        "| Symbol | Equity drawdown | Linked bonds | Fresh | Min price | Max yield | Max spread | Credit stress |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in run.candidates:
        symbol = item.equity.security.symbol
        min_price = "—" if item.min_fresh_price_pct_par is None else f"{item.min_fresh_price_pct_par:.1f}"
        max_yield = "—" if item.max_fresh_yield_pct is None else f"{item.max_fresh_yield_pct:.1f}%"
        max_spread = "—" if item.max_fresh_spread_bps is None else f"{item.max_fresh_spread_bps:.0f} bps"
        lines.append(
            f"| {symbol} | {item.equity.adjusted_drawdown_from_peak:.1%} | "
            f"{item.linked_credit_instrument_count} | {item.fresh_credit_instrument_count} | "
            f"{min_price} | {max_yield} | {max_spread} | {'YES' if item.any_credit_stress else 'no'} |"
        )
    lines.extend([
        "",
        "> Credit stress is a cross-check, not a default-probability estimate. Missing links, stale trades, price, yield, and spread signals remain separate fields.",
        "",
    ])

    if outcomes is not None:
        lines.extend([
            "## Market-observable outcomes",
            "",
            f"- outcome data cutoff: **{outcomes.outcome_cutoff.isoformat()}**",
            f"- +12m membership resolved: **{outcomes.resolved_12m_membership_count:,}/{outcomes.case_count:,}**",
            f"- same permanent security active at +12m: **{outcomes.active_12m_count:,}**",
            f"- +3y membership resolved: **{outcomes.resolved_3y_membership_count:,}/{outcomes.case_count:,}**",
            f"- same permanent security active at +3y: **{outcomes.active_3y_count:,}**",
            f"- +3y endpoint multiple resolved: **{outcomes.resolved_3y_endpoint_multiple_count:,}**",
            f"- 3y max observed multiple resolved: **{outcomes.resolved_3y_max_multiple_count:,}**",
            "",
            "| Symbol | Active +12m | Adj. multiple +12m | Active +3y | Adj. multiple +3y | Max observed multiple ≤3y |",
            "|---|---|---:|---|---:|---:|",
        ])
        for outcome in outcomes.outcomes:
            m12 = _mult(outcome.adjusted_price_multiple_12m)
            m3 = _mult(outcome.adjusted_price_multiple_3y)
            max3 = _mult(outcome.max_adjusted_price_multiple_within_3y)
            a12 = "unknown" if outcome.same_security_active_12m is None else ("yes" if outcome.same_security_active_12m else "no")
            a3 = "unknown" if outcome.same_security_active_3y is None else ("yes" if outcome.same_security_active_3y else "no")
            lines.append(f"| {outcome.symbol_at_distress} | {a12} | {m12} | {a3} | {m3} | {max3} |")
        lines.extend([
            "",
            "> Same-security membership and adjusted-price multiples are market-observable facts only. They are not labels for corporate survival, bankruptcy avoidance, reorganization success, or legal survival of the pre-distress common stock.",
            "",
        ])

    if base_rates is not None:
        lines.extend([
            "## Market-observable base-rate comparison",
            "",
            "| Credit evidence group | Cases | Same security active +12m | Same security active +3y | 3x at +3y endpoint | 3x observed within 3y | Median +3y endpoint | Median max ≤3y |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for group in base_rates.groups:
            lines.append(
                f"| {group.group} | {group.case_count} | {_pct(group.same_security_active_12m_rate)} | "
                f"{_pct(group.same_security_active_3y_rate)} | {_pct(group.three_x_3y_endpoint_rate)} | "
                f"{_pct(group.three_x_observed_within_3y_rate)} | {_mult(group.median_3y_endpoint_multiple)} | "
                f"{_mult(group.median_max_observed_multiple_within_3y)} |"
            )
        lines.extend([
            "",
            "> These are market-observable conditional rates, not bankruptcy/common-survival rates. `no_fresh_credit` is a coverage/liquidity group, not evidence of credit health.",
            "",
        ])

    if source_base_rates is not None:
        lines.extend([
            "## Verified legal / business outcome base rates",
            "",
            f"- knowledge cutoff: **{source_base_rates.knowledge_cutoff.isoformat()}**",
            f"- replay cohort cases: **{source_base_rates.cohort_case_count:,}**",
            f"- verified source-backed labels known by cutoff: **{source_base_rates.known_source_label_count:,}**",
            "",
            "| Credit evidence group | Cohort | Source labels | Company survives 12m | Existing common survives 12m | Normalizes ≤3y | 3x ≤3y | Median 3y multiple |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for group in source_base_rates.groups:
            lines.append(
                f"| {group.group} | {group.cohort_case_count} | {group.source_labeled_case_count} | "
                f"{_pct(group.survived_12m_rate)} | {_pct(group.existing_common_survival_rate)} | "
                f"{_pct(group.normalized_within_3y_rate)} | {_pct(group.three_x_3y_rate)} | "
                f"{_mult(group.median_equity_multiple_3y)} |"
            )
        lines.extend([
            "",
            "> Legal/business rates use only rows marked verified with non-empty evidence refs, verifier metadata, reopened evidence, and outcome_known_date at or before the knowledge cutoff. Missing labels stay missing; they are not counted as failures.",
            "",
        ])

    if feature_strata is not None:
        lines.extend([
            "## T0 survival-feature strata",
            "",
            f"- verified T0 feature snapshots in cohort: **{feature_strata.feature_snapshot_count:,}/{feature_strata.cohort_case_count:,}**",
            f"- source-backed outcome labels known by cutoff: **{feature_strata.known_source_label_count:,}**",
            "",
            "| Dimension | Bucket | Credit group | Cohort | Feature rows | Outcome labels | Company survives 12m | Existing common survives 12m | Normalizes ≤3y | 3x ≤3y |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for item in feature_strata.strata:
            lines.append(
                f"| {item.dimension} | {item.bucket} | {item.credit_group} | {item.cohort_case_count} | "
                f"{item.feature_snapshot_count} | {item.source_labeled_case_count} | {_pct(item.survived_12m_rate)} | "
                f"{_pct(item.existing_common_survival_rate)} | {_pct(item.normalized_within_3y_rate)} | "
                f"{_pct(item.three_x_3y_rate)} |"
            )
        lines.extend([
            "",
            "> Feature buckets are descriptive strata, not universal causal thresholds. Only verified T0 values with evidence_known_date on or before the analysis date are admitted; unknown features remain explicit unknown buckets.",
            "",
        ])

    if base_rates is not None or source_base_rates is not None or feature_strata is not None:
        lines.extend([
            "## Rate uncertainty",
            "",
            "- JSON output includes Wilson 95% intervals for every empirical binomial rate.",
            f"- Any resolved denominator below **{rate_small_sample_n}** is flagged `small_sample=true`.",
            "- Point estimates from thin strata should not be treated as precise probabilities; no LLM-based probability adjustment is applied.",
            "",
        ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.source_outcomes_csv and not args.outcome_cutoff:
        raise ValueError("--source-outcomes-csv requires --outcome-cutoff as the outcome knowledge boundary")
    if args.survival_features_csv and not args.source_outcomes_csv:
        raise ValueError("--survival-features-csv requires --source-outcomes-csv for source-backed outcome strata")
    if args.survival_features_csv and not args.outcome_cutoff:
        raise ValueError("--survival-features-csv requires --outcome-cutoff as the outcome knowledge boundary")
    if args.rate_small_sample_n <= 0:
        raise ValueError("--rate-small-sample-n must be positive")

    cutoff = date.fromisoformat(args.analysis_date)
    equity_provider = CsvMarketProvider(args.securities_csv, args.prices_csv)
    equity_run = run_historical_replay(
        equity_provider,
        cutoff,
        config=DistressScanConfig(
            min_adjusted_drawdown=args.min_drawdown,
            lookback_years=args.lookback_years,
            min_raw_price=args.min_price,
            exchanges=tuple(args.exchange),
        ),
        max_securities=args.max_securities,
    )
    credit_provider = CsvBondMarketProvider(args.bond_observations_csv)
    links = CsvCreditIdentityIndex(args.credit_links_csv)
    joint = run_joint_credit_replay(
        equity_run,
        credit_provider,
        links,
        config=CreditReplayConfig(
            lookback_days=args.credit_lookback_days,
            max_staleness_days=args.credit_max_staleness_days,
            price_stress_below_pct_par=args.credit_price_below,
            yield_stress_at_or_above_pct=args.credit_yield_at_or_above,
            spread_stress_at_or_above_bps=args.credit_spread_at_or_above,
        ),
    )

    outcomes = None
    base_rates = None
    source_base_rates = None
    feature_strata = None
    source_index = None
    payload = joint_replay_to_dict(joint)
    diagnostics: dict[str, object] = {}
    outcome_cutoff = None
    if args.outcome_cutoff:
        outcome_cutoff = date.fromisoformat(args.outcome_cutoff)
        outcomes = label_market_outcome_cohort(
            equity_provider,
            (item.equity for item in joint.candidates),
            outcome_cutoff,
            config=MarketOutcomeConfig(horizon_grace_days=args.outcome_horizon_grace_days),
        )
        base_rates = compare_market_outcome_base_rates(joint, outcomes)
        payload["market_outcomes"] = market_outcome_cohort_to_dict(outcomes)
        payload["market_base_rates"] = market_outcome_base_rate_to_dict(base_rates)
        diagnostics["market_base_rates"] = market_rate_diagnostics(
            base_rates,
            small_sample_n=args.rate_small_sample_n,
        )

    if args.source_outcomes_csv:
        assert outcome_cutoff is not None
        source_index = CsvSourceBackedOutcomeIndex(args.source_outcomes_csv)
        source_base_rates = compare_source_outcome_base_rates(joint, source_index, outcome_cutoff)
        payload["source_outcome_base_rates"] = source_outcome_base_rate_to_dict(source_base_rates)
        diagnostics["source_outcome_base_rates"] = source_rate_diagnostics(
            source_base_rates,
            small_sample_n=args.rate_small_sample_n,
        )

    if args.survival_features_csv:
        assert outcome_cutoff is not None and source_index is not None
        feature_index = CsvSurvivalFeatureIndex(args.survival_features_csv)
        feature_strata = compare_feature_strata_base_rates(
            joint,
            feature_index,
            source_index,
            outcome_cutoff,
            dimensions=_feature_dimensions(args.feature_strata_dimensions),
        )
        payload["feature_strata_base_rates"] = feature_strata_to_dict(feature_strata)
        diagnostics["feature_strata_base_rates"] = feature_strata_rate_diagnostics(
            feature_strata,
            small_sample_n=args.rate_small_sample_n,
        )

    if diagnostics:
        payload["rate_diagnostics"] = diagnostics

    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    if args.markdown_output:
        target = Path(args.markdown_output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            _markdown(
                joint,
                outcomes,
                base_rates,
                source_base_rates,
                feature_strata,
                rate_small_sample_n=args.rate_small_sample_n,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

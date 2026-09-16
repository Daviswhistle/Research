from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

from .credit_replay import (
    CreditReplayConfig,
    CsvCreditIdentityIndex,
    JointReplayRun,
    run_joint_credit_replay,
)
from .csv_bond_market import CsvBondMarketProvider
from .csv_market import CsvMarketProvider
from .rate_diagnostics import BinomialRateDiagnostic
from .replay import DistressScanConfig, run_historical_replay
from .source_outcomes import CsvSourceBackedOutcomeIndex
from .survival_features import CsvSurvivalFeatureIndex
from .walk_forward_priors import (
    WalkForwardPriorRun,
    build_walk_forward_priors,
    walk_forward_prior_to_dict,
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
        description=(
            "Build cutoff-safe walk-forward empirical priors for a target distress cohort using only "
            "historical cases whose outcomes were already known at target T0"
        )
    )
    parser.add_argument("--analysis-date", required=True, help="Target T0, YYYY-MM-DD")
    parser.add_argument(
        "--prior-analysis-date",
        action="append",
        required=True,
        help="Historical replay T0; repeat for each calibration date",
    )
    parser.add_argument("--securities-csv", required=True, help="Point-in-time equity security master")
    parser.add_argument("--prices-csv", required=True, help="Normalized raw/adjusted equity price history")
    parser.add_argument(
        "--credit-links-csv",
        required=True,
        help="Point-in-time permanent security_id -> CUSIP/ISIN links",
    )
    parser.add_argument(
        "--bond-observations-csv",
        required=True,
        help="Normalized daily corporate-bond observations",
    )
    parser.add_argument(
        "--source-outcomes-csv",
        required=True,
        help="Verified source-backed historical legal/business outcomes",
    )
    parser.add_argument(
        "--survival-features-csv",
        required=True,
        help="Verified T0 liquidity/maturity/covenant/leverage/impairment snapshots",
    )
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
        "--feature-dimensions",
        default=",".join(_DEFAULT_FEATURE_DIMENSIONS),
        help=(
            "Comma-separated prior strata dimensions: liquidity_runway,nearest_maturity,"
            "covenant_headroom,net_leverage,impairment_type"
        ),
    )
    parser.add_argument(
        "--episode-gap-days",
        type=int,
        default=365,
        help="Collapse repeated same-security distress observations within this many days",
    )
    parser.add_argument(
        "--small-sample-n",
        type=int,
        default=30,
        help="Flag binomial rate diagnostics with resolved denominator below this value",
    )
    parser.add_argument("--output", "-o", help="JSON output; stdout when omitted")
    parser.add_argument("--markdown-output", help="Optional human-readable prior summary")
    return parser


def _dates(values: list[str]) -> tuple[date, ...]:
    parsed = tuple(sorted({date.fromisoformat(value) for value in values}))
    if not parsed:
        raise ValueError("at least one --prior-analysis-date is required")
    return parsed


def _feature_dimensions(raw: str) -> tuple[str, ...]:
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not values:
        raise ValueError("--feature-dimensions must contain at least one dimension")
    return values


def _joint_run(
    cutoff: date,
    equity_provider: CsvMarketProvider,
    credit_provider: CsvBondMarketProvider,
    links: CsvCreditIdentityIndex,
    *,
    distress_config: DistressScanConfig,
    credit_config: CreditReplayConfig,
    max_securities: int | None,
) -> JointReplayRun:
    equity_run = run_historical_replay(
        equity_provider,
        cutoff,
        config=distress_config,
        max_securities=max_securities,
    )
    return run_joint_credit_replay(
        equity_run,
        credit_provider,
        links,
        config=credit_config,
    )


def _diagnostic_text(value: BinomialRateDiagnostic) -> str:
    if value.rate is None or value.resolved_n == 0:
        return "— (n=0)"
    interval = ""
    if value.wilson_95_low is not None and value.wilson_95_high is not None:
        interval = f", 95% CI {value.wilson_95_low:.1%}–{value.wilson_95_high:.1%}"
    sample = ", small-n" if value.small_sample else ""
    return f"{value.rate:.1%} ({value.successes}/{value.resolved_n}{interval}{sample})"


def _markdown(run: WalkForwardPriorRun) -> str:
    lines = [
        f"# Walk-forward distress priors — target {run.target_analysis_date.isoformat()}",
        "",
        f"- historical replay dates: **{', '.join(item.isoformat() for item in run.historical_run_dates)}**",
        f"- historical cases before episode dedup: **{run.historical_case_count_before_episode_dedup:,}**",
        f"- historical cases after episode dedup: **{run.historical_case_count_after_episode_dedup:,}**",
        f"- historical source labels known by target T0: **{run.source_labels_known_by_target:,}**",
        f"- target cases: **{run.target_case_count:,}**",
        f"- same-episode exclusion window: **{run.episode_gap_days} days**",
        "",
        "| Symbol | Basis | Bucket | Credit group | Calibration cases | Source labels | Company survives 12m | Existing common survives 12m | Normalizes ≤3y | 3x ≤3y |",
        "|---|---|---|---|---:|---:|---|---|---|---|",
    ]
    for case in run.priors:
        strata = (case.credit_group_prior,) + case.feature_priors
        for stratum in strata:
            lines.append(
                f"| {case.symbol} | {stratum.basis} | {stratum.bucket} | {stratum.credit_group} | "
                f"{stratum.calibration_case_count} | {stratum.source_labeled_case_count} | "
                f"{_diagnostic_text(stratum.survived_12m)} | "
                f"{_diagnostic_text(stratum.existing_common_survival)} | "
                f"{_diagnostic_text(stratum.normalized_within_3y)} | "
                f"{_diagnostic_text(stratum.three_x_3y)} |"
            )
        for warning in case.warnings:
            lines.append(f"| {case.symbol} | warning | — | — | — | — | {warning} | — | — | — |")
    lines.extend(
        [
            "",
            "> These are ex-ante empirical priors available at the target T0. Target-cohort outcomes and source labels first known after target T0 are excluded. Raw rates, denominators, Wilson intervals, and small-sample flags are preserved; no LLM probability adjustment is applied.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    target_date = date.fromisoformat(args.analysis_date)
    prior_dates = _dates(args.prior_analysis_date)
    if any(item >= target_date for item in prior_dates):
        invalid = [item.isoformat() for item in prior_dates if item >= target_date]
        raise ValueError(
            "--prior-analysis-date values must strictly precede --analysis-date: " + ", ".join(invalid)
        )

    equity_provider = CsvMarketProvider(args.securities_csv, args.prices_csv)
    credit_provider = CsvBondMarketProvider(args.bond_observations_csv)
    links = CsvCreditIdentityIndex(args.credit_links_csv)
    outcomes = CsvSourceBackedOutcomeIndex(args.source_outcomes_csv)
    features = CsvSurvivalFeatureIndex(args.survival_features_csv)

    distress_config = DistressScanConfig(
        min_adjusted_drawdown=args.min_drawdown,
        lookback_years=args.lookback_years,
        min_raw_price=args.min_price,
        exchanges=tuple(args.exchange),
    )
    credit_config = CreditReplayConfig(
        lookback_days=args.credit_lookback_days,
        max_staleness_days=args.credit_max_staleness_days,
        price_stress_below_pct_par=args.credit_price_below,
        yield_stress_at_or_above_pct=args.credit_yield_at_or_above,
        spread_stress_at_or_above_bps=args.credit_spread_at_or_above,
    )

    target = _joint_run(
        target_date,
        equity_provider,
        credit_provider,
        links,
        distress_config=distress_config,
        credit_config=credit_config,
        max_securities=args.max_securities,
    )
    historical = tuple(
        _joint_run(
            item,
            equity_provider,
            credit_provider,
            links,
            distress_config=distress_config,
            credit_config=credit_config,
            max_securities=args.max_securities,
        )
        for item in prior_dates
    )
    priors = build_walk_forward_priors(
        target,
        historical,
        outcomes,
        features,
        dimensions=_feature_dimensions(args.feature_dimensions),
        episode_gap_days=args.episode_gap_days,
        small_sample_n=args.small_sample_n,
    )

    text = json.dumps(walk_forward_prior_to_dict(priors), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        target_path = Path(args.output)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(text, encoding="utf-8")
    else:
        print(text, end="")

    if args.markdown_output:
        markdown_path = Path(args.markdown_output)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(_markdown(priors), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

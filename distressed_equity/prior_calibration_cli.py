from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

from .calibration_stability import (
    CalibrationStabilityReport,
    calibration_stability_to_dict,
    evaluate_calibration_stability,
)
from .credit_replay import CreditReplayConfig, CsvCreditIdentityIndex, JointReplayRun, run_joint_credit_replay
from .csv_bond_market import CsvBondMarketProvider
from .csv_market import CsvMarketProvider
from .prior_calibration import PriorCalibrationReport, evaluate_walk_forward_priors, prior_calibration_to_dict
from .replay import DistressScanConfig, run_historical_replay
from .source_outcomes import CsvSourceBackedOutcomeIndex
from .survival_features import CsvSurvivalFeatureIndex
from .walk_forward_priors import WalkForwardPriorRun, build_walk_forward_priors


_DEFAULT_FEATURE_DIMENSIONS = (
    "liquidity_runway",
    "nearest_maturity",
    "covenant_headroom",
    "net_leverage",
    "impairment_type",
)
_DEFAULT_STABILITY_DIMENSIONS = (
    "target_date",
    "target_year",
    "credit_group",
    "calibration_n_band",
    "basis_bucket",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Replay multiple historical distress cutoffs, build cutoff-safe walk-forward priors at each later cutoff, "
            "evaluate each forecast family against source-backed outcomes, and slice realized calibration stability"
        )
    )
    parser.add_argument(
        "--analysis-date",
        action="append",
        required=True,
        help="Replay cutoff YYYY-MM-DD; repeat in chronological research grid (at least two unique dates)",
    )
    parser.add_argument(
        "--evaluation-cutoff",
        required=True,
        help="YYYY-MM-DD: latest source-backed outcome knowledge allowed for ex-post calibration evaluation",
    )
    parser.add_argument("--securities-csv", required=True)
    parser.add_argument("--prices-csv", required=True)
    parser.add_argument("--credit-links-csv", required=True)
    parser.add_argument("--bond-observations-csv", required=True)
    parser.add_argument("--source-outcomes-csv", required=True)
    parser.add_argument("--survival-features-csv", required=True)
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
        "--stability-dimensions",
        default=",".join(_DEFAULT_STABILITY_DIMENSIONS),
        help=(
            "Comma-separated realized calibration slices: target_date,target_year,credit_group,"
            "calibration_n_band,basis_bucket"
        ),
    )
    parser.add_argument("--episode-gap-days", type=int, default=365)
    parser.add_argument("--small-sample-n", type=int, default=30)
    parser.add_argument(
        "--calibration-bin-width",
        type=float,
        default=0.10,
        help="Reliability-bin width; must divide 1 exactly",
    )
    parser.add_argument("--output", "-o", help="JSON output; stdout when omitted")
    parser.add_argument("--markdown-output", help="Optional human-readable calibration summary")
    return parser


def _analysis_dates(values: list[str]) -> tuple[date, ...]:
    parsed = tuple(sorted({date.fromisoformat(value) for value in values}))
    if len(parsed) < 2:
        raise ValueError("prior calibration requires at least two unique --analysis-date values")
    return parsed


def _dimensions(raw: str, *, option: str) -> tuple[str, ...]:
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not values:
        raise ValueError(f"{option} must contain at least one dimension")
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
    equity = run_historical_replay(
        equity_provider,
        cutoff,
        config=distress_config,
        max_securities=max_securities,
    )
    return run_joint_credit_replay(equity, credit_provider, links, config=credit_config)


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def _fmt_float(value: float | None, digits: int = 4) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _markdown(
    report: PriorCalibrationReport,
    stability: CalibrationStabilityReport,
    *,
    analysis_dates: tuple[date, ...],
    prior_runs: tuple[WalkForwardPriorRun, ...],
) -> str:
    lines = [
        f"# Walk-forward prior calibration — evaluated through {report.evaluation_cutoff.isoformat()}",
        "",
        f"- replay grid: **{', '.join(item.isoformat() for item in analysis_dates)}**",
        f"- walk-forward prior runs: **{report.prior_run_count:,}**",
        f"- target cases across prior runs: **{report.target_case_count:,}**",
        f"- realized forecast observations: **{report.forecast_observation_count:,}**",
        "",
        "| Basis | Metric | Forecasts | Mean predicted | Observed rate | Brier score | Median calibration n | Small-n forecasts |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report.summaries:
        mean_p = _fmt_pct(item.mean_predicted_probability)
        observed = _fmt_pct(item.observed_rate)
        brier = _fmt_float(item.brier_score)
        median_n = "—" if item.median_calibration_resolved_n is None else f"{item.median_calibration_resolved_n:.1f}"
        lines.append(
            f"| {item.basis} | {item.metric} | {item.forecast_count} | {mean_p} | {observed} | "
            f"{brier} | {median_n} | {item.small_sample_forecast_count} |"
        )

    lines.extend(["", "## Reliability bins", ""])
    for summary in report.summaries:
        lines.extend(
            [
                f"### {summary.basis} / {summary.metric}",
                "",
                "| Forecast interval | n | Mean predicted | Observed | Brier |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for bin_row in summary.bins:
            bracket = "]" if bin_row.upper_inclusive else ")"
            interval = f"[{bin_row.lower_bound:.2f}, {bin_row.upper_bound:.2f}{bracket}"
            lines.append(
                f"| {interval} | {bin_row.forecast_count} | {_fmt_pct(bin_row.mean_predicted_probability)} | "
                f"{_fmt_pct(bin_row.observed_rate)} | {_fmt_float(bin_row.brier_score)} |"
            )
        lines.append("")

    lines.extend(
        [
            "## Stability slices",
            "",
            "| Dimension | Slice | Basis | Metric | Forecasts | Target dates | Securities | Mean predicted | Observed | Gap | Brier | Median calibration n |",
            "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in stability.slices:
        median_n = "—" if item.median_calibration_resolved_n is None else f"{item.median_calibration_resolved_n:.1f}"
        lines.append(
            f"| {item.dimension} | {item.value} | {item.basis} | {item.metric} | {item.forecast_count} | "
            f"{item.unique_target_date_count} | {item.unique_security_count} | {_fmt_pct(item.mean_predicted_probability)} | "
            f"{_fmt_pct(item.observed_rate)} | {_fmt_pct(item.calibration_gap)} | {_fmt_float(item.brier_score)} | {median_n} |"
        )

    if report.warnings or stability.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in report.warnings)
        lines.extend(f"- {warning}" for warning in stability.warnings)
        lines.append("")

    lines.extend(
        [
            "> Each basis is a separate forecast family. Credit-group, liquidity, maturity, covenant, leverage, and impairment priors overlap and are not averaged into one synthetic probability. Stability slices only regroup already-realized OOS forecasts; they do not refit or alter probabilities. Calendar slices are descriptive dates/years, not automatically named economic regimes.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    analysis_dates = _analysis_dates(args.analysis_date)
    evaluation_cutoff = date.fromisoformat(args.evaluation_cutoff)
    feature_dimensions = _dimensions(args.feature_dimensions, option="--feature-dimensions")
    stability_dimensions = _dimensions(args.stability_dimensions, option="--stability-dimensions")

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

    joint_by_date = {
        cutoff: _joint_run(
            cutoff,
            equity_provider,
            credit_provider,
            links,
            distress_config=distress_config,
            credit_config=credit_config,
            max_securities=args.max_securities,
        )
        for cutoff in analysis_dates
    }

    prior_runs: list[WalkForwardPriorRun] = []
    for index, target_date in enumerate(analysis_dates[1:], start=1):
        historical = tuple(joint_by_date[item] for item in analysis_dates[:index])
        prior_runs.append(
            build_walk_forward_priors(
                joint_by_date[target_date],
                historical,
                outcomes,
                features,
                dimensions=feature_dimensions,
                episode_gap_days=args.episode_gap_days,
                small_sample_n=args.small_sample_n,
            )
        )

    report = evaluate_walk_forward_priors(
        prior_runs,
        outcomes,
        evaluation_cutoff,
        bin_width=args.calibration_bin_width,
    )
    stability = evaluate_calibration_stability(report, dimensions=stability_dimensions)
    payload = prior_calibration_to_dict(report)
    payload["analysis_dates"] = [item.isoformat() for item in analysis_dates]
    payload["walk_forward_prior_runs"] = [
        {
            "target_analysis_date": run.target_analysis_date.isoformat(),
            "target_case_count": run.target_case_count,
            "historical_case_count_before_episode_dedup": run.historical_case_count_before_episode_dedup,
            "historical_case_count_after_episode_dedup": run.historical_case_count_after_episode_dedup,
            "source_labels_known_by_target": run.source_labels_known_by_target,
        }
        for run in prior_runs
    ]
    payload["stability"] = calibration_stability_to_dict(stability)

    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
    else:
        print(text, end="")

    if args.markdown_output:
        markdown_path = Path(args.markdown_output)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(
            _markdown(
                report,
                stability,
                analysis_dates=analysis_dates,
                prior_runs=tuple(prior_runs),
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

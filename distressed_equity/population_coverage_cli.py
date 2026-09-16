from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

from .credit_replay import CreditReplayConfig, CsvCreditIdentityIndex, run_joint_credit_replay
from .csv_bond_market import CsvBondMarketProvider
from .csv_market import CsvMarketProvider
from .population_coverage import build_population_coverage_report, population_coverage_to_dict
from .replay import DistressScanConfig, run_historical_replay
from .source_outcomes import CsvSourceBackedOutcomeIndex
from .survival_features import CsvSurvivalFeatureIndex


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit historical distress-population data coverage across replay dates: market evaluability, "
            "equity distress, point-in-time credit links, source-outcome maturity/censoring, and T0 features"
        )
    )
    parser.add_argument("--analysis-date", action="append", required=True, help="YYYY-MM-DD; repeat as needed")
    parser.add_argument(
        "--outcome-coverage-cutoff",
        required=True,
        help="YYYY-MM-DD: latest knowledge date allowed when measuring ex-post source-outcome coverage",
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
    parser.add_argument("--output", "-o", help="JSON output; stdout when omitted")
    parser.add_argument("--markdown-output", help="Optional human-readable coverage report")
    return parser


def _analysis_dates(raw: list[str]) -> tuple[date, ...]:
    dates = tuple(sorted({date.fromisoformat(item) for item in raw}))
    if not dates:
        raise ValueError("at least one --analysis-date is required")
    return dates


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def _markdown(payload: dict[str, object]) -> str:
    rows = payload["rows"]
    assert isinstance(rows, list)
    lines = [
        f"# Historical distress population coverage — through {payload['outcome_coverage_cutoff']}",
        "",
        f"- replay dates: **{', '.join(payload['analysis_dates'])}**",
        f"- distress case observations: **{payload['total_case_observations']}**",
        f"- unique permanent securities: **{payload['unique_security_count']}**",
        f"- repeated security observations: **{payload['repeated_case_observation_count']}**",
        "",
        "## Market evaluability",
        "",
        "| Date | Universe | Scanned | Evaluable | Unevaluable | Evaluable non-candidate | Distress | Distress / evaluable |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        assert isinstance(row, dict)
        lines.append(
            f"| {row['analysis_date']} | {row['historical_universe_size']} | {row['scanned_security_count']} | "
            f"{row['evaluable_security_count']} | {row['unevaluable_security_count']} | "
            f"{row['evaluable_non_candidate_count']} | {row['equity_distress_case_count']} | "
            f"{_pct(row['distress_rate_of_evaluable'])} |"
        )

    lines.extend([
        "",
        "### Skip reasons",
        "",
        "| Date | Classification | Reason | Count |",
        "|---|---|---|---:|",
    ])
    for row in rows:
        assert isinstance(row, dict)
        reasons = row.get("skip_reason_counts")
        assert isinstance(reasons, list)
        if not reasons:
            lines.append(f"| {row['analysis_date']} | — | none | 0 |")
            continue
        for reason in reasons:
            assert isinstance(reason, dict)
            lines.append(
                f"| {row['analysis_date']} | {reason['classification']} | {reason['reason']} | {reason['count']} |"
            )

    lines.extend([
        "",
        "## Credit / source / feature coverage",
        "",
        "| Date | Credit links | Fresh credit | Credit stress | Source labels | T0 features | Link / distress | Fresh / linked |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for row in rows:
        assert isinstance(row, dict)
        lines.append(
            f"| {row['analysis_date']} | {row['credit_linked_case_count']} | {row['fresh_credit_case_count']} | "
            f"{row['credit_stress_case_count']} | {row['source_labeled_case_count']} | {row['t0_feature_snapshot_count']} | "
            f"{_pct(row['credit_link_coverage_of_distress'])} | {_pct(row['fresh_credit_coverage_of_linked'])} |"
        )

    lines.extend([
        "",
        "## Outcome maturity / censoring",
        "",
        "| Date | Metric | Labeled | Horizon eligible | Early resolved | Right-censored | Missing among eligible | Coverage of eligible |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ])
    metric_rows = (
        ("survived_12m", "Survival 12m"),
        ("existing_common_12m", "Common 12m"),
        ("normalized_3y", "Normalized 3y"),
        ("equity_multiple_3y", "Equity multiple 3y"),
    )
    for row in rows:
        assert isinstance(row, dict)
        for prefix, label in metric_rows:
            lines.append(
                f"| {row['analysis_date']} | {label} | {row[prefix + '_labeled_count']} | "
                f"{row[prefix + '_horizon_eligible_count']} | {row[prefix + '_early_resolved_count']} | "
                f"{row[prefix + '_right_censored_count']} | {row[prefix + '_missing_among_horizon_eligible_count']} | "
                f"{_pct(row[prefix + '_label_coverage_of_horizon_eligible'])} |"
            )

    lines.extend([
        "",
        "## T0 feature detail",
        "",
        "| Date | Snapshots | Liquidity | Maturity | Covenant | Leverage | Impairment |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for row in rows:
        assert isinstance(row, dict)
        lines.append(
            f"| {row['analysis_date']} | {row['t0_feature_snapshot_count']} | {row['liquidity_runway_feature_count']} | "
            f"{row['nearest_maturity_feature_count']} | {row['covenant_headroom_feature_count']} | "
            f"{row['net_leverage_feature_count']} | {row['impairment_type_feature_count']} |"
        )
    warnings = payload.get("warnings")
    if isinstance(warnings, list) and warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {item}" for item in warnings)
    lines.extend([
        "",
        "> This is an ex-post dataset coverage audit, not an investment signal and not an ex-ante prior. Unevaluable securities are separated from evaluable non-candidates; outcome labels are measured against horizon-eligible cases, while pre-horizon unresolved cases remain explicitly right-censored.",
        "",
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    analysis_dates = _analysis_dates(args.analysis_date)
    coverage_cutoff = date.fromisoformat(args.outcome_coverage_cutoff)
    if any(item > coverage_cutoff for item in analysis_dates):
        raise ValueError("all analysis dates must be on or before --outcome-coverage-cutoff")

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

    pairs = []
    for cutoff in analysis_dates:
        equity = run_historical_replay(
            equity_provider,
            cutoff,
            config=distress_config,
            max_securities=args.max_securities,
        )
        joint = run_joint_credit_replay(equity, credit_provider, links, config=credit_config)
        pairs.append((equity, joint))

    report = build_population_coverage_report(
        pairs,
        outcomes,
        features,
        outcome_coverage_cutoff=coverage_cutoff,
    )
    payload = population_coverage_to_dict(report)
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
        target.write_text(_markdown(payload), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

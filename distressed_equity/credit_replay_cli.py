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
from .replay import DistressScanConfig, run_historical_replay


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
            "Optional YYYY-MM-DD date through which later market data may be used to label market-observable "
            "+12m/+3y outcomes and market-only base-rate comparisons. Omit to keep candidate replay strictly cutoff-only."
        ),
    )
    parser.add_argument(
        "--outcome-horizon-grace-days",
        type=int,
        default=31,
        help="Maximum days after a +12m/+3y target to find the first adjusted price observation",
    )
    parser.add_argument("--output", "-o", help="JSON output; stdout when omitted")
    parser.add_argument("--markdown-output", help="Optional human-readable cohort summary")
    return parser


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def _mult(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}x"


def _markdown(
    run: JointReplayRun,
    outcomes: MarketOutcomeCohort | None = None,
    base_rates: MarketOutcomeBaseRateComparison | None = None,
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
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
    payload = joint_replay_to_dict(joint)
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
        target.write_text(_markdown(joint, outcomes, base_rates), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

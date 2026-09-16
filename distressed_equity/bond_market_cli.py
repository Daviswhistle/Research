from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
from typing import Any

from .bond_market import build_bond_market_packet, bond_market_packet_to_dict
from .csv_bond_market import CsvBondMarketProvider
from .debt_instruments import DebtInstrumentLedger, DebtInstrumentVersion, debt_snapshots_from_dict
from .eodhd_bonds import EodhdBondProvider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Attach historical bond price/yield evidence to a stable debt-instrument ledger"
    )
    parser.add_argument("--ledger", required=True, help="debt_instrument_ledger.json")
    parser.add_argument("--analysis-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--provider", choices=("csv", "eodhd"), required=True)
    parser.add_argument("--bond-observations-csv", help="Normalized bond observations CSV for --provider csv")
    parser.add_argument("--eodhd-key", help="Defaults to EODHD_API_KEY")
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument("--max-staleness-days", type=int, default=30)
    parser.add_argument("--probability-horizon-years", type=float, default=1.0)
    parser.add_argument(
        "--recovery-rates",
        default="0.20,0.40,0.60",
        help="Comma-separated recovery assumptions used only for spread-implied risk-neutral stress proxies",
    )
    parser.add_argument("--output", required=True)
    return parser


def _load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("ledger JSON must be an object")
    return payload


def _ledger_from_payload(payload: dict[str, Any]) -> DebtInstrumentLedger:
    raw_versions = payload.get("versions")
    if not isinstance(raw_versions, list):
        raise ValueError("ledger JSON must contain versions[]")
    versions: list[DebtInstrumentVersion] = []
    for index, raw in enumerate(raw_versions):
        if not isinstance(raw, dict):
            raise ValueError(f"ledger version {index} must be an object")
        stable_id = str(raw.get("stable_id") or "").strip()
        snapshot_raw = raw.get("snapshot")
        if not stable_id or not isinstance(snapshot_raw, dict):
            raise ValueError(f"ledger version {index} requires stable_id and snapshot")
        snapshots = debt_snapshots_from_dict([snapshot_raw])
        if len(snapshots) != 1:
            raise ValueError(f"ledger version {index} snapshot could not be parsed")
        score = raw.get("match_score")
        versions.append(
            DebtInstrumentVersion(
                stable_id=stable_id,
                snapshot=snapshots[0],
                match_confidence=str(raw.get("match_confidence") or "serialized"),
                match_score=float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else None,
                matched_from_accession=(
                    str(raw["matched_from_accession"]) if raw.get("matched_from_accession") else None
                ),
                warnings=tuple(str(item) for item in raw.get("warnings", []) if str(item).strip()),
            )
        )
    return DebtInstrumentLedger(
        versions=tuple(versions),
        changes=(),
        unmatched_versions=tuple(str(item) for item in payload.get("unmatched_versions", []) if str(item).strip()),
        warnings=tuple(str(item) for item in payload.get("warnings", []) if str(item).strip()),
    )


def _recoveries(raw: str) -> tuple[float, ...]:
    values = tuple(float(item.strip()) for item in raw.split(",") if item.strip())
    if not values:
        raise ValueError("--recovery-rates must contain at least one value")
    return values


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cutoff = date.fromisoformat(args.analysis_date)
    ledger = _ledger_from_payload(_load_json(args.ledger))

    if args.provider == "csv":
        if not args.bond_observations_csv:
            raise ValueError("--provider csv requires --bond-observations-csv")
        provider = CsvBondMarketProvider(args.bond_observations_csv)
    else:
        provider = EodhdBondProvider(api_key=args.eodhd_key or os.getenv("EODHD_API_KEY"))

    packet = build_bond_market_packet(
        provider,
        ledger,
        cutoff,
        lookback_days=args.lookback_days,
        max_staleness_days=args.max_staleness_days,
        recovery_rates=_recoveries(args.recovery_rates),
        probability_horizon_years=args.probability_horizon_years,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(bond_market_packet_to_dict(packet), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .debt_instruments import build_debt_instrument_ledger, debt_instrument_ledger_to_dict, debt_snapshots_from_dict
from .debt_lineage import (
    build_debt_lineage_graph,
    debt_lineage_event_template,
    debt_lineage_events_from_dict,
    debt_lineage_graph_to_dict,
    debt_lineage_source_packet_fingerprint,
    debt_lineage_verification_template,
    promote_verified_debt_lineage_events,
    validate_debt_lineage_events_against_source_packet,
)
from .instrument_verification import validate_debt_snapshots_against_source_packet


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build source-backed debt exchange/modification/extinguishment lineage"
    )
    parser.add_argument("instruments", help="JSON with filing-level debt instrument snapshots")
    parser.add_argument(
        "events",
        nargs="?",
        help="JSON with candidate lineage events; omit with --template-output to create a research template",
    )
    parser.add_argument("--source-packet", help="Frozen SEC debt_instrument_sources.json")
    parser.add_argument("--output", "-o", help="Combined ledger + lineage JSON; stdout when omitted")
    parser.add_argument("--template-output", help="Write an event template containing available stable IDs")
    parser.add_argument(
        "--verification-template-output",
        help="Write verification template bound to the supplied candidate events and frozen source packet",
    )
    parser.add_argument(
        "--verification",
        help="Verification JSON; matching event and frozen-source fingerprints are required for promotion",
    )
    parser.add_argument("--match-threshold", type=float, default=55.0)
    parser.add_argument("--ambiguity-margin", type=float, default=8.0)
    return parser


def _load(path: str | Path) -> dict[str, Any] | list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, (dict, list)):
        raise ValueError(f"expected JSON object or list: {path}")
    return payload


def _load_object(path: str | Path) -> dict[str, Any]:
    payload = _load(path)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _write(path: str | Path, payload: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _same_path(left: str | Path, right: str | Path) -> bool:
    return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if (args.verification_template_output or args.verification) and not args.source_packet:
        raise ValueError("lineage verification requires --source-packet so approval is bound to frozen evidence")

    raw_instruments = _load(args.instruments)
    source_packet = _load_object(args.source_packet) if args.source_packet else None
    source_warnings: list[str] = []

    if source_packet is not None:
        snapshot_validation = validate_debt_snapshots_against_source_packet(source_packet, raw_instruments)
        if not snapshot_validation.valid:
            raise ValueError(
                "invalid source-backed debt snapshots: " + "; ".join(snapshot_validation.errors)
            )
        snapshots = snapshot_validation.snapshots
        source_warnings.extend(snapshot_validation.warnings)
    else:
        snapshots = debt_snapshots_from_dict(raw_instruments)
        source_warnings.append(
            "source packet not supplied; snapshot and lineage source_refs were not re-opened against frozen SEC spans"
        )

    ledger = build_debt_instrument_ledger(
        snapshots,
        match_threshold=args.match_threshold,
        ambiguity_margin=args.ambiguity_margin,
    )

    if args.template_output:
        _write(args.template_output, debt_lineage_event_template(ledger))
        if not args.events:
            print(args.template_output)
            return 0

    if not args.events:
        raise ValueError("events JSON is required unless only --template-output is requested")
    raw_events = _load(args.events)
    if source_packet is not None:
        event_validation = validate_debt_lineage_events_against_source_packet(
            source_packet,
            raw_events,
            ledger,
        )
        if not event_validation.valid:
            raise ValueError(
                "invalid source-backed debt lineage events: " + "; ".join(event_validation.errors)
            )
        events = event_validation.events
        source_warnings.extend(event_validation.warnings)
        source_fingerprint = debt_lineage_source_packet_fingerprint(source_packet)
    else:
        events = debt_lineage_events_from_dict(raw_events)
        source_fingerprint = None

    verification = _load_object(args.verification) if args.verification else None
    if args.verification_template_output:
        if verification is None or not _same_path(args.verification_template_output, args.verification):
            if source_fingerprint is None:
                raise ValueError("verification template requires a frozen source packet fingerprint")
            _write(
                args.verification_template_output,
                debt_lineage_verification_template(
                    events,
                    source_packet_fingerprint=source_fingerprint,
                ),
            )

    if verification is not None:
        if source_fingerprint is None:
            raise ValueError("verification promotion requires a frozen source packet fingerprint")
        events = promote_verified_debt_lineage_events(
            events,
            verification,
            source_packet_fingerprint=source_fingerprint,
        )

    lineage = build_debt_lineage_graph(ledger, events)
    payload = {
        "ledger": debt_instrument_ledger_to_dict(ledger),
        "lineage": debt_lineage_graph_to_dict(lineage),
        "source_validation_warnings": list(dict.fromkeys(source_warnings)),
    }
    if args.output:
        _write(args.output, payload)
        print(args.output)
    elif args.verification_template_output and verification is None:
        print(args.verification_template_output)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

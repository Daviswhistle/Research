from __future__ import annotations

from dataclasses import replace
from datetime import date
import math
from typing import Any


def install_debt_pipeline_hardening(sec_module: Any, debt_module: Any, lineage_module: Any) -> None:
    """Install conservative boundaries shared by extraction, ledger and lineage.

    The structured extractors intentionally produce research candidates. This layer
    makes the transition from candidate -> verified debt snapshot -> confirmed
    lineage explicit and prevents precision/identity shortcuts from leaking into
    investment-facing topology.
    """

    if getattr(sec_module, "_debt_pipeline_hardening_installed", False):
        return

    # ------------------------------------------------------------------
    # 1. Generated snapshot templates: effective date remains unresolved
    #    and the reviewer attestation is explicit in the generated workflow.
    # ------------------------------------------------------------------
    original_template_for_cluster = sec_module._template_for_cluster

    def hardened_template_for_cluster(document: Any, proposals: tuple[Any, ...], source_span_ids: tuple[str, ...]):
        template, warnings = original_template_for_cluster(document, proposals, source_span_ids)
        template = dict(template)
        template["as_of_date"] = None
        notes = list(template.get("notes") or [])
        notes.append(
            "as_of_date is the economic measurement/effective date, not the SEC publication date; verify it explicitly"
        )
        template["notes"] = list(dict.fromkeys(notes))
        return template, tuple(dict.fromkeys((*warnings, "snapshot effective date remains unresolved until source verification")))

    sec_module._template_for_cluster = hardened_template_for_cluster

    original_task = sec_module.build_instrument_verification_task

    def hardened_task(packet: Any):
        task = original_task(packet)
        guardrails = tuple(dict.fromkeys((*task.guardrails,
            "Set each retained snapshot's as_of_date from the economic measurement/effective date supported by evidence; never substitute the SEC filing/publication date.",
            "Complete verification.status/verifier/verified_on/evidence_reopened only after re-opening every cited source span.",
        )))
        required_output = tuple(dict.fromkeys((*task.required_output,
            "Per-instrument verification object with status, verifier, verified_on, and evidence_reopened",
            "Verified economic as_of_date for every retained instrument",
        )))
        return replace(task, guardrails=guardrails, required_output=required_output)

    sec_module.build_instrument_verification_task = hardened_task

    original_verification_template = sec_module.instrument_verification_template

    def hardened_verification_template(packet: Any) -> dict[str, Any]:
        payload = original_verification_template(packet)
        rows: list[dict[str, Any]] = []
        for raw in payload.get("instruments", []):
            row = dict(raw)
            row["as_of_date"] = None
            row["verification"] = {
                "status": "unverified",
                "verifier": None,
                "verified_on": None,
                "evidence_reopened": False,
            }
            rows.append(row)
        payload["instruments"] = rows
        warnings = list(payload.get("warnings") or [])
        warnings.extend([
            "Generated snapshot rows are intentionally unverified; fill the verification object only after reopening cited evidence.",
            "as_of_date is intentionally null until the economic measurement/effective date is verified.",
        ])
        payload["warnings"] = list(dict.fromkeys(warnings))
        return payload

    sec_module.instrument_verification_template = hardened_verification_template

    # ------------------------------------------------------------------
    # 2. Ledger JSON cannot carry NaN/Infinity into matching or lineage.
    # ------------------------------------------------------------------
    original_snapshot_from_dict = debt_module._snapshot_from_dict
    finite_keys = (
        "principal", "commitment", "drawn", "available", "coupon_pct", "spread_bps",
    )

    def hardened_snapshot_from_dict(raw: dict[str, Any]):
        name = str(raw.get("name") or "debt instrument").strip() or "debt instrument"
        for key in finite_keys:
            value = raw.get(key)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue  # original parser emits the canonical numeric type error
            if not math.isfinite(float(value)):
                raise ValueError(f"{name}: {key} must be finite")
        return original_snapshot_from_dict(raw)

    debt_module._snapshot_from_dict = hardened_snapshot_from_dict

    # ------------------------------------------------------------------
    # 3. Boundary snapshot ties must be source-disambiguated at ANY selected
    #    boundary date, not only on the event date itself.
    # ------------------------------------------------------------------
    def hardened_boundary_snapshot(
        versions: tuple[Any, ...],
        event_date: date,
        *,
        predecessor: bool,
        observation_accession: str | None = None,
    ):
        snapshots = [item.snapshot for item in versions]
        if not snapshots:
            raise ValueError("debt lineage participant has no instrument snapshots")
        role = "predecessor" if predecessor else "successor"

        if observation_accession:
            matches = [item for item in snapshots if item.source_accession == observation_accession]
            if len(matches) != 1:
                raise ValueError(
                    f"observation_accession {observation_accession!r} does not identify exactly one debt snapshot"
                )
            snapshot = matches[0]
            if predecessor and snapshot.as_of_date > event_date:
                raise ValueError("predecessor observation_accession points after event effective date")
            if not predecessor and snapshot.as_of_date < event_date:
                raise ValueError("successor observation_accession points before event effective date")
            warning = None
            if snapshot.as_of_date != event_date:
                warning = (
                    f"{role} observation date {snapshot.as_of_date.isoformat()} differs from "
                    f"event effective date {event_date.isoformat()}"
                )
            return snapshot, warning

        if predecessor:
            eligible_dates = [item.as_of_date for item in snapshots if item.as_of_date <= event_date]
            selected_date = max(eligible_dates) if eligible_dates else min(item.as_of_date for item in snapshots)
        else:
            eligible_dates = [item.as_of_date for item in snapshots if item.as_of_date >= event_date]
            selected_date = min(eligible_dates) if eligible_dates else max(item.as_of_date for item in snapshots)

        matches = [item for item in snapshots if item.as_of_date == selected_date]
        if len(matches) > 1:
            raise ValueError(
                f"multiple debt snapshots exist on selected {role} boundary date {selected_date.isoformat()}; "
                "participant.observation_accession is required to disambiguate the instrument state"
            )
        snapshot = matches[0]
        if selected_date == event_date:
            return snapshot, None
        if predecessor:
            if selected_date < event_date:
                return snapshot, (
                    f"predecessor observation date {selected_date.isoformat()} differs from "
                    f"event effective date {event_date.isoformat()}"
                )
            return snapshot, (
                f"predecessor first observed {selected_date.isoformat()} after event effective date "
                f"{event_date.isoformat()}"
            )
        if selected_date > event_date:
            return snapshot, (
                f"successor observation date {selected_date.isoformat()} differs from "
                f"event effective date {event_date.isoformat()}"
            )
        return snapshot, (
            f"successor last observed {selected_date.isoformat()} before event effective date "
            f"{event_date.isoformat()}"
        )

    lineage_module._boundary_snapshot = hardened_boundary_snapshot

    # ------------------------------------------------------------------
    # 4. Maturity comparisons use shared precision. A year-only maturity does
    #    not invent January 1 against an exact date in the same year.
    # ------------------------------------------------------------------
    original_impact_for_event = lineage_module._impact_for_event

    def _maturity_effect(before: str | None, after: str | None) -> str:
        if before is None or after is None:
            return "not_comparable"
        before_year_only = len(before) == 4 and before.isdigit()
        after_year_only = len(after) == 4 and after.isdigit()
        before_year = int(before[:4])
        after_year = int(after[:4])
        if before_year != after_year:
            return "extended" if after_year > before_year else "accelerated"
        if before_year_only and after_year_only:
            return "unchanged"
        if before_year_only != after_year_only:
            return "not_comparable"
        before_date = date.fromisoformat(before)
        after_date = date.fromisoformat(after)
        if after_date > before_date:
            return "extended"
        if after_date < before_date:
            return "accelerated"
        return "unchanged"

    def hardened_impact_for_event(event: Any, versions_by_id: dict[str, tuple[Any, ...]]):
        impact = original_impact_for_event(event, versions_by_id)
        effect = _maturity_effect(impact.nearest_maturity_before, impact.nearest_maturity_after)
        signals = [
            item for item in impact.signals
            if item not in {"nearest_maturity_extended", "nearest_maturity_accelerated"}
        ]
        if effect == "extended":
            signals.append("nearest_maturity_extended")
        elif effect == "accelerated":
            signals.append("nearest_maturity_accelerated")
        return replace(impact, maturity_effect=effect, signals=tuple(dict.fromkeys(signals)))

    lineage_module._impact_for_event = hardened_impact_for_event

    # ------------------------------------------------------------------
    # 5. Terminal topology uses aggregate consumption against one observed
    #    predecessor state. Two 500 exchanges of a 1,000 note fully consume it.
    # ------------------------------------------------------------------
    original_build_graph = lineage_module.build_debt_lineage_graph

    def hardened_build_graph(ledger: Any, events: Any):
        events_tuple = tuple(events)
        graph = original_build_graph(ledger, events_tuple)
        versions_by_id = lineage_module._versions_by_id(ledger)
        verified_events = [event for event in events_tuple if event.status == "verified"]
        lineage_instruments = {
            participant.stable_id
            for event in verified_events
            for participant in (*event.predecessors, *event.successors)
        }
        incoming: set[str] = set()
        outgoing: set[str] = set()
        consumption: dict[tuple[str, str], float] = {}
        caps: dict[tuple[str, str], float] = {}

        for event in verified_events:
            predecessor_ids = {item.stable_id for item in event.predecessors}
            successor_ids = {item.stable_id for item in event.successors}
            incoming.update(successor_ids - predecessor_ids)
            for participant in event.predecessors:
                if participant.stable_id in successor_ids or event.event_type == "amendment":
                    continue
                snapshot, _ = lineage_module._snapshot_for_participant(
                    participant,
                    versions_by_id,
                    event.effective_date,
                    predecessor=True,
                )
                amount = lineage_module._participant_amount(participant, snapshot)
                if (
                    event.event_type in lineage_module._CONSUMING_EVENT_TYPES
                    and amount is not None
                    and snapshot.principal is not None
                ):
                    key = (participant.stable_id, snapshot.source_accession)
                    consumption[key] = consumption.get(key, 0.0) + amount
                    caps[key] = snapshot.principal
                    continue
                # Unknown consumption cannot prove extinguishment. Preserve the
                # instrument as a possible terminal rather than inventing zero residual.

        for key, total in consumption.items():
            cap = caps[key]
            tolerance = max(1e-9, abs(cap) * 1e-9)
            if total >= cap - tolerance:
                outgoing.add(key[0])

        roots = tuple(sorted(lineage_instruments - incoming))
        terminals = tuple(sorted(lineage_instruments - outgoing))
        return replace(graph, root_instruments=roots, terminal_instruments=terminals)

    lineage_module.build_debt_lineage_graph = hardened_build_graph

    sec_module._debt_pipeline_hardening_installed = True
    debt_module._debt_pipeline_hardening_installed = True
    lineage_module._debt_pipeline_hardening_installed = True

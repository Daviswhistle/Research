from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

from .covenants import CovenantDefinition, CovenantEbitdaBridge, CovenantHeadroomResult, CovenantInputs, calculate_covenant_headroom


@dataclass(frozen=True)
class CovenantSensitivityCase:
    label: str
    included_disputed_add_backs: tuple[str, ...]
    excluded_disputed_add_backs: tuple[str, ...]
    result: CovenantHeadroomResult


@dataclass(frozen=True)
class CovenantSensitivityReport:
    covenant_name: str
    disputed_add_backs: tuple[str, ...]
    claimed_case: CovenantSensitivityCase
    conservative_case: CovenantSensitivityCase
    individual_exclusion_cases: tuple[CovenantSensitivityCase, ...]
    covenant_ebitda_range: tuple[float, float]
    actual_range: tuple[float, float] | None
    headroom_range: tuple[float, float] | None
    breach_in_any_determinate_case: bool | None
    breach_in_all_cases: bool | None
    classification: str
    warnings: tuple[str, ...]


def _case(label: str, definition: CovenantDefinition, bridge: CovenantEbitdaBridge, inputs: CovenantInputs, excluded: tuple[str, ...]) -> CovenantSensitivityCase:
    disputed = set(bridge.disputed_add_backs)
    excluded_set = set(excluded)
    scenario_bridge = replace(
        bridge,
        add_backs={key: value for key, value in bridge.add_backs.items() if key not in excluded_set},
        disputed_add_backs=(),
    )
    return CovenantSensitivityCase(
        label=label,
        included_disputed_add_backs=tuple(sorted(disputed - excluded_set)),
        excluded_disputed_add_backs=tuple(sorted(excluded_set)),
        result=calculate_covenant_headroom(definition, scenario_bridge, inputs),
    )


def _numeric_range(values: list[float | None]) -> tuple[float, float] | None:
    present = [value for value in values if value is not None]
    return (min(present), max(present)) if present else None

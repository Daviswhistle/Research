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


def calculate_covenant_addback_sensitivity(definition: CovenantDefinition, bridge: CovenantEbitdaBridge, inputs: CovenantInputs) -> CovenantSensitivityReport:
    disputed = tuple(dict.fromkeys(bridge.disputed_add_backs))
    warnings: list[str] = []
    missing = [name for name in disputed if name not in bridge.add_backs]
    if missing:
        warnings.append("disputed add-backs missing from add_backs: " + ", ".join(missing))
    modeled = tuple(name for name in disputed if name in bridge.add_backs)

    claimed = _case("claimed_all_disputed_included", definition, bridge, inputs, ())
    conservative = _case("conservative_all_disputed_excluded", definition, bridge, inputs, modeled)
    individual = tuple(
        _case(f"exclude:{name}", definition, bridge, inputs, (name,))
        for name in modeled
    )
    cases = (claimed, conservative, *individual)

    actual_range = _numeric_range([case.result.actual for case in cases])
    headroom_range = _numeric_range([case.result.headroom for case in cases])
    ebitda_values = [case.result.covenant_ebitda for case in cases]

    applicable = [case.result for case in cases if case.result.applies is True]
    determinate_breaches = [result.breached for result in applicable if result.breached is not None]
    breach_any = any(determinate_breaches) if determinate_breaches else None
    breach_all = (
        all(result.breached is True for result in applicable)
        if applicable and all(result.breached is not None for result in applicable)
        else None
    )

    if not modeled:
        classification = "no_modeled_disputed_addbacks"
    elif breach_all is True:
        classification = "robust_breach"
    elif applicable and all(result.breached is False for result in applicable):
        classification = "robust_compliance"
    elif claimed.result.breached is False and conservative.result.breached is True:
        classification = "disputed_addbacks_flip_outcome"
    elif claimed.result.breached is True and conservative.result.breached is False:
        classification = "non_monotonic_or_input_issue"
        warnings.append("excluding positive add-backs improved compliance; inspect signs and covenant inputs")
    else:
        classification = "sensitivity_unresolved_or_partial"

    return CovenantSensitivityReport(
        covenant_name=definition.name,
        disputed_add_backs=modeled,
        claimed_case=claimed,
        conservative_case=conservative,
        individual_exclusion_cases=individual,
        covenant_ebitda_range=(min(ebitda_values), max(ebitda_values)),
        actual_range=actual_range,
        headroom_range=headroom_range,
        breach_in_any_determinate_case=breach_any,
        breach_in_all_cases=breach_all,
        classification=classification,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def covenant_sensitivity_to_dict(report: CovenantSensitivityReport) -> dict[str, Any]:
    return asdict(report)

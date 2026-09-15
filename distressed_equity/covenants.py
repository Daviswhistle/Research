from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SUPPORTED_COVENANT_KINDS = {
    "max_net_leverage",
    "max_total_leverage",
    "min_interest_coverage",
    "min_fixed_charge_coverage",
    "min_liquidity",
}


@dataclass(frozen=True)
class CovenantEbitdaBridge:
    """Auditable bridge from a base EBITDA measure to covenant EBITDA.

    The engine does not decide whether an add-back is legally permitted. The
    caller must source each component from the credit agreement / amendment.
    """

    base_ebitda: float
    add_backs: dict[str, float] = field(default_factory=dict)
    deductions: dict[str, float] = field(default_factory=dict)
    disputed_add_backs: tuple[str, ...] = ()

    @property
    def covenant_ebitda(self) -> float:
        return self.base_ebitda + sum(self.add_backs.values()) - sum(self.deductions.values())


@dataclass(frozen=True)
class CovenantDefinition:
    name: str
    kind: str
    threshold: float
    test_month: int = 0
    springing: bool = False
    springing_revolver_drawn_pct: float | None = None
    cure_available: bool | None = None
    cure_cost: float = 0.0
    source_refs: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CovenantInputs:
    gross_debt: float | None = None
    unrestricted_cash: float | None = None
    cash_netting_cap: float | None = None
    cash_netting_allowed: bool = True
    interest_expense: float | None = None
    fixed_charges: float | None = None
    liquidity: float | None = None
    revolver_drawn: float | None = None
    revolver_commitment: float | None = None


@dataclass(frozen=True)
class CovenantHeadroomResult:
    name: str
    kind: str
    threshold: float
    applies: bool | None
    actual: float | None
    headroom: float | None
    headroom_pct_of_threshold: float | None
    breached: bool | None
    covenant_ebitda: float
    minimum_ebitda_to_comply: float | None
    ebitda_cushion: float | None
    net_debt: float | None
    test_month: int
    cure_available: bool | None
    cure_cost: float
    warnings: tuple[str, ...]


def _number(value: Any, field_name: str, *, allow_none: bool = True) -> float | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    return float(value)


def covenant_model_from_dict(raw: dict[str, Any]) -> tuple[CovenantDefinition, CovenantEbitdaBridge, CovenantInputs]:
    definition_raw = raw.get("definition") or raw
    bridge_raw = raw.get("ebitda_bridge") or {}
    inputs_raw = raw.get("inputs") or {}

    kind = str(definition_raw.get("kind") or "").strip()
    if kind not in SUPPORTED_COVENANT_KINDS:
        raise ValueError(f"unsupported covenant kind: {kind!r}")
    name = str(definition_raw.get("name") or "").strip()
    if not name:
        raise ValueError("covenant name is required")
    threshold = _number(definition_raw.get("threshold"), "threshold", allow_none=False)
    assert threshold is not None
    if threshold < 0:
        raise ValueError("threshold cannot be negative")
    test_month = definition_raw.get("test_month", 0)
    if not isinstance(test_month, int) or isinstance(test_month, bool) or test_month < 0:
        raise ValueError("test_month must be a non-negative integer")

    springing_pct = _number(definition_raw.get("springing_revolver_drawn_pct"), "springing_revolver_drawn_pct")
    if springing_pct is not None and not 0 <= springing_pct <= 1:
        raise ValueError("springing_revolver_drawn_pct must be between 0 and 1")

    definition = CovenantDefinition(
        name=name,
        kind=kind,
        threshold=threshold,
        test_month=test_month,
        springing=bool(definition_raw.get("springing", False)),
        springing_revolver_drawn_pct=springing_pct,
        cure_available=definition_raw.get("cure_available"),
        cure_cost=float(definition_raw.get("cure_cost", 0.0)),
        source_refs=tuple(str(item) for item in definition_raw.get("source_refs", []) if str(item).strip()),
        notes=tuple(str(item) for item in definition_raw.get("notes", []) if str(item).strip()),
    )

    base_ebitda = _number(bridge_raw.get("base_ebitda"), "ebitda_bridge.base_ebitda", allow_none=False)
    assert base_ebitda is not None
    add_backs = {str(k): float(v) for k, v in (bridge_raw.get("add_backs") or {}).items()}
    deductions = {str(k): float(v) for k, v in (bridge_raw.get("deductions") or {}).items()}
    if any(value < 0 for value in (*add_backs.values(), *deductions.values())):
        raise ValueError("EBITDA add-backs/deductions must be non-negative; use the correct bucket for direction")
    bridge = CovenantEbitdaBridge(
        base_ebitda=base_ebitda,
        add_backs=add_backs,
        deductions=deductions,
        disputed_add_backs=tuple(str(item) for item in bridge_raw.get("disputed_add_backs", []) if str(item).strip()),
    )

    inputs = CovenantInputs(
        gross_debt=_number(inputs_raw.get("gross_debt"), "inputs.gross_debt"),
        unrestricted_cash=_number(inputs_raw.get("unrestricted_cash"), "inputs.unrestricted_cash"),
        cash_netting_cap=_number(inputs_raw.get("cash_netting_cap"), "inputs.cash_netting_cap"),
        cash_netting_allowed=bool(inputs_raw.get("cash_netting_allowed", True)),
        interest_expense=_number(inputs_raw.get("interest_expense"), "inputs.interest_expense"),
        fixed_charges=_number(inputs_raw.get("fixed_charges"), "inputs.fixed_charges"),
        liquidity=_number(inputs_raw.get("liquidity"), "inputs.liquidity"),
        revolver_drawn=_number(inputs_raw.get("revolver_drawn"), "inputs.revolver_drawn"),
        revolver_commitment=_number(inputs_raw.get("revolver_commitment"), "inputs.revolver_commitment"),
    )
    return definition, bridge, inputs


def validate_covenant_model_dict(raw: dict[str, Any]) -> str | None:
    try:
        covenant_model_from_dict(raw)
    except (TypeError, ValueError) as exc:
        return str(exc)
    return None


def _springing_applies(definition: CovenantDefinition, inputs: CovenantInputs) -> tuple[bool | None, str | None]:
    if not definition.springing:
        return True, None
    trigger = definition.springing_revolver_drawn_pct
    if trigger is None:
        return None, "springing covenant trigger is not modeled"
    if inputs.revolver_drawn is None or inputs.revolver_commitment is None or inputs.revolver_commitment <= 0:
        return None, "springing covenant requires revolver drawn and commitment amounts"
    usage = inputs.revolver_drawn / inputs.revolver_commitment
    return usage >= trigger, None


def _net_debt(inputs: CovenantInputs) -> float | None:
    if inputs.gross_debt is None:
        return None
    if not inputs.cash_netting_allowed:
        return inputs.gross_debt
    if inputs.unrestricted_cash is None:
        return None
    cash_netting = inputs.unrestricted_cash
    if inputs.cash_netting_cap is not None:
        cash_netting = min(cash_netting, inputs.cash_netting_cap)
    return inputs.gross_debt - cash_netting


def calculate_covenant_headroom(
    definition: CovenantDefinition,
    bridge: CovenantEbitdaBridge,
    inputs: CovenantInputs,
) -> CovenantHeadroomResult:
    applies, spring_warning = _springing_applies(definition, inputs)
    warnings: list[str] = []
    if spring_warning:
        warnings.append(spring_warning)
    if bridge.disputed_add_backs:
        warnings.append("disputed covenant EBITDA add-backs: " + ", ".join(bridge.disputed_add_backs))

    ebitda = bridge.covenant_ebitda
    net_debt = _net_debt(inputs)
    actual: float | None = None
    required_ebitda: float | None = None

    if definition.kind == "max_net_leverage":
        if net_debt is None:
            warnings.append("net debt is unresolved")
        elif ebitda <= 0:
            warnings.append("covenant EBITDA is non-positive; leverage ratio is not meaningful")
        else:
            actual = net_debt / ebitda
        if net_debt is not None and definition.threshold > 0:
            required_ebitda = net_debt / definition.threshold
    elif definition.kind == "max_total_leverage":
        if inputs.gross_debt is None:
            warnings.append("gross debt is unresolved")
        elif ebitda <= 0:
            warnings.append("covenant EBITDA is non-positive; leverage ratio is not meaningful")
        else:
            actual = inputs.gross_debt / ebitda
        if inputs.gross_debt is not None and definition.threshold > 0:
            required_ebitda = inputs.gross_debt / definition.threshold
    elif definition.kind == "min_interest_coverage":
        if inputs.interest_expense is None or inputs.interest_expense <= 0:
            warnings.append("interest expense is unresolved or non-positive")
        else:
            actual = ebitda / inputs.interest_expense
            required_ebitda = definition.threshold * inputs.interest_expense
    elif definition.kind == "min_fixed_charge_coverage":
        if inputs.fixed_charges is None or inputs.fixed_charges <= 0:
            warnings.append("fixed charges are unresolved or non-positive")
        else:
            actual = ebitda / inputs.fixed_charges
            required_ebitda = definition.threshold * inputs.fixed_charges
    elif definition.kind == "min_liquidity":
        if inputs.liquidity is None:
            warnings.append("liquidity is unresolved")
        else:
            actual = inputs.liquidity

    headroom: float | None = None
    breached: bool | None = None
    if applies is False:
        breached = False
    elif applies is True and actual is not None:
        if definition.kind.startswith("max_"):
            headroom = definition.threshold - actual
            breached = actual > definition.threshold
        else:
            headroom = actual - definition.threshold
            breached = actual < definition.threshold

    headroom_pct = None
    if headroom is not None and definition.threshold != 0:
        headroom_pct = headroom / abs(definition.threshold)

    ebitda_cushion = None
    if required_ebitda is not None:
        ebitda_cushion = ebitda - required_ebitda

    return CovenantHeadroomResult(
        name=definition.name,
        kind=definition.kind,
        threshold=definition.threshold,
        applies=applies,
        actual=actual,
        headroom=headroom,
        headroom_pct_of_threshold=headroom_pct,
        breached=breached,
        covenant_ebitda=ebitda,
        minimum_ebitda_to_comply=required_ebitda,
        ebitda_cushion=ebitda_cushion,
        net_debt=net_debt,
        test_month=definition.test_month,
        cure_available=definition.cure_available,
        cure_cost=definition.cure_cost,
        warnings=tuple(warnings),
    )


def covenant_risk_from_result(result: CovenantHeadroomResult) -> dict[str, Any]:
    notes = [
        f"kind={result.kind}",
        f"threshold={result.threshold:g}",
        f"actual={result.actual:g}" if result.actual is not None else "actual=unresolved",
        f"headroom={result.headroom:g}" if result.headroom is not None else "headroom=unresolved",
        f"covenant_ebitda={result.covenant_ebitda:g}",
    ]
    notes.extend(result.warnings)
    unresolved = result.applies is None or (result.applies is True and result.breached is None)
    return {
        "name": result.name,
        "breach_month_if_unremedied": result.test_month if result.applies is True and result.breached is True else None,
        "cure_available": result.cure_available,
        "cure_cost": result.cure_cost,
        "test_month": result.test_month,
        "unresolved": unresolved,
        "notes": notes,
    }


def covenant_model_to_dict(raw: dict[str, Any]) -> dict[str, Any]:
    definition, bridge, inputs = covenant_model_from_dict(raw)
    result = calculate_covenant_headroom(definition, bridge, inputs)
    return {
        "definition": asdict(definition),
        "ebitda_bridge": asdict(bridge),
        "inputs": asdict(inputs),
        "result": asdict(result),
        "screening_covenant_risk": covenant_risk_from_result(result),
    }

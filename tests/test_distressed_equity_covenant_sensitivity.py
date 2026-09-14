from distressed_equity.covenant_sensitivity import calculate_covenant_addback_sensitivity
from distressed_equity.covenants import CovenantDefinition, CovenantEbitdaBridge, CovenantInputs


def test_disputed_addbacks_can_flip_leverage_compliance():
    definition = CovenantDefinition(
        name="Maximum Net Leverage",
        kind="max_net_leverage",
        threshold=4.0,
        source_refs=("e1",),
    )
    bridge = CovenantEbitdaBridge(
        base_ebitda=100.0,
        add_backs={"restructuring": 30.0, "synergies": 20.0},
        disputed_add_backs=("restructuring", "synergies"),
    )
    inputs = CovenantInputs(gross_debt=500.0, unrestricted_cash=50.0)

    report = calculate_covenant_addback_sensitivity(definition, bridge, inputs)
    assert report.claimed_case.result.breached is False
    assert report.conservative_case.result.breached is True
    assert report.claimed_case.result.covenant_ebitda == 150.0
    assert report.conservative_case.result.covenant_ebitda == 100.0
    assert report.covenant_ebitda_range == (100.0, 150.0)
    assert report.classification == "disputed_addbacks_flip_outcome"
    assert report.breach_in_any_determinate_case is True
    assert report.breach_in_all_cases is False


def test_individual_exclusions_show_each_addback_impact():
    definition = CovenantDefinition(
        name="Maximum Total Leverage",
        kind="max_total_leverage",
        threshold=5.0,
    )
    bridge = CovenantEbitdaBridge(
        base_ebitda=100.0,
        add_backs={"a": 10.0, "b": 20.0},
        disputed_add_backs=("a", "b"),
    )
    report = calculate_covenant_addback_sensitivity(
        definition,
        bridge,
        CovenantInputs(gross_debt=500.0),
    )
    assert {case.label for case in report.individual_exclusion_cases} == {"exclude:a", "exclude:b"}
    ebitdas = {case.label: case.result.covenant_ebitda for case in report.individual_exclusion_cases}
    assert ebitdas["exclude:a"] == 120.0
    assert ebitdas["exclude:b"] == 110.0


def test_missing_disputed_name_is_visible_not_invented():
    definition = CovenantDefinition(name="Test", kind="max_total_leverage", threshold=4.0)
    bridge = CovenantEbitdaBridge(
        base_ebitda=100.0,
        add_backs={"known": 10.0},
        disputed_add_backs=("unknown",),
    )
    report = calculate_covenant_addback_sensitivity(
        definition,
        bridge,
        CovenantInputs(gross_debt=300.0),
    )
    assert report.disputed_add_backs == ()
    assert report.classification == "no_modeled_disputed_addbacks"
    assert any("unknown" in warning for warning in report.warnings)

from datetime import date

from distressed_equity.legal_name_alias import (
    alias_groups_from_graphs,
    build_legal_name_alias_graph_from_submissions,
)


def meta_payload():
    return {
        "name": "Meta Platforms, Inc.",
        "formerNames": [
            {
                "name": "Facebook Inc",
                "from": "2005-05-06T04:00:00.000Z",
                "to": "2021-10-27T04:00:00.000Z",
            }
        ],
    }


def test_cutoff_before_rename_does_not_leak_future_current_name():
    graph = build_legal_name_alias_graph_from_submissions(
        meta_payload(),
        cik="0001326801",
        analysis_date=date(2020, 12, 31),
    )
    assert graph.canonical_name_as_of == "Facebook Inc"
    assert graph.alias_names == ("facebook inc",)
    assert graph.transitions == ()
    assert any("withheld" in warning for warning in graph.warnings)
    record = graph.records[0]
    assert record.valid_to is None


def test_cutoff_after_rename_links_former_and_current_name():
    graph = build_legal_name_alias_graph_from_submissions(
        meta_payload(),
        cik="0001326801",
        analysis_date=date(2022, 12, 31),
    )
    assert graph.canonical_name_as_of == "Meta Platforms, Inc."
    assert set(graph.alias_names) == {"facebook inc", "meta platforms inc"}
    assert len(graph.transitions) == 1
    transition = graph.transitions[0]
    assert transition.from_name == "Facebook Inc"
    assert transition.to_name == "Meta Platforms, Inc."
    assert transition.effective_on == date(2021, 10, 27)


def test_multiple_completed_renames_preserve_chain_and_alias_group():
    payload = {
        "name": "Gamma Holdings Inc.",
        "formerNames": [
            {"name": "Alpha Inc.", "from": "2010-01-01", "to": "2015-01-01"},
            {"name": "Beta Inc.", "from": "2015-01-01", "to": "2020-01-01"},
        ],
    }
    graph = build_legal_name_alias_graph_from_submissions(
        payload,
        cik="1234567",
        analysis_date=date(2022, 1, 1),
    )
    assert [item.from_name for item in graph.transitions] == ["Alpha Inc.", "Beta Inc."]
    assert [item.to_name for item in graph.transitions] == ["Beta Inc.", "Gamma Holdings Inc."]
    groups = alias_groups_from_graphs((graph,))
    assert groups == (
        frozenset({"alpha inc", "beta inc", "gamma holdings inc"}),
    )


def test_future_former_name_interval_is_not_added_to_historical_aliases():
    payload = {
        "name": "Future Name Inc.",
        "formerNames": [
            {"name": "Old Name Inc.", "from": "2024-01-01", "to": "2026-01-01"},
        ],
    }
    graph = build_legal_name_alias_graph_from_submissions(
        payload,
        cik="1",
        analysis_date=date(2023, 12, 31),
    )
    assert graph.records == ()
    assert graph.transitions == ()

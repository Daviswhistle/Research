from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from distressed_equity.experiment_manifest import (
    build_experiment_manifest,
    experiment_manifest_to_dict,
)
from distressed_equity.experiment_manifest_diff import (
    compare_experiment_manifests,
    load_experiment_manifest,
)
from distressed_equity.experiment_manifest_diff_cli import main


def _manifest(path: Path, *, config: dict[str, object] | None = None, revision: str = "rev-a"):
    return build_experiment_manifest(
        pipeline="prior_calibration",
        input_files={"prices": path},
        config=config or {"min_drawdown": 0.60, "nested": {"window": 30}},
        code_revision=revision,
    )


def _write_output(path: Path, manifest) -> None:
    path.write_text(
        json.dumps({"result": {"n": 1}, "experiment_manifest": experiment_manifest_to_dict(manifest)}, indent=2),
        encoding="utf-8",
    )


def test_diff_reports_path_only_move_without_changing_identity(tmp_path: Path):
    first_dir = tmp_path / "a"
    second_dir = tmp_path / "b"
    first_dir.mkdir()
    second_dir.mkdir()
    first = first_dir / "prices.csv"
    second = second_dir / "renamed.csv"
    first.write_bytes(b"security_id,date,close\nA,2020-01-01,5\n")
    second.write_bytes(first.read_bytes())

    before = _manifest(first)
    after = _manifest(second)
    diff = compare_experiment_manifests(before, after)

    assert diff.data_config_changed is False
    assert diff.code_revision_changed is False
    assert diff.exact_experiment_comparable is True
    assert diff.experiment_changed is False
    assert diff.path_only_change_count == 1
    assert diff.change_categories == ("input_path_only",)
    assert len(diff.input_changes) == 1
    change = diff.input_changes[0]
    assert change.status == "path_only_changed"
    assert change.content_changed is False
    assert change.path_changed is True


def test_diff_separates_input_config_and_code_changes(tmp_path: Path):
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    first.write_bytes(b"a\n")
    second.write_bytes(b"a\nb\n")

    before = _manifest(first)
    after = _manifest(
        second,
        config={"min_drawdown": 0.70, "nested": {"window": 45}},
        revision="rev-b",
    )
    diff = compare_experiment_manifests(before, after)

    assert diff.data_config_changed is True
    assert diff.code_revision_changed is True
    assert diff.experiment_changed is True
    assert diff.change_categories == ("input_content", "config", "code_revision")
    assert diff.input_changes[0].status == "content_changed"
    assert {item.path for item in diff.config_changes} == {"/min_drawdown", "/nested/window"}
    assert {item.status for item in diff.config_changes} == {"changed"}


def test_diff_preserves_json_numeric_and_boolean_types(tmp_path: Path):
    source = tmp_path / "input.csv"
    source.write_text("x\n", encoding="utf-8")
    before = _manifest(
        source,
        config={"integer": 1, "flag": True, "series": [1]},
    )
    after = _manifest(
        source,
        config={"integer": 1.0, "flag": 1, "series": [1.0]},
    )

    diff = compare_experiment_manifests(before, after)

    assert diff.data_config_changed is True
    assert diff.change_categories == ("config",)
    assert {item.path for item in diff.config_changes} == {"/integer", "/flag", "/series"}
    assert all(item.status == "changed" for item in diff.config_changes)


def test_diff_with_unresolved_code_revision_does_not_guess_exact_identity(tmp_path: Path):
    source = tmp_path / "input.csv"
    source.write_text("x\n", encoding="utf-8")
    complete = _manifest(source, revision="rev-a")
    incomplete = replace(
        complete,
        code_revision=None,
        code_revision_source=None,
        reproducibility_complete=False,
        experiment_fingerprint=None,
        warnings=("code revision unavailable",),
    )

    diff = compare_experiment_manifests(complete, incomplete)

    assert diff.data_config_changed is False
    assert diff.code_revision_changed is True
    assert diff.exact_experiment_comparable is False
    assert diff.experiment_changed is None
    assert diff.change_categories == ("code_revision",)
    assert any("not comparable" in item for item in diff.warnings)


def test_loader_accepts_nested_analysis_output_and_rejects_tampered_manifest(tmp_path: Path):
    source = tmp_path / "input.csv"
    source.write_text("x\n", encoding="utf-8")
    manifest = _manifest(source)
    good = tmp_path / "good.json"
    _write_output(good, manifest)

    loaded = load_experiment_manifest(good)
    assert loaded.experiment_fingerprint == manifest.experiment_fingerprint

    tampered_payload = {"experiment_manifest": experiment_manifest_to_dict(manifest)}
    tampered_payload["experiment_manifest"]["config"]["min_drawdown"] = 0.99
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(tampered_payload), encoding="utf-8")
    with pytest.raises(ValueError, match="data_config_fingerprint does not match"):
        load_experiment_manifest(tampered)


def test_diff_cli_writes_machine_and_human_readable_change_breakdown(tmp_path: Path):
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    first.write_text("x\n", encoding="utf-8")
    second.write_text("x\ny\n", encoding="utf-8")
    before_json = tmp_path / "before.json"
    after_json = tmp_path / "after.json"
    output = tmp_path / "diff.json"
    markdown = tmp_path / "diff.md"
    _write_output(before_json, _manifest(first))
    _write_output(after_json, _manifest(second, revision="rev-b"))

    assert main([
        str(before_json),
        str(after_json),
        "--output", str(output),
        "--markdown-output", str(markdown),
    ]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["data_config_changed"] is True
    assert payload["code_revision_changed"] is True
    assert payload["experiment_changed"] is True
    assert payload["change_categories"] == ["input_content", "code_revision"]
    assert payload["input_changes"][0]["status"] == "content_changed"

    text = markdown.read_text(encoding="utf-8")
    assert "Experiment manifest diff" in text
    assert "input_content" in text
    assert "Code revision" in text
    assert "fingerprint-validated" in text


def test_diff_reports_added_and_removed_input_roles(tmp_path: Path):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    a.write_text("a\n", encoding="utf-8")
    b.write_text("b\n", encoding="utf-8")
    before = build_experiment_manifest(
        pipeline="population_coverage",
        input_files={"a": a},
        config={},
        code_revision="rev",
    )
    after = build_experiment_manifest(
        pipeline="population_coverage",
        input_files={"b": b},
        config={},
        code_revision="rev",
    )

    diff = compare_experiment_manifests(before, after)
    changes = {item.role: item.status for item in diff.input_changes}
    assert changes == {"a": "removed", "b": "added"}
    assert diff.change_categories == ("input_content",)

from __future__ import annotations

from pathlib import Path

import pytest

import distressed_equity.experiment_manifest as manifest_module
from distressed_equity.experiment_manifest import (
    build_experiment_manifest,
    experiment_manifest_to_dict,
)


def _config() -> dict[str, object]:
    return {
        "analysis_dates": ["2018-12-31", "2020-12-31"],
        "min_drawdown": 0.60,
        "credit_max_staleness_days": 30,
    }


def test_manifest_identity_ignores_paths_but_binds_input_bytes_config_and_revision(tmp_path: Path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first_a = first_dir / "securities.csv"
    first_b = first_dir / "prices.csv"
    second_a = second_dir / "renamed-securities.csv"
    second_b = second_dir / "renamed-prices.csv"
    first_a.write_bytes(b"security_id,symbol\nA,A\n")
    first_b.write_bytes(b"security_id,date,close\nA,2020-01-01,5\n")
    second_a.write_bytes(first_a.read_bytes())
    second_b.write_bytes(first_b.read_bytes())

    one = build_experiment_manifest(
        pipeline="population_coverage",
        input_files={"securities": first_a, "prices": first_b},
        config=_config(),
        code_revision="abc123",
    )
    two = build_experiment_manifest(
        pipeline="population_coverage",
        input_files={"securities": second_a, "prices": second_b},
        config=_config(),
        code_revision="abc123",
    )

    assert one.data_config_fingerprint == two.data_config_fingerprint
    assert one.experiment_fingerprint == two.experiment_fingerprint
    assert one.experiment_fingerprint is not None
    assert one.reproducibility_complete is True
    assert one.code_revision_source == "explicit"
    assert [item.path for item in one.input_files] != [item.path for item in two.input_files]

    changed_bytes = second_dir / "changed-prices.csv"
    changed_bytes.write_bytes(first_b.read_bytes() + b"B,2020-01-01,7\n")
    bytes_variant = build_experiment_manifest(
        pipeline="population_coverage",
        input_files={"securities": second_a, "prices": changed_bytes},
        config=_config(),
        code_revision="abc123",
    )
    assert bytes_variant.data_config_fingerprint != one.data_config_fingerprint
    assert bytes_variant.experiment_fingerprint != one.experiment_fingerprint

    config_variant = build_experiment_manifest(
        pipeline="population_coverage",
        input_files={"securities": second_a, "prices": second_b},
        config={**_config(), "min_drawdown": 0.70},
        code_revision="abc123",
    )
    assert config_variant.data_config_fingerprint != one.data_config_fingerprint

    revision_variant = build_experiment_manifest(
        pipeline="population_coverage",
        input_files={"securities": second_a, "prices": second_b},
        config=_config(),
        code_revision="def456",
    )
    assert revision_variant.data_config_fingerprint == one.data_config_fingerprint
    assert revision_variant.experiment_fingerprint != one.experiment_fingerprint


def test_manifest_role_order_does_not_change_identity(tmp_path: Path):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    a.write_text("a\n", encoding="utf-8")
    b.write_text("b\n", encoding="utf-8")

    first = build_experiment_manifest(
        pipeline="prior_calibration",
        input_files=(("a", a), ("b", b)),
        config={"x": [2, 1]},
        code_revision="rev",
    )
    second = build_experiment_manifest(
        pipeline="prior_calibration",
        input_files=(("b", b), ("a", a)),
        config={"x": [2, 1]},
        code_revision="rev",
    )
    assert first.data_config_fingerprint == second.data_config_fingerprint
    assert first.experiment_fingerprint == second.experiment_fingerprint


def test_manifest_withholds_exact_experiment_fingerprint_when_code_revision_unknown(tmp_path: Path, monkeypatch):
    source = tmp_path / "input.csv"
    source.write_text("x\n", encoding="utf-8")
    monkeypatch.delenv("GITHUB_SHA", raising=False)

    class _NoGit:
        def __call__(self, *args, **kwargs):
            raise OSError("git unavailable")

    monkeypatch.setattr(manifest_module.subprocess, "run", _NoGit())
    manifest = build_experiment_manifest(
        pipeline="population_coverage",
        input_files={"input": source},
        config={"cutoff": "2022-12-31"},
    )
    assert manifest.code_revision is None
    assert manifest.reproducibility_complete is False
    assert manifest.experiment_fingerprint is None
    assert manifest.data_config_fingerprint
    assert any("experiment_fingerprint is withheld" in item for item in manifest.warnings)


def test_manifest_validates_roles_files_and_config_serialization(tmp_path: Path):
    source = tmp_path / "input.csv"
    source.write_text("x\n", encoding="utf-8")

    with pytest.raises(ValueError, match="roles must be unique"):
        build_experiment_manifest(
            pipeline="x",
            input_files=(("same", source), ("same", source)),
            config={},
            code_revision="rev",
        )

    with pytest.raises(ValueError, match="does not exist"):
        build_experiment_manifest(
            pipeline="x",
            input_files={"missing": tmp_path / "missing.csv"},
            config={},
            code_revision="rev",
        )

    with pytest.raises(TypeError):
        build_experiment_manifest(
            pipeline="x",
            input_files={"input": source},
            config={"not_json": {1, 2}},
            code_revision="rev",
        )


def test_manifest_serializer_preserves_audit_paths_but_not_path_based_identity(tmp_path: Path):
    source = tmp_path / "input.csv"
    source.write_text("x\n", encoding="utf-8")
    manifest = build_experiment_manifest(
        pipeline="population_coverage",
        input_files={"input": source},
        config={"cutoff": "2022-12-31"},
        code_revision="rev",
    )
    payload = experiment_manifest_to_dict(manifest)
    assert payload["schema_version"] == 1
    assert payload["pipeline"] == "population_coverage"
    assert payload["code_revision"] == "rev"
    assert payload["reproducibility_complete"] is True
    assert payload["input_files"][0]["path"] == str(source)
    assert payload["input_files"][0]["size_bytes"] == len(source.read_bytes())
    assert len(payload["input_files"][0]["sha256"]) == 64
    assert len(payload["data_config_fingerprint"]) == 64
    assert len(payload["experiment_fingerprint"]) == 64

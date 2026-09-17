from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Iterable, Mapping


@dataclass(frozen=True)
class ExperimentInputFile:
    role: str
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ExperimentManifest:
    schema_version: int
    pipeline: str
    code_revision: str | None
    code_revision_source: str | None
    reproducibility_complete: bool
    input_files: tuple[ExperimentInputFile, ...]
    config: dict[str, object]
    data_config_fingerprint: str
    experiment_fingerprint: str | None
    warnings: tuple[str, ...]


def _sha256_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(char in "0123456789abcdef" for char in value)


def _canonical_input_rows(input_files: Iterable[ExperimentInputFile]) -> list[dict[str, object]]:
    return [
        {
            "role": item.role,
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
        }
        for item in sorted(input_files, key=lambda item: item.role)
    ]


def _data_config_payload(
    *,
    schema_version: int,
    pipeline: str,
    input_files: Iterable[ExperimentInputFile],
    config: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "pipeline": pipeline,
        "inputs": _canonical_input_rows(input_files),
        "config": dict(config),
    }


def resolve_code_revision(explicit: str | None = None) -> tuple[str | None, str | None]:
    if explicit is not None:
        clean = explicit.strip()
        if not clean:
            raise ValueError("explicit code revision cannot be blank")
        return clean, "explicit"

    github_sha = os.environ.get("GITHUB_SHA", "").strip()
    if github_sha:
        return github_sha, "GITHUB_SHA"

    repo_root = Path(__file__).resolve().parents[1]
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    revision = completed.stdout.strip()
    return (revision, "git") if revision else (None, None)


def build_experiment_manifest(
    *,
    pipeline: str,
    input_files: Mapping[str, str | Path] | Iterable[tuple[str, str | Path]],
    config: Mapping[str, object],
    code_revision: str | None = None,
) -> ExperimentManifest:
    clean_pipeline = pipeline.strip()
    if not clean_pipeline:
        raise ValueError("experiment pipeline cannot be blank")

    pairs = tuple(input_files.items() if isinstance(input_files, Mapping) else input_files)
    roles = [str(role).strip() for role, _ in pairs]
    if not roles or any(not role for role in roles):
        raise ValueError("experiment manifest requires non-blank input roles")
    if len(set(roles)) != len(roles):
        raise ValueError("experiment input roles must be unique")

    hashed_inputs: list[ExperimentInputFile] = []
    for role, raw_path in sorted(pairs, key=lambda item: str(item[0])):
        clean_role = str(role).strip()
        path = Path(raw_path)
        if not path.is_file():
            raise ValueError(f"experiment input file does not exist or is not a file: {clean_role}={path}")
        size, digest = _sha256_file(path)
        hashed_inputs.append(
            ExperimentInputFile(
                role=clean_role,
                path=str(path),
                size_bytes=size,
                sha256=digest,
            )
        )

    normalized_config = dict(config)
    data_config_fingerprint = _fingerprint(
        _data_config_payload(
            schema_version=1,
            pipeline=clean_pipeline,
            input_files=hashed_inputs,
            config=normalized_config,
        )
    )

    resolved_revision, revision_source = resolve_code_revision(code_revision)
    warnings: list[str] = []
    if resolved_revision is None:
        warnings.append(
            "code revision could not be resolved; data_config_fingerprint is valid but experiment_fingerprint is withheld"
        )
        experiment_fingerprint = None
    else:
        experiment_fingerprint = _fingerprint(
            {
                "data_config_fingerprint": data_config_fingerprint,
                "code_revision": resolved_revision,
            }
        )

    return ExperimentManifest(
        schema_version=1,
        pipeline=clean_pipeline,
        code_revision=resolved_revision,
        code_revision_source=revision_source,
        reproducibility_complete=experiment_fingerprint is not None,
        input_files=tuple(hashed_inputs),
        config=normalized_config,
        data_config_fingerprint=data_config_fingerprint,
        experiment_fingerprint=experiment_fingerprint,
        warnings=tuple(warnings),
    )


def experiment_manifest_from_dict(
    payload: Mapping[str, object],
    *,
    verify_fingerprints: bool = True,
) -> ExperimentManifest:
    """Parse a serialized manifest and optionally verify its canonical fingerprints.

    This validates the manifest itself, not the current filesystem contents. Input
    byte hashes are compared between manifests; reproducing the run still requires
    access to files whose bytes match those hashes.
    """

    schema_version = payload.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        raise ValueError("unsupported experiment manifest schema_version; expected 1")

    raw_pipeline = payload.get("pipeline")
    if not isinstance(raw_pipeline, str) or not raw_pipeline.strip():
        raise ValueError("experiment manifest pipeline must be a non-blank string")
    pipeline = raw_pipeline.strip()

    raw_inputs = payload.get("input_files")
    if not isinstance(raw_inputs, list) or not raw_inputs:
        raise ValueError("experiment manifest input_files must be a non-empty list")
    input_files: list[ExperimentInputFile] = []
    seen_roles: set[str] = set()
    for index, raw in enumerate(raw_inputs):
        if not isinstance(raw, dict):
            raise ValueError(f"experiment manifest input_files[{index}] must be an object")
        role = raw.get("role")
        path = raw.get("path")
        size_bytes = raw.get("size_bytes")
        sha256 = raw.get("sha256")
        if not isinstance(role, str) or not role.strip():
            raise ValueError(f"experiment manifest input_files[{index}].role must be non-blank")
        clean_role = role.strip()
        if clean_role in seen_roles:
            raise ValueError(f"duplicate experiment input role: {clean_role}")
        seen_roles.add(clean_role)
        if not isinstance(path, str) or not path:
            raise ValueError(f"experiment manifest input_files[{index}].path must be a string")
        if type(size_bytes) is not int or size_bytes < 0:
            raise ValueError(f"experiment manifest input_files[{index}].size_bytes must be a non-negative integer")
        if not _is_sha256(sha256):
            raise ValueError(f"experiment manifest input_files[{index}].sha256 must be a lowercase SHA-256 hex digest")
        input_files.append(
            ExperimentInputFile(
                role=clean_role,
                path=path,
                size_bytes=size_bytes,
                sha256=sha256,
            )
        )

    raw_config = payload.get("config")
    if not isinstance(raw_config, dict):
        raise ValueError("experiment manifest config must be an object")
    config = dict(raw_config)

    code_revision = payload.get("code_revision")
    if code_revision is not None and (not isinstance(code_revision, str) or not code_revision.strip()):
        raise ValueError("experiment manifest code_revision must be null or non-blank")
    if isinstance(code_revision, str):
        code_revision = code_revision.strip()

    code_revision_source = payload.get("code_revision_source")
    if code_revision_source is not None and (
        not isinstance(code_revision_source, str) or not code_revision_source.strip()
    ):
        raise ValueError("experiment manifest code_revision_source must be null or non-blank")
    if isinstance(code_revision_source, str):
        code_revision_source = code_revision_source.strip()
    if (code_revision is None) != (code_revision_source is None):
        raise ValueError("experiment manifest code revision and source must either both be present or both be null")

    reproducibility_complete = payload.get("reproducibility_complete")
    if type(reproducibility_complete) is not bool:
        raise ValueError("experiment manifest reproducibility_complete must be boolean")

    data_config_fingerprint = payload.get("data_config_fingerprint")
    if not _is_sha256(data_config_fingerprint):
        raise ValueError("experiment manifest data_config_fingerprint must be a lowercase SHA-256 hex digest")

    experiment_fingerprint = payload.get("experiment_fingerprint")
    if experiment_fingerprint is not None and not _is_sha256(experiment_fingerprint):
        raise ValueError("experiment manifest experiment_fingerprint must be null or a lowercase SHA-256 hex digest")

    raw_warnings = payload.get("warnings", [])
    if not isinstance(raw_warnings, list) or any(not isinstance(item, str) for item in raw_warnings):
        raise ValueError("experiment manifest warnings must be a list of strings")

    manifest = ExperimentManifest(
        schema_version=1,
        pipeline=pipeline,
        code_revision=code_revision,
        code_revision_source=code_revision_source,
        reproducibility_complete=reproducibility_complete,
        input_files=tuple(sorted(input_files, key=lambda item: item.role)),
        config=config,
        data_config_fingerprint=data_config_fingerprint,
        experiment_fingerprint=experiment_fingerprint,
        warnings=tuple(raw_warnings),
    )

    if verify_fingerprints:
        expected_data = _fingerprint(
            _data_config_payload(
                schema_version=manifest.schema_version,
                pipeline=manifest.pipeline,
                input_files=manifest.input_files,
                config=manifest.config,
            )
        )
        if manifest.data_config_fingerprint != expected_data:
            raise ValueError("experiment manifest data_config_fingerprint does not match serialized inputs/config")

        if manifest.code_revision is None:
            if manifest.experiment_fingerprint is not None or manifest.reproducibility_complete:
                raise ValueError("experiment manifest without code revision cannot claim a complete experiment fingerprint")
        else:
            expected_experiment = _fingerprint(
                {
                    "data_config_fingerprint": expected_data,
                    "code_revision": manifest.code_revision,
                }
            )
            if manifest.experiment_fingerprint != expected_experiment:
                raise ValueError("experiment manifest experiment_fingerprint does not match data/config fingerprint and code revision")
            if not manifest.reproducibility_complete:
                raise ValueError("experiment manifest with a verified experiment fingerprint must be reproducibility_complete")

    return manifest


def experiment_manifest_to_dict(manifest: ExperimentManifest) -> dict[str, object]:
    return {
        "schema_version": manifest.schema_version,
        "pipeline": manifest.pipeline,
        "code_revision": manifest.code_revision,
        "code_revision_source": manifest.code_revision_source,
        "reproducibility_complete": manifest.reproducibility_complete,
        "input_files": [asdict(item) for item in manifest.input_files],
        "config": manifest.config,
        "data_config_fingerprint": manifest.data_config_fingerprint,
        "experiment_fingerprint": manifest.experiment_fingerprint,
        "warnings": list(manifest.warnings),
    }

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
    canonical_inputs: list[dict[str, object]] = []
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
        # Paths are intentionally excluded from canonical identity so moving the
        # same bytes between machines/directories does not create a new experiment.
        canonical_inputs.append(
            {
                "role": clean_role,
                "size_bytes": size,
                "sha256": digest,
            }
        )

    normalized_config = dict(config)
    data_config_payload = {
        "schema_version": 1,
        "pipeline": clean_pipeline,
        "inputs": canonical_inputs,
        "config": normalized_config,
    }
    data_config_fingerprint = _fingerprint(data_config_payload)

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

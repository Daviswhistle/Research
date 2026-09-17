from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Mapping

from .experiment_manifest import (
    ExperimentManifest,
    experiment_manifest_from_dict,
    experiment_manifest_to_dict,
)


@dataclass(frozen=True)
class ExperimentInputChange:
    role: str
    status: str
    content_changed: bool
    path_changed: bool
    before_path: str | None
    after_path: str | None
    before_size_bytes: int | None
    after_size_bytes: int | None
    before_sha256: str | None
    after_sha256: str | None


@dataclass(frozen=True)
class ExperimentConfigChange:
    path: str
    status: str
    before: object
    after: object


@dataclass(frozen=True)
class ExperimentManifestDiff:
    schema_version: int
    before_pipeline: str
    after_pipeline: str
    pipeline_changed: bool
    before_data_config_fingerprint: str
    after_data_config_fingerprint: str
    data_config_changed: bool
    before_code_revision: str | None
    after_code_revision: str | None
    code_revision_changed: bool
    before_experiment_fingerprint: str | None
    after_experiment_fingerprint: str | None
    exact_experiment_comparable: bool
    experiment_changed: bool | None
    input_changes: tuple[ExperimentInputChange, ...]
    config_changes: tuple[ExperimentConfigChange, ...]
    path_only_change_count: int
    change_categories: tuple[str, ...]
    warnings: tuple[str, ...]


def _extract_manifest_payload(payload: object) -> Mapping[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("experiment JSON root must be an object")
    nested = payload.get("experiment_manifest")
    if nested is not None:
        if not isinstance(nested, dict):
            raise ValueError("experiment_manifest must be an object")
        return nested
    return payload


def load_experiment_manifest(path: str | Path) -> ExperimentManifest:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"experiment JSON does not exist: {source}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"experiment JSON is invalid: {source}: {exc}") from exc
    return experiment_manifest_from_dict(_extract_manifest_payload(payload), verify_fingerprints=True)


def _pointer_token(value: object) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _same_json_value(left: object, right: object) -> bool:
    """Compare JSON values with the same type-sensitive semantics as fingerprints.

    Python equality intentionally collapses some distinct JSON values (for example
    1 == 1.0 and True == 1). Canonical experiment identity does not, so the diff
    layer must compare canonical JSON representations rather than Python values.
    """

    return json.dumps(
        left,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ) == json.dumps(
        right,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _config_changes(
    before: Mapping[str, object],
    after: Mapping[str, object],
    *,
    prefix: str = "",
) -> tuple[ExperimentConfigChange, ...]:
    output: list[ExperimentConfigChange] = []
    for key in sorted(set(before) | set(after), key=str):
        path = f"{prefix}/{_pointer_token(key)}"
        if key not in before:
            output.append(
                ExperimentConfigChange(path=path, status="added", before=None, after=after[key])
            )
            continue
        if key not in after:
            output.append(
                ExperimentConfigChange(path=path, status="removed", before=before[key], after=None)
            )
            continue
        left = before[key]
        right = after[key]
        if isinstance(left, dict) and isinstance(right, dict):
            output.extend(_config_changes(left, right, prefix=path))
        elif not _same_json_value(left, right):
            output.append(
                ExperimentConfigChange(path=path, status="changed", before=left, after=right)
            )
    return tuple(output)


def compare_experiment_manifests(
    before: ExperimentManifest,
    after: ExperimentManifest,
) -> ExperimentManifestDiff:
    # Reparse the dataclasses through their serialized representation so callers
    # cannot bypass fingerprint validation by constructing an inconsistent object.
    before = experiment_manifest_from_dict(experiment_manifest_to_dict(before), verify_fingerprints=True)
    after = experiment_manifest_from_dict(experiment_manifest_to_dict(after), verify_fingerprints=True)

    before_inputs = {item.role: item for item in before.input_files}
    after_inputs = {item.role: item for item in after.input_files}
    input_changes: list[ExperimentInputChange] = []
    for role in sorted(set(before_inputs) | set(after_inputs)):
        left = before_inputs.get(role)
        right = after_inputs.get(role)
        if left is None:
            assert right is not None
            input_changes.append(
                ExperimentInputChange(
                    role=role,
                    status="added",
                    content_changed=True,
                    path_changed=True,
                    before_path=None,
                    after_path=right.path,
                    before_size_bytes=None,
                    after_size_bytes=right.size_bytes,
                    before_sha256=None,
                    after_sha256=right.sha256,
                )
            )
            continue
        if right is None:
            input_changes.append(
                ExperimentInputChange(
                    role=role,
                    status="removed",
                    content_changed=True,
                    path_changed=True,
                    before_path=left.path,
                    after_path=None,
                    before_size_bytes=left.size_bytes,
                    after_size_bytes=None,
                    before_sha256=left.sha256,
                    after_sha256=None,
                )
            )
            continue

        content_changed = left.size_bytes != right.size_bytes or left.sha256 != right.sha256
        path_changed = left.path != right.path
        if content_changed:
            status = "content_changed"
        elif path_changed:
            status = "path_only_changed"
        else:
            status = "unchanged"
        input_changes.append(
            ExperimentInputChange(
                role=role,
                status=status,
                content_changed=content_changed,
                path_changed=path_changed,
                before_path=left.path,
                after_path=right.path,
                before_size_bytes=left.size_bytes,
                after_size_bytes=right.size_bytes,
                before_sha256=left.sha256,
                after_sha256=right.sha256,
            )
        )

    config_changes = _config_changes(before.config, after.config)
    pipeline_changed = before.pipeline != after.pipeline
    data_config_changed = before.data_config_fingerprint != after.data_config_fingerprint
    code_revision_changed = before.code_revision != after.code_revision
    exact_experiment_comparable = (
        before.experiment_fingerprint is not None and after.experiment_fingerprint is not None
    )
    experiment_changed = (
        before.experiment_fingerprint != after.experiment_fingerprint
        if exact_experiment_comparable
        else None
    )

    categories: list[str] = []
    if pipeline_changed:
        categories.append("pipeline")
    if any(item.content_changed for item in input_changes):
        categories.append("input_content")
    if config_changes:
        categories.append("config")
    if code_revision_changed:
        categories.append("code_revision")
    path_only_change_count = sum(item.status == "path_only_changed" for item in input_changes)
    if path_only_change_count:
        categories.append("input_path_only")
    if not categories:
        categories.append("none")

    warnings: list[str] = []
    if pipeline_changed:
        warnings.append("manifests belong to different pipelines; component differences are still reported")
    if not exact_experiment_comparable:
        warnings.append(
            "at least one manifest lacks an exact code-bound experiment fingerprint; exact experiment identity is not comparable"
        )
    if data_config_changed and not (
        pipeline_changed
        or any(item.content_changed for item in input_changes)
        or config_changes
    ):
        # This should be impossible after fingerprint validation and is retained as
        # an explicit invariant check if the schema evolves.
        raise ValueError("data/config fingerprint changed without a corresponding pipeline, input, or config difference")

    return ExperimentManifestDiff(
        schema_version=1,
        before_pipeline=before.pipeline,
        after_pipeline=after.pipeline,
        pipeline_changed=pipeline_changed,
        before_data_config_fingerprint=before.data_config_fingerprint,
        after_data_config_fingerprint=after.data_config_fingerprint,
        data_config_changed=data_config_changed,
        before_code_revision=before.code_revision,
        after_code_revision=after.code_revision,
        code_revision_changed=code_revision_changed,
        before_experiment_fingerprint=before.experiment_fingerprint,
        after_experiment_fingerprint=after.experiment_fingerprint,
        exact_experiment_comparable=exact_experiment_comparable,
        experiment_changed=experiment_changed,
        input_changes=tuple(input_changes),
        config_changes=config_changes,
        path_only_change_count=path_only_change_count,
        change_categories=tuple(categories),
        warnings=tuple(warnings),
    )


def experiment_manifest_diff_to_dict(diff: ExperimentManifestDiff) -> dict[str, object]:
    return {
        "schema_version": diff.schema_version,
        "before_pipeline": diff.before_pipeline,
        "after_pipeline": diff.after_pipeline,
        "pipeline_changed": diff.pipeline_changed,
        "before_data_config_fingerprint": diff.before_data_config_fingerprint,
        "after_data_config_fingerprint": diff.after_data_config_fingerprint,
        "data_config_changed": diff.data_config_changed,
        "before_code_revision": diff.before_code_revision,
        "after_code_revision": diff.after_code_revision,
        "code_revision_changed": diff.code_revision_changed,
        "before_experiment_fingerprint": diff.before_experiment_fingerprint,
        "after_experiment_fingerprint": diff.after_experiment_fingerprint,
        "exact_experiment_comparable": diff.exact_experiment_comparable,
        "experiment_changed": diff.experiment_changed,
        "input_changes": [asdict(item) for item in diff.input_changes],
        "config_changes": [asdict(item) for item in diff.config_changes],
        "path_only_change_count": diff.path_only_change_count,
        "change_categories": list(diff.change_categories),
        "warnings": list(diff.warnings),
    }

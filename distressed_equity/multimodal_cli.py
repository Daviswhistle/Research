from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from typing import Any

from .multimodal_runner import (
    DEFAULT_ALLOWED_SOURCE_HOSTS,
    OpenAIResponsesMultimodalProvider,
    run_multimodal_source_task,
)
from .multimodal_source_reader import multimodal_source_task_from_dict
from .multimodal_source_results import (
    multimodal_verification_template,
    promote_verified_multimodal_result,
)


def _load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _safe_name(task_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", task_id).strip("._")
    return cleaned or "multimodal-source"


def _manifest_from_packet(packet: dict[str, Any]) -> dict[str, Any]:
    source_packet = packet.get("debt_instrument_source_packet")
    if not isinstance(source_packet, dict):
        raise ValueError("packet has no debt_instrument_source_packet")
    manifest = source_packet.get("multimodal_source_reading")
    if not isinstance(manifest, dict):
        raise ValueError("packet has no multimodal_source_reading manifest")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run and verify visual SEC source-reading tasks"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser(
        "run",
        help="Execute frozen visual-source tasks with a multimodal provider",
    )
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("--workspace", help="Research workspace containing research_packet.json")
    source.add_argument("--packet", help="Path to a frozen research_packet.json")
    run.add_argument("--output-dir")
    run.add_argument("--task-id", action="append", default=[])
    run.add_argument("--provider", choices=("openai",), default="openai")
    run.add_argument(
        "--model",
        default=os.getenv("OPENAI_MULTIMODAL_MODEL", "gpt-5.6-sol"),
        help="OpenAI model ID; defaults to OPENAI_MULTIMODAL_MODEL or gpt-5.6-sol",
    )
    run.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY"))
    run.add_argument(
        "--base-url",
        default=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    )
    run.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high", "xhigh", "max"),
        default="high",
    )
    run.add_argument("--provider-timeout", type=float, default=180.0)
    run.add_argument("--source-timeout", type=float, default=30.0)
    run.add_argument("--max-source-bytes", type=int, default=25_000_000)
    run.add_argument("--max-embedded-image-bytes", type=int, default=8_000_000)
    run.add_argument("--max-embedded-images", type=int, default=8)
    run.add_argument(
        "--allow-source-host",
        action="append",
        default=[],
        help="Additional HTTPS host allowed for source or embedded image downloads",
    )
    run.add_argument("--user-agent", default=os.getenv("SEC_USER_AGENT"))
    run.add_argument("--overwrite", action="store_true")

    verify = sub.add_parser(
        "verification-template",
        help="Create an independent-verification checklist for an existing result",
    )
    verify.add_argument("--result", required=True)
    verify.add_argument("-o", "--output", required=True)

    promote = sub.add_parser(
        "promote",
        help="Promote only independently verified engine patches into agent ingestion JSON",
    )
    promote.add_argument("--result", required=True)
    promote.add_argument("--verification", required=True)
    promote.add_argument("-o", "--output", required=True)

    return parser


def _task_from_result(result: dict[str, Any]):
    snapshot = result.get("task_snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError(
            "result has no task_snapshot; generate it with 'run' or attach the frozen task snapshot"
        )
    return multimodal_source_task_from_dict(snapshot)


def _run(args: argparse.Namespace) -> int:
    if args.workspace:
        workspace = Path(args.workspace)
        packet_path = workspace / "research_packet.json"
        output_dir = Path(args.output_dir) if args.output_dir else workspace / "multimodal_results"
    else:
        packet_path = Path(args.packet)
        output_dir = (
            Path(args.output_dir)
            if args.output_dir
            else packet_path.parent / "multimodal_results"
        )
    packet = _load_json(packet_path)
    manifest = _manifest_from_packet(packet)
    entries = manifest.get("tasks")
    if not isinstance(entries, list):
        raise ValueError("multimodal_source_reading.tasks must be a list")

    selected_ids = set(args.task_id)
    tasks = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("task"), dict):
            continue
        task = multimodal_source_task_from_dict(entry["task"])
        if selected_ids and task.task_id not in selected_ids:
            continue
        tasks.append(task)
    if selected_ids:
        found = {task.task_id for task in tasks}
        missing = sorted(selected_ids - found)
        if missing:
            raise ValueError("requested task IDs not found in frozen manifest: " + ", ".join(missing))
    if not tasks:
        print("No multimodal source tasks selected.")
        return 0

    if args.provider == "openai":
        if not args.api_key:
            raise ValueError("OPENAI_API_KEY or --api-key is required for provider=openai")
        provider = OpenAIResponsesMultimodalProvider(
            api_key=args.api_key,
            model=args.model,
            base_url=args.base_url,
            reasoning_effort=(
                None if args.reasoning_effort == "none" else args.reasoning_effort
            ),
            timeout=args.provider_timeout,
        )
    else:
        raise ValueError(f"unsupported provider: {args.provider}")

    allowed_hosts = frozenset(
        host.strip().lower()
        for host in (*DEFAULT_ALLOWED_SOURCE_HOSTS, *args.allow_source_host)
        if host.strip()
    )
    user_agent = str(args.user_agent or "").strip()
    if not user_agent:
        raise ValueError("SEC_USER_AGENT or --user-agent is required for SEC source downloads")
    output_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for task in tasks:
        stem = _safe_name(task.task_id)
        result_path = output_dir / f"{stem}.json"
        verification_path = output_dir / f"{stem}.verification.json"
        error_path = output_dir / f"{stem}.error.json"
        if result_path.exists() and not args.overwrite:
            print(f"skip existing: {result_path}")
            continue
        try:
            result = run_multimodal_source_task(
                task,
                provider,
                allowed_hosts=allowed_hosts,
                user_agent=user_agent,
                timeout=args.source_timeout,
                max_source_bytes=args.max_source_bytes,
                max_embedded_image_bytes=args.max_embedded_image_bytes,
                max_embedded_images=args.max_embedded_images,
            )
            verification = multimodal_verification_template(task, result)
            _write_json(result_path, result)
            _write_json(verification_path, verification)
            if error_path.exists():
                error_path.unlink()
            print(result_path)
        except Exception as exc:
            failures += 1
            _write_json(
                error_path,
                {
                    "task_id": task.task_id,
                    "analysis_date": task.analysis_date.isoformat(),
                    "source_url": task.source_url,
                    "error": str(exc),
                },
            )
            print(error_path)
    return 2 if failures else 0


def _verification_template(args: argparse.Namespace) -> int:
    result = _load_json(args.result)
    task = _task_from_result(result)
    verification = multimodal_verification_template(task, result)
    _write_json(Path(args.output), verification)
    print(args.output)
    return 0


def _promote(args: argparse.Namespace) -> int:
    result = _load_json(args.result)
    verification = _load_json(args.verification)
    task = _task_from_result(result)
    promoted = promote_verified_multimodal_result(task, result, verification)
    _write_json(Path(args.output), promoted)
    print(args.output)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return _run(args)
    if args.command == "verification-template":
        return _verification_template(args)
    if args.command == "promote":
        return _promote(args)
    raise ValueError(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

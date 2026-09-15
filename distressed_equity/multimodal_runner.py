from __future__ import annotations

from dataclasses import dataclass
import base64
import binascii
from html.parser import HTMLParser
import json
import mimetypes
from pathlib import PurePosixPath
from typing import Any, Callable, Protocol
from urllib.parse import unquote_to_bytes, urljoin, urlparse

import requests

from .multimodal_source_reader import (
    MultimodalSourceTask,
    multimodal_source_task_to_dict,
)
from .multimodal_source_results import (
    multimodal_result_schema,
    validate_multimodal_source_result,
)


DEFAULT_ALLOWED_SOURCE_HOSTS = frozenset({"www.sec.gov", "sec.gov"})


@dataclass(frozen=True)
class SourceAsset:
    name: str
    url: str
    mime_type: str
    data: bytes
    kind: str


@dataclass(frozen=True)
class SourceAssetBundle:
    assets: tuple[SourceAsset, ...]
    warnings: tuple[str, ...] = ()


class MultimodalProvider(Protocol):
    provider_name: str
    model_name: str

    def read(
        self,
        task: MultimodalSourceTask,
        *,
        assets: tuple[SourceAsset, ...],
        prompt: str,
        result_schema: dict[str, Any],
    ) -> dict[str, Any]:
        ...


class _ImageSrcParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sources: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.lower() != "img":
            return
        values = {key.lower(): value for key, value in attrs}
        src = values.get("src")
        if src:
            self.sources.append(src)


def _allowed_url(url: str, allowed_hosts: frozenset[str]) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in allowed_hosts


def _read_response_bytes(response: requests.Response, max_bytes: int) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            declared = int(content_length)
        except ValueError:
            declared = None
        if declared is not None and declared > max_bytes:
            raise ValueError(
                f"source exceeds max_bytes before download: {declared} > {max_bytes}"
            )
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        size += len(chunk)
        if size > max_bytes:
            raise ValueError(f"source exceeds max_bytes while downloading: {size} > {max_bytes}")
        chunks.append(chunk)
    return b"".join(chunks)


def _mime_type(url: str, response: requests.Response) -> str:
    header = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if header:
        return header
    guessed, _ = mimetypes.guess_type(url)
    return guessed or "application/octet-stream"


def _name_from_url(url: str, fallback: str) -> str:
    path = PurePosixPath(urlparse(url).path)
    name = path.name
    return name or fallback


def _fetch_one(
    session: requests.Session,
    url: str,
    *,
    allowed_hosts: frozenset[str],
    user_agent: str,
    timeout: float,
    max_bytes: int,
) -> tuple[bytes, str, str]:
    if not _allowed_url(url, allowed_hosts):
        raise ValueError(f"source URL is not an allowed HTTPS host: {url}")
    response = session.get(
        url,
        headers={"User-Agent": user_agent},
        timeout=timeout,
        stream=True,
    )
    response.raise_for_status()
    final_url = str(getattr(response, "url", None) or url)
    if not _allowed_url(final_url, allowed_hosts):
        raise ValueError(f"source redirect left the allowed host set: {final_url}")
    data = _read_response_bytes(response, max_bytes=max_bytes)
    return data, _mime_type(final_url, response), final_url


def _data_image_asset(src: str, ordinal: int) -> SourceAsset | None:
    if not src.startswith("data:image/") or "," not in src:
        return None
    header, payload = src.split(",", 1)
    meta = header[5:]
    mime = meta.split(";", 1)[0].strip().lower()
    try:
        if ";base64" in header:
            data = base64.b64decode(payload, validate=True)
        else:
            data = unquote_to_bytes(payload)
    except (ValueError, binascii.Error):
        return None
    ext = mimetypes.guess_extension(mime) or ".img"
    return SourceAsset(
        name=f"embedded-{ordinal}{ext}",
        url=f"embedded:data-image:{ordinal}",
        mime_type=mime,
        data=data,
        kind="image",
    )


def fetch_source_assets(
    task: MultimodalSourceTask,
    *,
    session: requests.Session | None = None,
    allowed_hosts: frozenset[str] = DEFAULT_ALLOWED_SOURCE_HOSTS,
    user_agent: str = "Research multimodal-source-reader",
    timeout: float = 30.0,
    max_source_bytes: int = 25_000_000,
    max_embedded_image_bytes: int = 8_000_000,
    max_embedded_images: int = 8,
) -> SourceAssetBundle:
    """Download the frozen SEC source and, for image-heavy HTML, its embedded images."""

    session = session or requests.Session()
    data, mime, final_url = _fetch_one(
        session,
        task.source_url,
        allowed_hosts=allowed_hosts,
        user_agent=user_agent,
        timeout=timeout,
        max_bytes=max_source_bytes,
    )
    if ("pdf" in task.media_type.lower() or task.document_name.lower().endswith(".pdf")) and data.startswith(b"%PDF"):
        mime = "application/pdf"
    kind = "image" if mime.startswith("image/") else "file"
    assets: list[SourceAsset] = [
        SourceAsset(
            name=task.document_name or _name_from_url(final_url, "source"),
            url=final_url,
            mime_type=mime,
            data=data,
            kind=kind,
        )
    ]
    warnings: list[str] = []

    if mime in {"text/html", "application/xhtml+xml"}:
        try:
            html = data.decode("utf-8", errors="replace")
            parser = _ImageSrcParser()
            parser.feed(html)
        except Exception as exc:
            warnings.append(f"could not inspect embedded HTML images: {exc}")
            return SourceAssetBundle(tuple(assets), tuple(warnings))

        seen: set[str] = set()
        for ordinal, src in enumerate(parser.sources, start=1):
            if len(assets) - 1 >= max_embedded_images:
                warnings.append(
                    f"embedded image limit reached ({max_embedded_images}); remaining images were not sent"
                )
                break
            if src in seen:
                continue
            seen.add(src)
            data_asset = _data_image_asset(src, ordinal)
            if data_asset is not None:
                if len(data_asset.data) <= max_embedded_image_bytes:
                    assets.append(data_asset)
                else:
                    warnings.append(
                        f"embedded data image {ordinal} exceeded byte limit and was skipped"
                    )
                continue
            image_url = urljoin(final_url, src)
            if not _allowed_url(image_url, allowed_hosts):
                warnings.append(f"embedded image outside allowed hosts skipped: {image_url}")
                continue
            try:
                image_data, image_mime, image_final_url = _fetch_one(
                    session,
                    image_url,
                    allowed_hosts=allowed_hosts,
                    user_agent=user_agent,
                    timeout=timeout,
                    max_bytes=max_embedded_image_bytes,
                )
            except Exception as exc:
                warnings.append(f"embedded image fetch failed: {image_url}: {exc}")
                continue
            if not image_mime.startswith("image/"):
                warnings.append(
                    f"embedded image URL returned non-image content and was skipped: {image_final_url}"
                )
                continue
            assets.append(
                SourceAsset(
                    name=_name_from_url(image_final_url, f"embedded-{ordinal}"),
                    url=image_final_url,
                    mime_type=image_mime,
                    data=image_data,
                    kind="image",
                )
            )

    return SourceAssetBundle(tuple(assets), tuple(warnings))


def build_multimodal_prompt(task: MultimodalSourceTask) -> str:
    questions = "\n".join(f"- {item}" for item in task.questions)
    guardrails = "\n".join(f"- {item}" for item in task.guardrails)
    return (
        f"Company: {task.company_name}\n"
        f"Historical analysis cutoff: {task.analysis_date.isoformat()}\n"
        f"SEC source: {task.source_accession} / {task.document_name}\n"
        f"Deterministic deferral reason: {task.reason}\n\n"
        f"Objective:\n{task.objective}\n\n"
        f"Research questions:\n{questions}\n\n"
        f"Guardrails:\n{guardrails}\n\n"
        "Return only the requested structured result. Findings must be material to the "
        "survival/existing-common thesis. Engine patches are proposals, never approvals. "
        "A patch must cite the exact finding IDs and visual evidence IDs that support it. "
        "For a PDF, every visual evidence item must identify a page number and precise region. "
        "For image-heavy HTML, use the supplied embedded images as visual evidence and preserve "
        "the surrounding HTML context. Do not infer facts from information published after the cutoff."
    )


def _response_output_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    parts: list[str] = []
    for item in payload.get("output", []) or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []) or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                parts.append(content["text"])
    text = "".join(parts).strip()
    if not text:
        raise ValueError("provider response contained no output_text")
    return text


class OpenAIResponsesMultimodalProvider:
    provider_name = "openai-responses"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        reasoning_effort: str | None = "high",
        timeout: float = 180.0,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAI API key is required")
        if not model:
            raise ValueError("OpenAI model is required")
        self.api_key = api_key
        self.model_name = model
        self.base_url = base_url.rstrip("/")
        self.reasoning_effort = reasoning_effort
        self.timeout = timeout
        self.session = session or requests.Session()

    def _content_for_assets(self, assets: tuple[SourceAsset, ...]) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = []
        for ordinal, asset in enumerate(assets, start=1):
            encoded = base64.b64encode(asset.data).decode("ascii")
            if asset.kind == "image" or asset.mime_type.startswith("image/"):
                content.append(
                    {
                        "type": "input_text",
                        "text": f"Visual asset {ordinal}: {asset.url}",
                    }
                )
                content.append(
                    {
                        "type": "input_image",
                        "image_url": f"data:{asset.mime_type};base64,{encoded}",
                        "detail": "high",
                    }
                )
                continue
            item: dict[str, Any] = {
                "type": "input_file",
                "filename": asset.name,
                "file_data": f"data:{asset.mime_type};base64,{encoded}",
            }
            if asset.mime_type == "application/pdf":
                item["detail"] = "high"
            content.append(item)
        return content

    def read(
        self,
        task: MultimodalSourceTask,
        *,
        assets: tuple[SourceAsset, ...],
        prompt: str,
        result_schema: dict[str, Any],
    ) -> dict[str, Any]:
        content = [{"type": "input_text", "text": prompt}]
        content.extend(self._content_for_assets(assets))
        body: dict[str, Any] = {
            "model": self.model_name,
            "store": False,
            "input": [{"role": "user", "content": content}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "multimodal_source_result",
                    "strict": False,
                    "schema": result_schema,
                }
            },
        }
        if self.reasoning_effort:
            body["reasoning"] = {"effort": self.reasoning_effort}
        response = self.session.post(
            f"{self.base_url}/responses",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") in {"failed", "cancelled"}:
            raise ValueError(f"OpenAI response failed: {payload.get('error') or payload.get('status')}")
        text = _response_output_text(payload)
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"OpenAI response was not valid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError("OpenAI structured response must be a JSON object")
        return raw


AssetLoader = Callable[[MultimodalSourceTask], SourceAssetBundle]


def run_multimodal_source_task(
    task: MultimodalSourceTask,
    provider: MultimodalProvider,
    *,
    asset_loader: AssetLoader | None = None,
    allowed_hosts: frozenset[str] = DEFAULT_ALLOWED_SOURCE_HOSTS,
    user_agent: str = "Research multimodal-source-reader",
    timeout: float = 30.0,
    max_source_bytes: int = 25_000_000,
    max_embedded_image_bytes: int = 8_000_000,
    max_embedded_images: int = 8,
) -> dict[str, Any]:
    if asset_loader is None:
        bundle = fetch_source_assets(
            task,
            allowed_hosts=allowed_hosts,
            user_agent=user_agent,
            timeout=timeout,
            max_source_bytes=max_source_bytes,
            max_embedded_image_bytes=max_embedded_image_bytes,
            max_embedded_images=max_embedded_images,
        )
    else:
        bundle = asset_loader(task)
    if not bundle.assets:
        raise ValueError("multimodal source task has no source assets")
    result = provider.read(
        task,
        assets=bundle.assets,
        prompt=build_multimodal_prompt(task),
        result_schema=multimodal_result_schema(task),
    )
    validation = validate_multimodal_source_result(task, result)
    if not validation.valid:
        raise ValueError("invalid multimodal provider result: " + "; ".join(validation.errors))
    output = dict(result)
    output["task_snapshot"] = multimodal_source_task_to_dict(task)
    output["run_metadata"] = {
        "provider": provider.provider_name,
        "model": provider.model_name,
        "asset_count": len(bundle.assets),
        "asset_urls": [asset.url for asset in bundle.assets],
        "source_warnings": list(bundle.warnings),
        "validation_warnings": list(validation.warnings),
    }
    return output

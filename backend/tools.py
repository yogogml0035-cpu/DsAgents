from __future__ import annotations

import json
import mimetypes
import os
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import urljoin

import requests
from dotenv import load_dotenv
from langgraph.config import get_stream_writer

from resources import ResourceConfig

load_dotenv(Path(__file__).with_name(".env"))

MINERU_POLL_INTERVAL_SECONDS = 30.0
ToolHandler = Callable[..., Any]


@dataclass(frozen=True)
class ToolCatalog:
    handlers: tuple[ToolHandler, ...]

    def as_list(self) -> list[ToolHandler]:
        return list(self.handlers)


def parse_document(
    file_path: str,
    output_path: str | None = None,
) -> str:
    """Parse a local document and write the returned markdown to a local file."""
    source = _resolve_document_path(file_path)
    if not source.is_file():
        raise FileNotFoundError(f"File not found: {source}")

    target = _resolve_document_path(output_path) if output_path else _default_output_path(source)
    target.parent.mkdir(parents=True, exist_ok=True)

    base_url = _required_env("MINERU_BASE_URL")
    backend = _required_env("MINERU_BACKEND")
    effort = os.getenv("MINERU_EFFORT") or ""
    timeout_seconds = int(_required_env("MINERU_TIMEOUT_SECONDS"))
    writer = _stream_writer()

    task = _submit_mineru_task(
        source,
        base_url=base_url,
        backend=backend,
        effort=effort,
        timeout_seconds=timeout_seconds,
    )
    _emit_parse_document_status(
        writer,
        status="submitted",
        task=task,
        file_path=str(source),
        output_path=str(target),
    )
    status_payload = _wait_for_mineru_completion(
        task,
        timeout_seconds=timeout_seconds,
        file_path=str(source),
        output_path=str(target),
        writer=writer,
    )
    try:
        markdown = _fetch_mineru_markdown(task["result_url"], timeout_seconds=timeout_seconds)
        target.write_text(markdown, encoding="utf-8")
    except Exception as exc:
        _emit_parse_document_status(
            writer,
            status="failed",
            task=task,
            file_path=str(source),
            output_path=str(target),
            queued_ahead=_queued_ahead(status_payload),
            error=str(exc).strip() or exc.__class__.__name__,
        )
        raise
    _emit_parse_document_status(
        writer,
        status="completed",
        task=task,
        file_path=str(source),
        output_path=str(target),
        queued_ahead=_queued_ahead(status_payload),
    )

    return json.dumps(
        {
            "task_id": task["task_id"],
            "source": str(source),
            "output_path": str(target),
            "markdown_bytes": len(markdown.encode("utf-8")),
            "status_url": task["status_url"],
            "result_url": task["result_url"],
        },
        ensure_ascii=False,
    )


def _default_output_path(source: Path) -> Path:
    return Path(__file__).resolve().parent / "data" / "document_outputs" / f"{source.stem}.md"


def _resolve_document_path(raw_path: str | None) -> Path:
    if not raw_path:
        raise ValueError("Path is required")
    if raw_path == "/artifacts" or raw_path.startswith("/artifacts/"):
        virtual_path = PurePosixPath(raw_path)
        if ".." in virtual_path.parts:
            raise ValueError(f"Invalid /artifacts path: {raw_path}")
        relative = virtual_path.relative_to("/artifacts")
        return _artifacts_root().joinpath(*relative.parts).resolve()
    return Path(raw_path).expanduser().resolve()


def _artifacts_root() -> Path:
    return ResourceConfig().artifacts_dir.resolve()


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _submit_mineru_task(
    source: Path,
    *,
    base_url: str,
    backend: str,
    effort: str,
    timeout_seconds: int,
) -> dict[str, str]:
    mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    with source.open("rb") as handle:
        response = requests.post(
            f"{base_url}/tasks",
            files=[("files", (source.name, handle, mime))],
            data={
                "backend": backend,
                "effort": effort,
                "return_md": "true",
                "response_format_zip": "false",
            },
            timeout=timeout_seconds,
        )
    response.raise_for_status()
    payload = _json_or_text(response)
    if not isinstance(payload, dict):
        raise RuntimeError(f"MinerU task response was not a JSON object: {payload!r}")
    task_id = payload.get("task_id")
    status_url = payload.get("status_url")
    result_url = payload.get("result_url")
    if not isinstance(task_id, str) or not isinstance(status_url, str) or not isinstance(result_url, str):
        raise RuntimeError(
            "MinerU task response must include string task_id/status_url/result_url: "
            f"{payload!r}"
        )
    return {
        "task_id": task_id,
        "status_url": _mineru_url(base_url, status_url),
        "result_url": _mineru_url(base_url, result_url),
    }


def _wait_for_mineru_completion(
    task: dict[str, str],
    *,
    timeout_seconds: int,
    file_path: str,
    output_path: str,
    writer: Callable[[Any], None] | None,
    poll_interval_seconds: float = MINERU_POLL_INTERVAL_SECONDS,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_status: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        status_response = requests.get(task["status_url"], timeout=timeout_seconds)
        status_response.raise_for_status()
        payload = _json_or_text(status_response)
        if not isinstance(payload, dict):
            raise RuntimeError(f"MinerU task status response was not a JSON object: {payload!r}")
        last_status = payload
        status = payload.get("status")
        if not isinstance(status, str):
            raise RuntimeError(f"MinerU task status response did not include status: {payload!r}")
        normalized_status = status.lower()
        if normalized_status == "completed":
            return payload
        if normalized_status in {"pending", "processing"}:
            _emit_parse_document_status(
                writer,
                status=normalized_status,
                task=task,
                file_path=file_path,
                output_path=output_path,
                queued_ahead=_queued_ahead(payload),
            )
            time_left = deadline - time.monotonic()
            if time_left > 0:
                time.sleep(min(poll_interval_seconds, time_left))
            continue
        if normalized_status == "failed":
            error_text = _mineru_error_text(payload)
        else:
            error_text = f"Unexpected MinerU task status: {status}. Payload: {payload!r}"
        _emit_parse_document_status(
            writer,
            status="failed",
            task=task,
            file_path=file_path,
            output_path=output_path,
            queued_ahead=_queued_ahead(payload),
            error=error_text,
        )
        raise RuntimeError(error_text)
    error_text = f"MinerU task {task['task_id']} timed out. Last status: {last_status!r}"
    _emit_parse_document_status(
        writer,
        status="failed",
        task=task,
        file_path=file_path,
        output_path=output_path,
        queued_ahead=_queued_ahead(last_status),
        error=error_text,
    )
    raise TimeoutError(error_text)


def _fetch_mineru_markdown(result_url: str, *, timeout_seconds: int) -> str:
    result_response = requests.get(result_url, timeout=timeout_seconds)
    result_response.raise_for_status()
    return _extract_markdown(_json_or_text(result_response))


def _json_or_text(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def _extract_markdown(value: Any) -> str:
    if isinstance(value, dict):
        results = value.get("results")
        if isinstance(results, dict):
            for first_result in results.values():
                if not isinstance(first_result, dict):
                    continue
                markdown = first_result.get("md_content")
                if isinstance(markdown, str):
                    return markdown
    raise RuntimeError(f"MinerU result did not include markdown content: {value!r}")


def _mineru_url(base_url: str, raw_url: str) -> str:
    return urljoin(f"{base_url.rstrip('/')}/", raw_url)


def _queued_ahead(value: Any) -> int | None:
    if not isinstance(value, dict):
        return None
    queued_ahead = value.get("queued_ahead")
    if isinstance(queued_ahead, int) and not isinstance(queued_ahead, bool):
        return queued_ahead
    return None


def _mineru_error_text(payload: dict[str, Any]) -> str:
    for key in ("error", "message", "detail"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return f"MinerU task failed: {payload!r}"


def _stream_writer() -> Callable[[Any], None] | None:
    try:
        return get_stream_writer()
    except (KeyError, RuntimeError):
        return None


def _emit_parse_document_status(
    writer: Callable[[Any], None] | None,
    *,
    status: str,
    task: dict[str, str],
    file_path: str,
    output_path: str,
    queued_ahead: int | None = None,
    error: str | None = None,
) -> None:
    if writer is None:
        return
    payload: dict[str, Any] = {
        "name": "parse_document",
        "status": status,
        "task_id": task["task_id"],
        "file_path": file_path,
        "output_path": output_path,
        "status_url": task["status_url"],
        "result_url": task["result_url"],
        "queued_ahead": queued_ahead,
    }
    if error is not None:
        payload["error"] = error
    writer(payload)


def default_tool_catalog() -> ToolCatalog:
    return ToolCatalog((parse_document,))

from __future__ import annotations

import mimetypes
import os
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Sequence
from urllib.parse import urljoin

import requests
from dotenv import load_dotenv
from langgraph.config import get_stream_writer

from artifact_names import has_upload_suffix, make_timestamped_name, make_unique_name
from resources import ResourceConfig

load_dotenv(Path(__file__).with_name(".env"))

MINERU_POLL_INTERVAL_SECONDS = 30.0
ToolHandler = Callable[..., Any]


@dataclass(frozen=True)
class ToolCatalog:
    handlers: tuple[ToolHandler, ...]

    def as_list(self) -> list[ToolHandler]:
        return list(self.handlers)


def parse_documents(file_paths: list[str]) -> dict[str, Any]:
    """Parse one or more local PDF documents and write markdown under /artifacts/downloads/."""
    if not file_paths:
        raise ValueError("file_paths must not be empty")

    batch_timestamp = time.strftime("%Y%m%d%H%M%S")
    reserved_output_names: set[str] = set()
    writer = _stream_writer()
    valid_sources: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []

    for raw_path in file_paths:
        try:
            source = _resolve_document_path(raw_path)
            if not source.is_file():
                raise FileNotFoundError(f"File not found: {source}")
            output_path = _default_output_path(raw_path, source, batch_timestamp, reserved_output_names)
            valid_sources.append(
                {
                    "file_path": raw_path,
                    "source": source,
                    "output_path": output_path,
                    "target": _resolve_document_path(output_path),
                }
            )
        except Exception as exc:
            failed.append({"file_path": raw_path, "error": _error_text(exc)})

    output_paths = [item["output_path"] for item in valid_sources]
    if not valid_sources:
        _emit_parse_documents_status(
            writer,
            status="completed",
            task_id=None,
            file_paths=file_paths,
            output_paths=[],
            succeeded_count=0,
            failed_count=len(failed),
        )
        return {
            "task_id": None,
            "status_url": None,
            "result_url": None,
            "succeeded": [],
            "failed": failed,
        }

    for item in valid_sources:
        item["target"].parent.mkdir(parents=True, exist_ok=True)

    base_url = _required_env("MINERU_BASE_URL")
    backend = _required_env("MINERU_BACKEND")
    effort = os.getenv("MINERU_EFFORT") or ""
    timeout_seconds = int(_required_env("MINERU_TIMEOUT_SECONDS"))

    task = _submit_mineru_task(
        [item["source"] for item in valid_sources],
        base_url=base_url,
        backend=backend,
        effort=effort,
        timeout_seconds=timeout_seconds,
    )
    _emit_parse_documents_status(
        writer,
        status="submitted",
        task_id=task["task_id"],
        file_paths=file_paths,
        output_paths=output_paths,
        succeeded_count=0,
        failed_count=len(failed),
    )
    _wait_for_mineru_completion(
        task,
        timeout_seconds=timeout_seconds,
        file_paths=file_paths,
        output_paths=output_paths,
        writer=writer,
        pre_failed_count=len(failed),
        valid_count=len(valid_sources),
    )

    try:
        result_payload = _fetch_mineru_result(task["result_url"], timeout_seconds=timeout_seconds)
        succeeded, item_failures = _collect_batch_results(result_payload, valid_sources)
    except Exception as exc:
        _emit_parse_documents_status(
            writer,
            status="failed",
            task_id=task["task_id"],
            file_paths=file_paths,
            output_paths=output_paths,
            succeeded_count=0,
            failed_count=len(failed) + len(valid_sources),
            error=_error_text(exc),
        )
        raise

    failed.extend(item_failures)
    _emit_parse_documents_status(
        writer,
        status="completed",
        task_id=task["task_id"],
        file_paths=file_paths,
        output_paths=[item["output_path"] for item in succeeded],
        succeeded_count=len(succeeded),
        failed_count=len(failed),
    )
    return {
        "task_id": task["task_id"],
        "status_url": task["status_url"],
        "result_url": task["result_url"],
        "succeeded": succeeded,
        "failed": failed,
    }


def _default_output_path(
    raw_path: str,
    source: Path,
    batch_timestamp: str,
    reserved_names: set[str],
) -> str:
    downloads_dir = _artifacts_root() / "downloads"
    filename = _output_filename(raw_path, source, downloads_dir, batch_timestamp, reserved_names)
    return f"/artifacts/downloads/{filename}"


def _output_filename(
    raw_path: str,
    source: Path,
    downloads_dir: Path,
    batch_timestamp: str,
    reserved_names: set[str],
) -> str:
    if raw_path.startswith("/artifacts/uploads/") and has_upload_suffix(source.stem):
        return make_unique_name(downloads_dir, f"{source.stem}.md", reserved_names)
    return make_timestamped_name(
        downloads_dir,
        f"{source.stem}.md",
        batch_timestamp,
        reserved_names,
    )


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
    sources: Sequence[Path],
    *,
    base_url: str,
    backend: str,
    effort: str,
    timeout_seconds: int,
) -> dict[str, str]:
    with ExitStack() as stack:
        files = []
        for source in sources:
            mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
            handle = stack.enter_context(source.open("rb"))
            files.append(("files", (source.name, handle, mime)))
        response = requests.post(
            f"{base_url}/tasks",
            files=files,
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
    file_paths: list[str],
    output_paths: list[str],
    writer: Callable[[Any], None] | None,
    pre_failed_count: int,
    valid_count: int,
    poll_interval_seconds: float = MINERU_POLL_INTERVAL_SECONDS,
) -> None:
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
            return
        if normalized_status in {"pending", "processing"}:
            _emit_parse_documents_status(
                writer,
                status=normalized_status,
                task_id=task["task_id"],
                file_paths=file_paths,
                output_paths=output_paths,
                succeeded_count=0,
                failed_count=pre_failed_count,
            )
            time_left = deadline - time.monotonic()
            if time_left > 0:
                time.sleep(min(poll_interval_seconds, time_left))
            continue
        if normalized_status == "failed":
            error_text = _mineru_error_text(payload)
        else:
            error_text = f"Unexpected MinerU task status: {status}. Payload: {payload!r}"
        _emit_parse_documents_status(
            writer,
            status="failed",
            task_id=task["task_id"],
            file_paths=file_paths,
            output_paths=output_paths,
            succeeded_count=0,
            failed_count=pre_failed_count + valid_count,
            error=error_text,
        )
        raise RuntimeError(error_text)
    error_text = f"MinerU task {task['task_id']} timed out. Last status: {last_status!r}"
    _emit_parse_documents_status(
        writer,
        status="failed",
        task_id=task["task_id"],
        file_paths=file_paths,
        output_paths=output_paths,
        succeeded_count=0,
        failed_count=pre_failed_count + valid_count,
        error=error_text,
    )
    raise TimeoutError(error_text)


def _fetch_mineru_result(result_url: str, *, timeout_seconds: int) -> Any:
    result_response = requests.get(result_url, timeout=timeout_seconds)
    result_response.raise_for_status()
    return _json_or_text(result_response)


def _json_or_text(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def _collect_batch_results(
    result_payload: Any,
    valid_sources: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    entries = _result_entries(result_payload)
    matched_entries = _match_result_entries(entries, valid_sources)
    succeeded: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []

    for item, entry in zip(valid_sources, matched_entries):
        if entry is None:
            failed.append(
                {
                    "file_path": item["file_path"],
                    "error": "MinerU result did not contain a matching file entry",
                }
            )
            continue
        try:
            markdown = _extract_markdown(entry)
            item["target"].write_text(markdown, encoding="utf-8")
        except Exception as exc:
            failed.append({"file_path": item["file_path"], "error": _error_text(exc)})
            continue
        succeeded.append(
            {
                "file_path": item["file_path"],
                "output_path": item["output_path"],
                "bytes": len(markdown.encode("utf-8")),
            }
        )
    return succeeded, failed


def _result_entries(value: Any) -> list[tuple[str | None, Any]]:
    if not isinstance(value, dict):
        raise RuntimeError(f"MinerU result response was not a JSON object: {value!r}")
    results = value.get("results")
    if isinstance(results, dict):
        return [(str(name), entry) for name, entry in results.items()]
    if isinstance(results, list):
        return [(None, entry) for entry in results]
    raise RuntimeError(f"MinerU result did not include usable results: {value!r}")


def _match_result_entries(
    entries: list[tuple[str | None, Any]],
    valid_sources: Sequence[dict[str, Any]],
) -> list[Any | None]:
    matched_entries: list[Any | None] = [None] * len(valid_sources)
    available_indices = list(range(len(entries)))

    for item_index, item in enumerate(valid_sources):
        for entry_index in list(available_indices):
            if _result_name(entries[entry_index][0]) == item["source"].name:
                matched_entries[item_index] = entries[entry_index][1]
                available_indices.remove(entry_index)
                break

    for item_index, item in enumerate(valid_sources):
        if matched_entries[item_index] is not None:
            continue
        for entry_index in list(available_indices):
            if _result_stem(entries[entry_index][0]) == item["source"].stem:
                matched_entries[item_index] = entries[entry_index][1]
                available_indices.remove(entry_index)
                break

    remaining_items = [index for index, entry in enumerate(matched_entries) if entry is None]
    if len(remaining_items) == len(available_indices):
        for item_index, entry_index in zip(remaining_items, available_indices):
            matched_entries[item_index] = entries[entry_index][1]
    return matched_entries


def _result_name(value: str | None) -> str | None:
    if not value:
        return None
    return value.replace("\\", "/").rsplit("/", 1)[-1]


def _result_stem(value: str | None) -> str | None:
    name = _result_name(value)
    if not name:
        return None
    return Path(name).stem


def _extract_markdown(value: Any) -> str:
    if isinstance(value, dict):
        markdown = value.get("md_content")
        if isinstance(markdown, str):
            return markdown
    raise RuntimeError(_result_error_text(value))


def _result_error_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("error", "message", "detail"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                return text
    return f"MinerU result did not include markdown content: {value!r}"


def _mineru_url(base_url: str, raw_url: str) -> str:
    return urljoin(f"{base_url.rstrip('/')}/", raw_url)


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


def _emit_parse_documents_status(
    writer: Callable[[Any], None] | None,
    *,
    status: str,
    task_id: str | None,
    file_paths: list[str],
    output_paths: list[str],
    succeeded_count: int,
    failed_count: int,
    error: str | None = None,
) -> None:
    if writer is None:
        return
    payload: dict[str, Any] = {
        "name": "parse_documents",
        "status": status,
        "file_paths": file_paths,
        "output_paths": output_paths,
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
    }
    if task_id is not None:
        payload["task_id"] = task_id
    if error is not None:
        payload["error"] = error
    writer(payload)


def _error_text(exc: Exception) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__


def default_tool_catalog() -> ToolCatalog:
    return ToolCatalog((parse_documents,))

from __future__ import annotations

import mimetypes
import os
import time
import zipfile
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Sequence
from urllib.parse import urljoin

import requests
from dotenv import load_dotenv
from langgraph.config import get_stream_writer

from artifact_names import make_unique_name
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
    """Parse local PDF/office documents via MinerU and save the task result ZIP under /artifacts/downloads/.

    The returned ``archive_path`` points to the task-level ZIP bundle (markdown +
    content_list + images + original file). Use ``extract_archives`` to unpack it.
    """
    if not file_paths:
        raise ValueError("file_paths must not be empty")

    batch_timestamp = time.strftime("%Y%m%d%H%M%S")
    writer = _stream_writer()
    valid_sources: list[Path] = []
    valid_file_paths: list[str] = []
    failed: list[dict[str, str]] = []

    for raw_path in file_paths:
        try:
            source = _resolve_document_path(raw_path)
            if not source.is_file():
                raise FileNotFoundError(f"File not found: {source}")
            valid_sources.append(source)
            valid_file_paths.append(raw_path)
        except Exception as exc:
            failed.append({"file_path": raw_path, "error": _error_text(exc)})

    if not valid_sources:
        _emit_parse_documents_status(
            writer,
            status="completed",
            task_id=None,
            file_paths=file_paths,
            archive_path=None,
            succeeded_count=0,
            failed_count=len(failed),
        )
        return {
            "task_id": None,
            "status_url": None,
            "result_url": None,
            "archive_path": None,
            "succeeded": [],
            "failed": failed,
        }

    base_url = _required_env("MINERU_BASE_URL")
    backend = _required_env("MINERU_BACKEND")
    effort = os.getenv("MINERU_EFFORT") or ""
    timeout_seconds = int(_required_env("MINERU_TIMEOUT_SECONDS"))

    task = _submit_mineru_task(
        valid_sources,
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
        archive_path=None,
        succeeded_count=0,
        failed_count=len(failed),
    )
    _wait_for_mineru_completion(
        task,
        timeout_seconds=timeout_seconds,
        file_paths=file_paths,
        writer=writer,
        pre_failed_count=len(failed),
        valid_count=len(valid_sources),
    )

    archive_filename = _archive_filename(valid_sources, batch_timestamp)
    try:
        archive_path = _download_mineru_zip(
            task["result_url"],
            archive_filename=archive_filename,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        _emit_parse_documents_status(
            writer,
            status="failed",
            task_id=task["task_id"],
            file_paths=file_paths,
            archive_path=None,
            succeeded_count=0,
            failed_count=len(failed) + len(valid_sources),
            error=_error_text(exc),
        )
        raise

    _emit_parse_documents_status(
        writer,
        status="completed",
        task_id=task["task_id"],
        file_paths=file_paths,
        archive_path=archive_path,
        succeeded_count=len(valid_sources),
        failed_count=len(failed),
    )
    return {
        "task_id": task["task_id"],
        "status_url": task["status_url"],
        "result_url": task["result_url"],
        "archive_path": archive_path,
        "succeeded": [{"file_path": path} for path in valid_file_paths],
        "failed": failed,
    }


def extract_archives(zip_paths: list[str]) -> dict[str, Any]:
    """Unpack one or more ZIP artifacts into /artifacts/downloads/<zip-stem>/ and list their files."""
    if not zip_paths:
        raise ValueError("zip_paths must not be empty")

    writer = _stream_writer()
    succeeded: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []

    for raw_path in zip_paths:
        try:
            source = _resolve_document_path(raw_path)
            if not source.is_file():
                raise FileNotFoundError(f"File not found: {source}")
            output_dir, files = _extract_zip(source)
            archive_path = _to_virtual_path(source)
        except Exception as exc:
            failed.append({"zip_path": raw_path, "error": _error_text(exc)})
            continue
        succeeded.append(
            {
                "archive_path": archive_path,
                "output_dir": output_dir,
                "files": files,
            }
        )

    if writer is not None:
        writer(
            {
                "name": "extract_archives",
                "status": "completed",
                "zip_paths": zip_paths,
                "succeeded_count": len(succeeded),
                "failed_count": len(failed),
            }
        )
    return {"succeeded": succeeded, "failed": failed}


def _extract_zip(source: Path) -> tuple[str, list[str]]:
    downloads_dir = _artifacts_root() / "downloads"
    output_dir_virtual = f"/artifacts/downloads/{source.stem}"
    output_dir = downloads_dir / source.stem
    output_dir.mkdir(parents=True, exist_ok=True)

    files: list[str] = []
    with zipfile.ZipFile(source) as archive:
        members = sorted(archive.namelist())
        for member in members:
            if member.endswith("/"):
                continue
            archive.extract(member, output_dir)
            files.append(f"{output_dir_virtual}/{member}")
    return output_dir_virtual, files


def _archive_filename(
    sources: Sequence[Path],
    batch_timestamp: str,
) -> str:
    downloads_dir = _artifacts_root() / "downloads"
    if len(sources) == 1:
        stem = sources[0].stem
    else:
        stem = f"{sources[0].stem}_etc_{batch_timestamp}"
    return make_unique_name(downloads_dir, f"{stem}.zip")


def _download_mineru_zip(result_url: str, *, archive_filename: str, timeout_seconds: int) -> str:
    downloads_dir = _artifacts_root() / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    target = downloads_dir / archive_filename
    with requests.get(result_url, timeout=timeout_seconds, stream=True) as response:
        response.raise_for_status()
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    handle.write(chunk)
    return f"/artifacts/downloads/{archive_filename}"


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


def _to_virtual_path(resolved: Path) -> str:
    root = _artifacts_root().resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return str(resolved)
    return f"/artifacts/{relative.as_posix()}"


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
                "return_content_list": "true",
                "return_images": "true",
                "return_original_file": "true",
                "response_format_zip": "true",
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
                archive_path=None,
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
            archive_path=None,
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
        archive_path=None,
        succeeded_count=0,
        failed_count=pre_failed_count + valid_count,
        error=error_text,
    )
    raise TimeoutError(error_text)


def _json_or_text(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


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
    archive_path: str | None,
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
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
    }
    if task_id is not None:
        payload["task_id"] = task_id
    if archive_path is not None:
        payload["archive_path"] = archive_path
    if error is not None:
        payload["error"] = error
    writer(payload)


def _error_text(exc: Exception) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__


def default_tool_catalog() -> ToolCatalog:
    return ToolCatalog((parse_documents, extract_archives))

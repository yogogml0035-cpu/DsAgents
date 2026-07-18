from __future__ import annotations

import json
import mimetypes
import os
import time
import zipfile
from contextlib import ExitStack
from pathlib import Path
from typing import Annotated, Any, Callable, Sequence
from urllib.parse import urljoin

import requests
from dotenv import load_dotenv
from langgraph.config import get_stream_writer

from integrations.artifacts import (
    artifacts_root,
    make_unique_name,
    resolve_artifact_path,
    to_virtual_artifact_path,
)


BACKEND_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(BACKEND_ENV_PATH)

MINERU_POLL_INTERVAL_SECONDS = 30.0


def parse_documents(
    file_paths: Annotated[
        list[str],
        "本地路径或 /artifacts/... 文件，在一次 MinerU 批次中解析。",
    ],
    return_md: Annotated[bool, "包含 Markdown 输出；会触发完整 ZIP 模式。"] = False,
    return_content_list: Annotated[
        bool,
        "包含 MinerU content_list；默认 JSON 模式会写入 result_path。",
    ] = True,
    return_images: Annotated[bool, "包含抽取图片；会触发完整 ZIP 模式。"] = False,
    return_original_file: Annotated[bool, "包含原始文件；会触发完整 ZIP 模式。"] = False,
    response_format_zip: Annotated[
        bool,
        "将完整 ZIP 保存到 archive_path，而不是 JSON result_path。",
    ] = False,
) -> dict[str, Any]:
    """通过 MinerU 解析本地 PDF/Office 文档，输出保存到 /artifacts/downloads/。

    默认模式将 JSON 写入 result_path。若请求 Markdown、图片、原始文件或 ZIP，
    会归一为完整 ZIP 模式；用 extract_archives 解压后再查看内容。
    """
    if not file_paths:
        raise ValueError("file_paths must not be empty")
    if return_md or return_images or return_original_file or response_format_zip:
        return_md = True
        return_content_list = True
        return_images = True
        return_original_file = True
        response_format_zip = True
    output_options = {
        "return_md": return_md,
        "return_content_list": return_content_list,
        "return_images": return_images,
        "return_original_file": return_original_file,
        "response_format_zip": response_format_zip,
    }

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
            "result_path": None,
            "result_format": "zip" if response_format_zip else "json",
            "output_options": output_options,
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
        output_options=output_options,
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

    try:
        if response_format_zip:
            result_path = None
            archive_path = _download_mineru_zip(
                task["result_url"],
                archive_filename=_archive_filename(valid_sources, batch_timestamp),
                timeout_seconds=timeout_seconds,
            )
        else:
            archive_path = None
            result_path = _download_mineru_json(
                task["result_url"],
                result_filename=_result_filename(valid_sources, batch_timestamp),
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
        result_path=result_path,
        succeeded_count=len(valid_sources),
        failed_count=len(failed),
    )
    return {
        "task_id": task["task_id"],
        "status_url": task["status_url"],
        "result_url": task["result_url"],
        "archive_path": archive_path,
        "result_path": result_path,
        "result_format": "zip" if response_format_zip else "json",
        "output_options": output_options,
        "succeeded": [{"file_path": path} for path in valid_file_paths],
        "failed": failed,
    }


def extract_archives(zip_paths: list[str]) -> dict[str, Any]:
    """将一个或多个 ZIP artifact 解压到 /artifacts/downloads/<zip-stem>/，并列出其中文件。"""
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
    downloads_dir = artifacts_root() / "downloads"
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


def _archive_filename(sources: Sequence[Path], batch_timestamp: str) -> str:
    downloads_dir = artifacts_root() / "downloads"
    if len(sources) == 1:
        stem = sources[0].stem
    else:
        stem = f"{sources[0].stem}_etc_{batch_timestamp}"
    return make_unique_name(downloads_dir, f"{stem}.zip")


def _result_filename(sources: Sequence[Path], batch_timestamp: str) -> str:
    downloads_dir = artifacts_root() / "downloads"
    if len(sources) == 1:
        stem = sources[0].stem
    else:
        stem = f"{sources[0].stem}_etc_{batch_timestamp}"
    return make_unique_name(downloads_dir, f"{stem}.json")


def _download_mineru_zip(result_url: str, *, archive_filename: str, timeout_seconds: int) -> str:
    downloads_dir = artifacts_root() / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    target = downloads_dir / archive_filename
    with requests.get(result_url, timeout=timeout_seconds, stream=True) as response:
        response.raise_for_status()
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    handle.write(chunk)
    return f"/artifacts/downloads/{archive_filename}"


def _download_mineru_json(result_url: str, *, result_filename: str, timeout_seconds: int) -> str:
    downloads_dir = artifacts_root() / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    target = downloads_dir / result_filename
    response = requests.get(result_url, timeout=timeout_seconds)
    response.raise_for_status()
    payload = _json_or_text(response)
    if not isinstance(payload, dict):
        raise RuntimeError(f"MinerU result response was not a JSON object: {payload!r}")
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"/artifacts/downloads/{result_filename}"


def _resolve_document_path(raw_path: str | None) -> Path:
    return resolve_artifact_path(raw_path, root=artifacts_root(), allow_local=True)


def _to_virtual_path(resolved: Path) -> str:
    try:
        return to_virtual_artifact_path(resolved, root=artifacts_root())
    except ValueError:
        return str(resolved)


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
    output_options: dict[str, bool],
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
                **{key: _bool_form(value) for key, value in output_options.items()},
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


def _bool_form(value: bool) -> str:
    return "true" if value else "false"


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
    result_path: str | None = None,
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
    if result_path is not None:
        payload["result_path"] = result_path
    if error is not None:
        payload["error"] = error
    writer(payload)


def _error_text(exc: Exception) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__

from __future__ import annotations

import json
import mimetypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

SUCCESS_STATES = {"completed", "complete", "done", "finished", "success", "succeeded"}
FAILURE_STATES = {"failed", "failure", "error", "errored", "cancelled", "canceled"}
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
    source = Path(file_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"File not found: {source}")

    target = Path(output_path).expanduser().resolve() if output_path else _default_output_path(source)
    target.parent.mkdir(parents=True, exist_ok=True)

    base_url = _required_env("MINERU_BASE_URL")
    backend = _required_env("MINERU_BACKEND")
    effort = _required_env("MINERU_EFFORT")
    timeout_seconds = int(_required_env("MINERU_TIMEOUT_SECONDS"))

    task_id = _submit_mineru_task(source, base_url=base_url, backend=backend, effort=effort)
    result = _wait_for_mineru_result(task_id, base_url=base_url, timeout_seconds=timeout_seconds)
    markdown = _extract_markdown(result)
    target.write_text(markdown, encoding="utf-8")

    return json.dumps(
        {
            "task_id": task_id,
            "source": str(source),
            "output_path": str(target),
            "markdown_bytes": len(markdown.encode("utf-8")),
        },
        ensure_ascii=False,
    )


def _default_output_path(source: Path) -> Path:
    return Path(__file__).resolve().parent / "data" / "document_outputs" / f"{source.stem}.md"


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _submit_mineru_task(source: Path, *, base_url: str, backend: str, effort: str) -> str:
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
            timeout=60,
        )
    response.raise_for_status()
    payload = _json_or_text(response)
    task_id = _find_value(payload, {"task_id", "taskId", "id"})
    if not task_id:
        raise RuntimeError(f"MinerU task response did not include a task id: {payload!r}")
    return str(task_id)


def _wait_for_mineru_result(
    task_id: str,
    *,
    base_url: str,
    timeout_seconds: int,
    poll_interval_seconds: float = 2.0,
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    last_status: Any = None
    while time.monotonic() < deadline:
        status_response = requests.get(f"{base_url}/tasks/{task_id}", timeout=30)
        status_response.raise_for_status()
        last_status = _json_or_text(status_response)
        status = str(_find_value(last_status, {"status", "state"}) or "").lower()
        if status in FAILURE_STATES:
            raise RuntimeError(f"MinerU task failed: {last_status!r}")
        if status in SUCCESS_STATES:
            result_response = requests.get(f"{base_url}/tasks/{task_id}/result", timeout=120)
            result_response.raise_for_status()
            return _json_or_text(result_response)
        time.sleep(poll_interval_seconds)
    raise TimeoutError(f"MinerU task {task_id} timed out. Last status: {last_status!r}")


def _json_or_text(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def _find_value(value: Any, names: set[str]) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in names:
                return item
        for item in value.values():
            found = _find_value(item, names)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_value(item, names)
            if found is not None:
                return found
    return None


def _extract_markdown(value: Any) -> str:
    if isinstance(value, str):
        return value
    markdown = _find_value(value, {"md", "markdown", "md_content", "markdown_content"})
    if isinstance(markdown, str):
        return markdown
    raise RuntimeError(f"MinerU result did not include markdown content: {value!r}")


def default_tool_catalog() -> ToolCatalog:
    return ToolCatalog((parse_document,))

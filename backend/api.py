from __future__ import annotations

import json
import mimetypes
import shutil
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from harness import HarnessRuntime, create_harness
from resources import AgentResources, ResourceConfig


INTERRUPTED_RUN_ERROR = "执行已中断，请重试"


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str | None = None
    messages: list["RunMessage"]


class TextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text"]
    text: str


class ArtifactBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["artifact"]
    path: str


ContentBlock = Annotated[TextBlock | ArtifactBlock, Field(discriminator="type")]


class RunMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    content: list[ContentBlock]


def create_app(
    *,
    resource_config: ResourceConfig | None = None,
    harness_factory: Callable[[AgentResources], HarnessRuntime] = create_harness,
) -> FastAPI:
    config = resource_config or ResourceConfig()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Iterator[None]:
        resources = AgentResources(config)
        resources.__enter__()
        resources.runs.fail_incomplete_runs(INTERRUPTED_RUN_ERROR)
        app.state.resources = resources
        app.state.harness = harness_factory(resources)
        app.state.session_locks: dict[str, threading.Lock] = {}
        app.state.active_runs: dict[str, str] = {}
        app.state.registry_lock = threading.Lock()
        try:
            yield
        finally:
            resources.__exit__(None, None, None)

    app = FastAPI(lifespan=lifespan)

    @app.post("/runs")
    def post_run(request: RunRequest):
        session_id = request.session_id or uuid.uuid4().hex
        run_id = uuid.uuid4().hex
        messages = [message.model_dump(mode="json") for message in request.messages]
        conflict = _acquire_session_run(app, session_id, run_id)
        if conflict is not None:
            return _conflict_response(conflict)
        try:
            app.state.resources.runs.create_run(
                run_id,
                session_id,
                json.dumps(messages, ensure_ascii=False),
            )
        except Exception:
            _release_session_run(app, session_id)
            raise

        worker = threading.Thread(
            target=_run_background,
            args=(app, session_id, run_id, messages),
            daemon=True,
        )
        try:
            worker.start()
        except Exception as exc:
            _ensure_failed_run(app, run_id, exc)
            _release_session_run(app, session_id)
            return _run_body(app.state.resources.runs.get_run(run_id))
        return _queued_response(run_id, session_id)

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, after_event_id: int | None = None):
        try:
            runs = app.state.resources.runs
            run = runs.get_run(run_id)
            events = runs.get_run_events(run_id, after_event_id=after_event_id)
            latest_content_event = runs.get_latest_content_event(run_id)
        except KeyError:
            return JSONResponse(status_code=404, content={"error": f"Unknown run: {run_id}"})
        return {
            "run": _run_body(run),
            "events": [_run_event_body(event) for event in events],
            "latest_content_event": _run_event_body(latest_content_event) if latest_content_event else None,
        }

    @app.post("/upload")
    def post_upload(files: list[UploadFile] = File(...)) -> dict[str, list[dict[str, Any]]]:
        return {"files": [_store_upload(file, config) for file in files]}

    def _store_upload(file: UploadFile, resource_config: ResourceConfig) -> dict[str, Any]:
        filename = _clean_filename(file.filename)
        stored_name = f"{uuid.uuid4().hex}_{filename}"
        target = resource_config.artifacts_dir / "uploads" / stored_name
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("wb") as handle:
                shutil.copyfileobj(file.file, handle)
        finally:
            file.file.close()
        mime_type = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return {
            "file_path": _virtual_upload_path(target.name),
            "name": filename,
            "mime_type": mime_type,
            "size": target.stat().st_size,
        }

    return app


app = create_app()


def _run_background(app: FastAPI, session_id: str, run_id: str, messages: list[dict[str, Any]]) -> None:
    try:
        for _ in app.state.harness.execute_run(messages, session_id, run_id):
            pass
    except Exception as exc:
        _ensure_failed_run(app, run_id, exc)
    finally:
        _release_session_run(app, session_id)


def _ensure_failed_run(app: FastAPI, run_id: str, exc: Exception):
    try:
        run = app.state.resources.runs.get_run(run_id)
    except KeyError:
        return None
    if run.status in {"succeeded", "failed"}:
        return None
    return app.state.resources.runs.emit_run_status(
        run_id,
        "failed",
        error=_error_text(exc),
        raw={"status": "failed", "error": repr(exc)},
    )


def _acquire_session_run(app: FastAPI, session_id: str, run_id: str) -> str | None:
    with app.state.registry_lock:
        lock = app.state.session_locks.setdefault(session_id, threading.Lock())
        if not lock.acquire(blocking=False):
            return app.state.active_runs.get(session_id)
        app.state.active_runs[session_id] = run_id
        return None


def _release_session_run(app: FastAPI, session_id: str) -> None:
    with app.state.registry_lock:
        lock = app.state.session_locks.get(session_id)
        if lock is None:
            return
        app.state.active_runs.pop(session_id, None)
        if lock.locked():
            lock.release()


def _queued_response(run_id: str, session_id: str) -> dict[str, Any]:
    return {"run_id": run_id, "session_id": session_id, "status": "queued"}


def _run_body(run: Any) -> dict[str, Any]:
    return asdict(run)


def _run_event_body(event: Any) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "run_id": event.run_id,
        "type": event.event_type,
        "created_at": event.created_at,
        "payload": event.payload,
        "raw": event.raw,
    }


def _conflict_response(active_run_id: str | None) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"error": "该会话正在运行", "active_run_id": active_run_id},
    )


def _clean_filename(filename: str | None) -> str:
    if not filename:
        return "upload"
    cleaned = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return cleaned or "upload"


def _virtual_upload_path(filename: str) -> str:
    return str(Path("/artifacts/uploads") / filename).replace("\\", "/")


def _error_text(exc: Exception) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__

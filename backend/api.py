from __future__ import annotations

import json
import shutil
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from harness import HarnessRuntime, create_harness
from resources import AgentResources, ResourceConfig


INTERRUPTED_RUN_ERROR = "执行已中断，请重试"


class MessageRequest(BaseModel):
    message: str
    session_id: str | None = None


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
        resources.sessions.fail_incomplete_runs(INTERRUPTED_RUN_ERROR)
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

    @app.post("/sessions/messages")
    def post_message(request: MessageRequest):
        session_id = request.session_id or uuid.uuid4().hex
        run_id = uuid.uuid4().hex
        conflict = _acquire_session_run(app, session_id, run_id)
        if conflict is not None:
            return _conflict_response(conflict)
        try:
            app.state.resources.sessions.create_run(session_id, run_id)
            _run_blocking(app, session_id, run_id, request.message)
            return _blocking_response(app.state.resources.sessions.get_run(run_id))
        finally:
            _release_session_run(app, session_id)

    @app.post("/sessions/messages/stream")
    def post_message_stream(request: MessageRequest):
        session_id = request.session_id or uuid.uuid4().hex
        run_id = uuid.uuid4().hex
        conflict = _acquire_session_run(app, session_id, run_id)
        if conflict is not None:
            return _conflict_response(conflict)
        try:
            app.state.resources.sessions.create_run(session_id, run_id)
        except Exception:
            _release_session_run(app, session_id)
            raise

        def event_stream() -> Iterator[str]:
            yield _sse_event(
                "session",
                {
                    "session_id": session_id,
                    "run_id": run_id,
                    "status": "queued",
                },
            )
            try:
                for run_event in app.state.harness.execute_run(request.message, session_id, run_id):
                    yield _sse_event("run_event", _run_event_body(run_event))
            except Exception as exc:
                event = _ensure_failed_run(app, run_id, exc)
                if event is not None:
                    yield _sse_event("run_event", _run_event_body(event))
            finally:
                _release_session_run(app, session_id)
            yield _sse_event("done", {"session_id": session_id, "run_id": run_id})

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @app.post("/sessions/messages/runs")
    def post_message_run(request: MessageRequest):
        session_id = request.session_id or uuid.uuid4().hex
        run_id = uuid.uuid4().hex
        conflict = _acquire_session_run(app, session_id, run_id)
        if conflict is not None:
            return _conflict_response(conflict)
        try:
            app.state.resources.sessions.create_run(session_id, run_id)
        except Exception:
            _release_session_run(app, session_id)
            raise

        worker = threading.Thread(
            target=_run_background,
            args=(app, session_id, run_id, request.message),
            daemon=True,
        )
        try:
            worker.start()
        except Exception as exc:
            _ensure_failed_run(app, run_id, exc)
            _release_session_run(app, session_id)
            return _blocking_response(app.state.resources.sessions.get_run(run_id))
        return {"session_id": session_id, "run_id": run_id, "status": "queued"}

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, after_event_id: int | None = None):
        try:
            run = app.state.resources.sessions.get_run(run_id)
            events = app.state.resources.sessions.get_run_events(run_id, after_event_id=after_event_id)
        except KeyError:
            return JSONResponse(status_code=404, content={"error": f"Unknown run: {run_id}"})
        return {"run": _run_body(run), "events": [_run_event_body(event) for event in events]}

    @app.get("/sessions/{session_id}/runs")
    def get_session_runs(session_id: str) -> list[dict[str, Any]]:
        return [_run_list_item(run) for run in app.state.resources.sessions.list_runs(session_id)]

    @app.post("/files")
    def post_file(file: UploadFile = File(...)) -> dict[str, str]:
        filename = _clean_filename(file.filename)
        stored_name = f"{uuid.uuid4().hex}_{filename}"
        target = config.artifacts_dir / "uploads" / stored_name
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("wb") as handle:
                shutil.copyfileobj(file.file, handle)
        finally:
            file.file.close()
        return {"file_path": _virtual_upload_path(target.name)}

    return app


app = create_app()


def _run_blocking(app: FastAPI, session_id: str, run_id: str, message: str) -> None:
    try:
        for _ in app.state.harness.execute_run(message, session_id, run_id):
            pass
    except Exception as exc:
        _ensure_failed_run(app, run_id, exc)


def _run_background(app: FastAPI, session_id: str, run_id: str, message: str) -> None:
    try:
        for _ in app.state.harness.execute_run(message, session_id, run_id):
            pass
    except Exception as exc:
        _ensure_failed_run(app, run_id, exc)
    finally:
        _release_session_run(app, session_id)


def _ensure_failed_run(app: FastAPI, run_id: str, exc: Exception):
    try:
        run = app.state.resources.sessions.get_run(run_id)
    except KeyError:
        return None
    if run.status in {"succeeded", "failed"}:
        return None
    return app.state.resources.sessions.emit_run_status(
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


def _blocking_response(run: Any) -> dict[str, Any]:
    payload = {"session_id": run.session_id, "run_id": run.run_id, "status": run.status}
    if run.status == "succeeded":
        payload["reply"] = run.reply or ""
    elif run.status == "failed":
        payload["error"] = run.error or ""
    return payload


def _run_body(run: Any) -> dict[str, Any]:
    return asdict(run)


def _run_list_item(run: Any) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "status": run.status,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "reply_preview": run.reply_preview,
        "error_preview": run.error_preview,
    }


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


def _sse_event(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


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

from __future__ import annotations

import json
import mimetypes
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from integrations.artifacts import clean_filename, make_timestamped_name
from runtime.execution import HarnessRuntime, create_harness
from runtime.resources import AgentResources, ResourceConfig


INTERRUPTED_RUN_ERROR = "执行已中断，请重试"

# MiniMax-M3 standard tier pricing (CNY per million tokens). Each model call is
# priced by its own input size: <=512k uses the standard tier, >512k the long
# context tier. Cache creation is charged as non-cache-read input. These are
# trend estimates only; final billing is whatever MiniMax actually invoices.
PRICING_AS_OF = "2026-07-12"
_TIER_THRESHOLD_INPUT_TOKENS = 512 * 1024
# (input_per_m, output_per_m, cache_read_per_m) per tier
_PRICING_TIERS: dict[str, tuple[float, float, float]] = {
    "standard": (2.10, 8.40, 0.42),
    "long_context": (4.20, 16.80, 0.84),
}
# Models we know how to price. Unknown model => amounts are null, tokens stay.
_PRICEABLE_MODELS = {"MiniMax-M3"}


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
            usage = _usage_summary(runs.aggregate_model_usage(run_id))
        except KeyError:
            return JSONResponse(status_code=404, content={"error": f"Unknown run: {run_id}"})
        return {
            "run": _run_body(run),
            "events": [_run_event_body(event) for event in events],
            "latest_content_event": _run_event_body(latest_content_event) if latest_content_event else None,
            "usage": usage,
        }

    @app.post("/runs/{run_id}/cancel")
    def cancel_run(run_id: str):
        runs = app.state.resources.runs
        try:
            run = runs.get_run(run_id)
        except KeyError:
            return JSONResponse(status_code=404, content={"error": f"Unknown run: {run_id}"})
        status = run.status
        if status in {"succeeded", "failed"}:
            return JSONResponse(
                status_code=409,
                content={"error": f"Run already terminal: {status}", "status": status},
            )
        if status in {"cancelling", "cancelled"}:
            return JSONResponse(
                status_code=200,
                content={"status": status},
            )
        # 活跃 run（queued/running）：投影 cancelling 并协作 drain。
        runs.emit_run_status(
            run_id,
            "cancelling",
            raw={"status": "cancelling"},
        )
        harness: HarnessRuntime = app.state.harness
        drained = harness.request_cancel(run_id)
        if not drained:
            # queued 或尚未进入 execute_run 的 run：直接置为 cancelled。
            runs.emit_run_status(
                run_id,
                "cancelled",
                error="run cancelled",
                raw={"status": "cancelled"},
            )
        return JSONResponse(status_code=202, content={"status": "cancelling"})

    @app.post("/upload")
    def post_upload(files: list[UploadFile] = File(...)) -> dict[str, list[dict[str, Any]]]:
        batch_timestamp = time.strftime("%Y%m%d%H%M%S")
        reserved_names: set[str] = set()
        upload_dir = config.artifacts_dir / "uploads"
        return {
            "files": [
                _store_upload(file, upload_dir, batch_timestamp, reserved_names)
                for file in files
            ]
        }

    def _store_upload(
        file: UploadFile,
        upload_dir: Path,
        batch_timestamp: str,
        reserved_names: set[str],
    ) -> dict[str, Any]:
        filename = clean_filename(file.filename)
        stored_name = make_timestamped_name(upload_dir, filename, batch_timestamp, reserved_names)
        target = upload_dir / stored_name
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
    if run.status in {"succeeded", "failed", "cancelled"}:
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


def _virtual_upload_path(filename: str) -> str:
    return str(Path("/artifacts/uploads") / filename).replace("\\", "/")


def _error_text(exc: Exception) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__


def _usage_summary(agg: dict[str, Any] | None) -> dict[str, Any] | None:
    """Layer cache hit rate + tier-aware CNY estimates onto raw token totals.

    Token counts are always the raw facts. If any model call cannot be priced
    (unknown model), both estimated_* amounts are null for the whole run rather
    than emitting a systematically-low partial figure. Savings is the discount
    cache-read gives versus paying standard input price for those tokens.
    """
    if agg is None:
        return None
    input_tokens = agg["input_tokens"]
    output_tokens = agg["output_tokens"]
    cache_read = agg["cache_read_input_tokens"]
    cache_hit_rate = (cache_read / input_tokens) if input_tokens else None

    priceable = True
    cost = 0.0
    savings = 0.0
    for call in agg.get("calls", []):
        if call.get("model") not in _PRICEABLE_MODELS:
            priceable = False
            break
        tier = _PRICING_TIERS[
            "long_context" if call["input_tokens"] > _TIER_THRESHOLD_INPUT_TOKENS else "standard"
        ]
        input_per_m, output_per_m, cache_read_per_m = tier
        # Cache creation is charged as ordinary non-cache-read input.
        non_cache_read_input = call["input_tokens"] - call["cache_read_input_tokens"]
        cost += (
            non_cache_read_input * input_per_m / 1_000_000
            + call["cache_read_input_tokens"] * cache_read_per_m / 1_000_000
            + call["output_tokens"] * output_per_m / 1_000_000
        )
        savings += call["cache_read_input_tokens"] * (input_per_m - cache_read_per_m) / 1_000_000

    by_agent: list[dict[str, Any]] = []
    for (scope, name), bucket in sorted(agg.get("by_agent", {}).items()):
        agent_input = bucket["input_tokens"]
        by_agent.append({
            "scope": scope,
            "agent_name": name,
            "model_calls": bucket["model_calls"],
            "input_tokens": agent_input,
            "output_tokens": bucket["output_tokens"],
            "cache_read_input_tokens": bucket["cache_read_input_tokens"],
            "cache_creation_input_tokens": bucket["cache_creation_input_tokens"],
            "cache_hit_rate": (bucket["cache_read_input_tokens"] / agent_input) if agent_input else None,
        })

    return {
        "model_calls": agg["model_calls"],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": agg["cache_creation_input_tokens"],
        "cache_hit_rate": cache_hit_rate,
        "estimated_cost_cny": round(cost, 6) if priceable else None,
        "estimated_savings_cny": round(savings, 6) if priceable else None,
        "pricing_as_of": PRICING_AS_OF,
        "estimated_cost_note": (
            "Trend estimate only from observed token usage and MiniMax-M3 "
            "standard pricing; final billing is whatever MiniMax invoices."
        ),
        "by_agent": by_agent,
    }

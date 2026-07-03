from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from harness import HarnessRuntime, assistant_reply_text, create_harness
from resources import AgentResources, ResourceConfig

class MessageRequest(BaseModel):
    message: str
    session_id: str | None = None


def create_app(
    *,
    resource_config: ResourceConfig | None = None,
    harness_factory: Callable[[AgentResources], HarnessRuntime] = create_harness,
) -> FastAPI:
    config = resource_config or ResourceConfig()
    app = FastAPI()

    @app.post("/sessions/messages")
    def post_message(request: MessageRequest) -> dict[str, str]:
        session_id = request.session_id or uuid.uuid4().hex
        with AgentResources(config) as resources:
            turn = harness_factory(resources).run_turn(request.message, session_id)
        return {"session_id": session_id, "reply": assistant_reply_text(turn.result)}

    @app.post("/sessions/messages/stream")
    def post_message_stream(request: MessageRequest) -> StreamingResponse:
        session_id = request.session_id or uuid.uuid4().hex

        def event_stream() -> Iterator[str]:
            yield _sse_event("session", {"session_id": session_id})
            try:
                with AgentResources(config) as resources:
                    for event_name, payload in harness_factory(resources).stream_turn(
                        request.message,
                        session_id,
                    ):
                        yield _sse_event(event_name, payload)
            except Exception as exc:
                yield _sse_event("error", {"session_id": session_id, "message": str(exc)})
                return
            yield _sse_event("done", {"session_id": session_id})

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

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


def _sse_event(event: str, payload: dict[str, str]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _clean_filename(filename: str | None) -> str:
    if not filename:
        return "upload"
    cleaned = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return cleaned or "upload"


def _virtual_upload_path(filename: str) -> str:
    return str(Path("/artifacts/uploads") / filename).replace("\\", "/")

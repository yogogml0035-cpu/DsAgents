from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from tools import _extract_markdown, default_tool_catalog, parse_document


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload
        self.text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

    def json(self) -> object:
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self) -> None:
        return None


def run() -> None:
    assert _extract_markdown({"results": {"source": {"md_content": "# ok"}}}) == "# ok"
    assert default_tool_catalog().handlers[0].__name__ == "parse_document"

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        _check_parse_document_env_guard(tmp)
        _check_virtual_artifacts_and_polling(tmp)
        _check_failed_status_progress(tmp)


def _check_parse_document_env_guard(tmp: str) -> None:
    with patch.dict(os.environ, {}, clear=True):
        source = Path(tmp) / "sample.pdf"
        source.write_text("demo", encoding="utf-8")
        try:
            parse_document(str(source))
        except RuntimeError as exc:
            assert str(exc) == "Missing required environment variable: MINERU_BASE_URL"
        else:
            raise AssertionError("parse_document must fail fast when MINERU_BASE_URL is missing")


def _check_virtual_artifacts_and_polling(tmp: str) -> None:
    api_data_dir = Path(tmp) / "api-data"
    virtual_source = api_data_dir / "artifacts" / "uploads" / "source.pdf"
    virtual_source.parent.mkdir(parents=True, exist_ok=True)
    virtual_source.write_bytes(b"demo")
    post_calls: list[dict[str, object]] = []
    get_calls: list[dict[str, object]] = []
    emitted: list[dict[str, object]] = []
    status_payloads = iter(
        [
            {"status": "pending", "queued_ahead": 2},
            {"status": "processing", "queued_ahead": 1},
            {"status": "completed", "queued_ahead": 0},
            {"results": {"source": {"md_content": "# parsed"}}},
        ]
    )

    def fake_post(url: str, **kwargs: object) -> _FakeResponse:
        post_calls.append({"url": url, "timeout": kwargs["timeout"]})
        assert kwargs["data"] == {
            "backend": "pipeline",
            "effort": "standard",
            "return_md": "true",
            "response_format_zip": "false",
        }
        return _FakeResponse(
            {
                "task_id": "task-1",
                "status_url": "/tasks/task-1",
                "result_url": "/tasks/task-1/result",
            }
        )

    def fake_get(url: str, **kwargs: object) -> _FakeResponse:
        get_calls.append({"url": url, "timeout": kwargs["timeout"]})
        return _FakeResponse(next(status_payloads))

    with patch("tools._artifacts_root", return_value=(api_data_dir / "artifacts").resolve()):
        with patch.dict(
            os.environ,
            {
                "MINERU_BASE_URL": "https://mineru.example",
                "MINERU_BACKEND": "pipeline",
                "MINERU_EFFORT": "standard",
                "MINERU_TIMEOUT_SECONDS": "321",
            },
            clear=True,
        ):
            with (
                patch("tools.requests.post", side_effect=fake_post),
                patch("tools.requests.get", side_effect=fake_get),
                patch("tools.get_stream_writer", return_value=emitted.append),
                patch("tools.time.sleep") as sleep_mock,
            ):
                parsed = parse_document(
                    "/artifacts/uploads/source.pdf",
                    "/artifacts/generated/output.md",
                )

    parsed_payload = json.loads(parsed)
    assert Path(parsed_payload["source"]) == virtual_source.resolve()
    assert Path(parsed_payload["output_path"]) == (api_data_dir / "artifacts" / "generated" / "output.md").resolve()
    assert Path(parsed_payload["output_path"]).read_text(encoding="utf-8") == "# parsed"
    assert parsed_payload["task_id"] == "task-1"
    assert parsed_payload["status_url"] == "https://mineru.example/tasks/task-1"
    assert parsed_payload["result_url"] == "https://mineru.example/tasks/task-1/result"
    assert parsed_payload["markdown_bytes"] == len("# parsed".encode("utf-8"))
    assert [call["timeout"] for call in post_calls + get_calls] == [321, 321, 321, 321, 321]
    assert [call["url"] for call in get_calls] == [
        "https://mineru.example/tasks/task-1",
        "https://mineru.example/tasks/task-1",
        "https://mineru.example/tasks/task-1",
        "https://mineru.example/tasks/task-1/result",
    ]
    assert [event["status"] for event in emitted] == [
        "submitted",
        "pending",
        "processing",
        "completed",
    ]
    assert all(event["task_id"] == "task-1" for event in emitted)
    assert emitted[1]["queued_ahead"] == 2
    assert emitted[2]["queued_ahead"] == 1
    assert emitted[3]["queued_ahead"] == 0
    assert [call.args[0] for call in sleep_mock.call_args_list] == [120.0, 120.0]

    try:
        parse_document("/artifacts/../x")
    except ValueError as exc:
        assert str(exc) == "Invalid /artifacts path: /artifacts/../x"
    else:
        raise AssertionError("parse_document must reject /artifacts path escapes")


def _check_failed_status_progress(tmp: str) -> None:
    source = Path(tmp) / "broken.pdf"
    source.write_bytes(b"demo")
    emitted: list[dict[str, object]] = []
    calls: list[dict[str, object]] = []

    def fake_post(url: str, **kwargs: object) -> _FakeResponse:
        calls.append({"url": url, "timeout": kwargs["timeout"]})
        return _FakeResponse(
            {
                "task_id": "task-2",
                "status_url": "https://mineru.example/tasks/task-2",
                "result_url": "https://mineru.example/tasks/task-2/result",
            }
        )

    def fake_get(url: str, **kwargs: object) -> _FakeResponse:
        calls.append({"url": url, "timeout": kwargs["timeout"]})
        return _FakeResponse({"status": "failed", "error": "bad pdf", "queued_ahead": 0})

    with patch.dict(
        os.environ,
        {
            "MINERU_BASE_URL": "https://mineru.example",
            "MINERU_BACKEND": "pipeline",
            "MINERU_EFFORT": "standard",
            "MINERU_TIMEOUT_SECONDS": "45",
        },
        clear=True,
    ):
        with (
            patch("tools.requests.post", side_effect=fake_post),
            patch("tools.requests.get", side_effect=fake_get),
            patch("tools.get_stream_writer", return_value=emitted.append),
        ):
            try:
                parse_document(str(source))
            except RuntimeError as exc:
                assert str(exc) == "bad pdf"
            else:
                raise AssertionError("parse_document must raise the MinerU failure")

    assert [call["timeout"] for call in calls] == [45, 45]
    assert [event["status"] for event in emitted] == ["submitted", "failed"]
    assert emitted[-1]["task_id"] == "task-2"
    assert emitted[-1]["error"] == "bad pdf"


if __name__ == "__main__":
    run()

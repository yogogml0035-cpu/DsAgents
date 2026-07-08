from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from tools import MINERU_POLL_INTERVAL_SECONDS, _extract_markdown, default_tool_catalog, parse_documents


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload
        self.text = payload if isinstance(payload, str) else str(payload)

    def json(self) -> object:
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self) -> None:
        return None


def run() -> None:
    assert _extract_markdown({"md_content": "# ok"}) == "# ok"
    assert default_tool_catalog().handlers[0].__name__ == "parse_documents"

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        _check_parse_documents_env_guard(tmp)
        _check_single_file_list(tmp)
        _check_uploaded_sources_reuse_upload_suffix(tmp)
        _check_multi_file_partial_failures(tmp)
        _check_all_invalid_inputs(tmp)
        _check_failed_status_progress(tmp)


def _check_parse_documents_env_guard(tmp: str) -> None:
    source = Path(tmp) / "sample.pdf"
    source.write_text("demo", encoding="utf-8")
    with (
        patch("tools._artifacts_root", return_value=(Path(tmp) / "artifacts").resolve()),
        patch.dict(os.environ, {}, clear=True),
    ):
        try:
            parse_documents([str(source)])
        except RuntimeError as exc:
            assert str(exc) == "Missing required environment variable: MINERU_BASE_URL"
        else:
            raise AssertionError("parse_documents must fail fast when MINERU_BASE_URL is missing")


def _check_single_file_list(tmp: str) -> None:
    api_data_dir = Path(tmp) / "single-data"
    source = api_data_dir / "inputs" / "single.pdf"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"demo")
    post_calls: list[dict[str, object]] = []
    get_calls: list[dict[str, object]] = []
    emitted: list[dict[str, object]] = []
    status_payloads = iter(
        [
            {"status": "completed"},
            {"results": {"single.pdf": {"md_content": "# parsed"}}},
        ]
    )

    def fake_post(url: str, **kwargs: object) -> _FakeResponse:
        files = kwargs["files"]
        assert isinstance(files, list)
        post_calls.append(
            {
                "url": url,
                "timeout": kwargs["timeout"],
                "names": [item[1][0] for item in files],
            }
        )
        assert kwargs["data"] == {
            "backend": "vlm-engine",
            "effort": "",
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

    with (
        patch("tools._artifacts_root", return_value=(api_data_dir / "artifacts").resolve()),
        patch.dict(
            os.environ,
            {
                "MINERU_BASE_URL": "https://mineru.example",
                "MINERU_BACKEND": "vlm-engine",
                "MINERU_EFFORT": "",
                "MINERU_TIMEOUT_SECONDS": "321",
            },
            clear=True,
        ),
        patch("tools.requests.post", side_effect=fake_post),
        patch("tools.requests.get", side_effect=fake_get),
        patch("tools.get_stream_writer", return_value=emitted.append),
        patch("tools.time.strftime", return_value="20260708010203"),
    ):
        parsed_payload = parse_documents([str(source)])

    assert parsed_payload["task_id"] == "task-1"
    assert parsed_payload["status_url"] == "https://mineru.example/tasks/task-1"
    assert parsed_payload["result_url"] == "https://mineru.example/tasks/task-1/result"
    assert parsed_payload["failed"] == []
    assert parsed_payload["succeeded"] == [
        {
            "file_path": str(source),
            "output_path": "/artifacts/downloads/single_20260708010203.md",
            "bytes": len("# parsed".encode("utf-8")),
        }
    ]
    output_file = api_data_dir / "artifacts" / "downloads" / "single_20260708010203.md"
    assert output_file.read_text(encoding="utf-8") == "# parsed"
    assert post_calls == [
        {
            "url": "https://mineru.example/tasks",
            "timeout": 321,
            "names": ["single.pdf"],
        }
    ]
    assert [call["timeout"] for call in get_calls] == [321, 321]
    assert [call["url"] for call in get_calls] == [
        "https://mineru.example/tasks/task-1",
        "https://mineru.example/tasks/task-1/result",
    ]
    assert emitted == [
        {
            "name": "parse_documents",
            "status": "submitted",
            "task_id": "task-1",
            "file_paths": [str(source)],
            "output_paths": ["/artifacts/downloads/single_20260708010203.md"],
            "succeeded_count": 0,
            "failed_count": 0,
        },
        {
            "name": "parse_documents",
            "status": "completed",
            "task_id": "task-1",
            "file_paths": [str(source)],
            "output_paths": ["/artifacts/downloads/single_20260708010203.md"],
            "succeeded_count": 1,
            "failed_count": 0,
        },
    ]


def _check_uploaded_sources_reuse_upload_suffix(tmp: str) -> None:
    api_data_dir = Path(tmp) / "uploaded-source-data"
    artifacts_dir = api_data_dir / "artifacts"
    first = artifacts_dir / "uploads" / "report_20260708000000.pdf"
    second = artifacts_dir / "uploads" / "report_20260708000000_2.pdf"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    status_payloads = iter(
        [
            {"status": "completed"},
            {
                "results": {
                    first.name: {"md_content": "# first"},
                    second.name: {"md_content": "# second"},
                }
            },
        ]
    )

    def fake_post(_url: str, **_kwargs: object) -> _FakeResponse:
        return _FakeResponse(
            {
                "task_id": "task-upload",
                "status_url": "/tasks/task-upload",
                "result_url": "/tasks/task-upload/result",
            }
        )

    def fake_get(_url: str, **_kwargs: object) -> _FakeResponse:
        return _FakeResponse(next(status_payloads))

    file_paths = [
        f"/artifacts/uploads/{first.name}",
        f"/artifacts/uploads/{second.name}",
    ]
    with (
        patch("tools._artifacts_root", return_value=artifacts_dir.resolve()),
        patch.dict(
            os.environ,
            {
                "MINERU_BASE_URL": "https://mineru.example",
                "MINERU_BACKEND": "vlm-engine",
                "MINERU_EFFORT": "",
                "MINERU_TIMEOUT_SECONDS": "45",
            },
            clear=True,
        ),
        patch("tools.requests.post", side_effect=fake_post),
        patch("tools.requests.get", side_effect=fake_get),
        patch("tools.time.strftime", return_value="20260708040506"),
    ):
        parsed_payload = parse_documents(file_paths)

    assert parsed_payload["succeeded"] == [
        {
            "file_path": file_paths[0],
            "output_path": "/artifacts/downloads/report_20260708000000.md",
            "bytes": len("# first".encode("utf-8")),
        },
        {
            "file_path": file_paths[1],
            "output_path": "/artifacts/downloads/report_20260708000000_2.md",
            "bytes": len("# second".encode("utf-8")),
        },
    ]
    assert parsed_payload["failed"] == []
    assert (artifacts_dir / "downloads" / "report_20260708000000.md").read_text(encoding="utf-8") == "# first"
    assert (
        artifacts_dir / "downloads" / "report_20260708000000_2.md"
    ).read_text(encoding="utf-8") == "# second"


def _check_multi_file_partial_failures(tmp: str) -> None:
    api_data_dir = Path(tmp) / "batch-data"
    artifacts_dir = api_data_dir / "artifacts"
    first = artifacts_dir / "uploads" / "first.pdf"
    second = artifacts_dir / "uploads" / "second.pdf"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    missing = "/artifacts/uploads/missing.pdf"
    post_calls: list[dict[str, object]] = []
    get_calls: list[dict[str, object]] = []
    emitted: list[dict[str, object]] = []
    status_payloads = iter(
        [
            {"status": "pending"},
            {"status": "processing"},
            {"status": "completed"},
            {
                "results": {
                    "result-a": {"md_content": "# first"},
                    "result-b": {"error": "ocr failed"},
                }
            },
        ]
    )

    def fake_post(url: str, **kwargs: object) -> _FakeResponse:
        files = kwargs["files"]
        assert isinstance(files, list)
        post_calls.append(
            {
                "url": url,
                "timeout": kwargs["timeout"],
                "names": [item[1][0] for item in files],
            }
        )
        return _FakeResponse(
            {
                "task_id": "task-2",
                "status_url": "https://mineru.example/tasks/task-2",
                "result_url": "https://mineru.example/tasks/task-2/result",
            }
        )

    def fake_get(url: str, **kwargs: object) -> _FakeResponse:
        get_calls.append({"url": url, "timeout": kwargs["timeout"]})
        return _FakeResponse(next(status_payloads))

    file_paths = [
        "/artifacts/uploads/first.pdf",
        missing,
        "/artifacts/uploads/second.pdf",
    ]
    with (
        patch("tools._artifacts_root", return_value=artifacts_dir.resolve()),
        patch.dict(
            os.environ,
            {
                "MINERU_BASE_URL": "https://mineru.example",
                "MINERU_BACKEND": "vlm-engine",
                "MINERU_EFFORT": "",
                "MINERU_TIMEOUT_SECONDS": "45",
            },
            clear=True,
        ),
        patch("tools.requests.post", side_effect=fake_post),
        patch("tools.requests.get", side_effect=fake_get),
        patch("tools.get_stream_writer", return_value=emitted.append),
        patch("tools.time.strftime", return_value="20260708020304"),
        patch("tools.time.sleep") as sleep_mock,
    ):
        parsed_payload = parse_documents(file_paths)

    assert parsed_payload["task_id"] == "task-2"
    assert parsed_payload["succeeded"] == [
        {
            "file_path": "/artifacts/uploads/first.pdf",
            "output_path": "/artifacts/downloads/first_20260708020304.md",
            "bytes": len("# first".encode("utf-8")),
        }
    ]
    assert parsed_payload["failed"] == [
        {
            "file_path": missing,
            "error": f"File not found: {(artifacts_dir / 'uploads' / 'missing.pdf').resolve()}",
        },
        {
            "file_path": "/artifacts/uploads/second.pdf",
            "error": "ocr failed",
        },
    ]
    assert (artifacts_dir / "downloads" / "first_20260708020304.md").read_text(encoding="utf-8") == "# first"
    assert post_calls == [
        {
            "url": "https://mineru.example/tasks",
            "timeout": 45,
            "names": ["first.pdf", "second.pdf"],
        }
    ]
    assert [call["timeout"] for call in get_calls] == [45, 45, 45, 45]
    assert [call["url"] for call in get_calls] == [
        "https://mineru.example/tasks/task-2",
        "https://mineru.example/tasks/task-2",
        "https://mineru.example/tasks/task-2",
        "https://mineru.example/tasks/task-2/result",
    ]
    assert [call.args[0] for call in sleep_mock.call_args_list] == [
        MINERU_POLL_INTERVAL_SECONDS,
        MINERU_POLL_INTERVAL_SECONDS,
    ]
    assert [event["status"] for event in emitted] == [
        "submitted",
        "pending",
        "processing",
        "completed",
    ]
    assert emitted[0]["file_paths"] == file_paths
    assert emitted[0]["failed_count"] == 1
    assert emitted[-1]["succeeded_count"] == 1
    assert emitted[-1]["failed_count"] == 2
    assert emitted[-1]["output_paths"] == ["/artifacts/downloads/first_20260708020304.md"]


def _check_all_invalid_inputs(tmp: str) -> None:
    artifacts_dir = (Path(tmp) / "invalid-data" / "artifacts").resolve()
    emitted: list[dict[str, object]] = []
    with (
        patch("tools._artifacts_root", return_value=artifacts_dir),
        patch("tools.get_stream_writer", return_value=emitted.append),
    ):
        parsed_payload = parse_documents(
            [
                "/artifacts/../x",
                "/artifacts/uploads/missing.pdf",
            ]
        )

    assert parsed_payload == {
        "task_id": None,
        "status_url": None,
        "result_url": None,
        "succeeded": [],
        "failed": [
            {"file_path": "/artifacts/../x", "error": "Invalid /artifacts path: /artifacts/../x"},
            {
                "file_path": "/artifacts/uploads/missing.pdf",
                "error": f"File not found: {(artifacts_dir / 'uploads' / 'missing.pdf').resolve()}",
            },
        ],
    }
    assert emitted == [
        {
            "name": "parse_documents",
            "status": "completed",
            "file_paths": ["/artifacts/../x", "/artifacts/uploads/missing.pdf"],
            "output_paths": [],
            "succeeded_count": 0,
            "failed_count": 2,
        }
    ]


def _check_failed_status_progress(tmp: str) -> None:
    source = Path(tmp) / "broken.pdf"
    source.write_bytes(b"demo")
    emitted: list[dict[str, object]] = []
    calls: list[dict[str, object]] = []

    def fake_post(url: str, **kwargs: object) -> _FakeResponse:
        calls.append({"url": url, "timeout": kwargs["timeout"]})
        return _FakeResponse(
            {
                "task_id": "task-3",
                "status_url": "https://mineru.example/tasks/task-3",
                "result_url": "https://mineru.example/tasks/task-3/result",
            }
        )

    def fake_get(url: str, **kwargs: object) -> _FakeResponse:
        calls.append({"url": url, "timeout": kwargs["timeout"]})
        return _FakeResponse({"status": "failed", "error": "bad pdf"})

    with (
        patch("tools._artifacts_root", return_value=(Path(tmp) / "failed-data" / "artifacts").resolve()),
        patch.dict(
            os.environ,
            {
                "MINERU_BASE_URL": "https://mineru.example",
                "MINERU_BACKEND": "vlm-engine",
                "MINERU_EFFORT": "",
                "MINERU_TIMEOUT_SECONDS": "45",
            },
            clear=True,
        ),
        patch("tools.requests.post", side_effect=fake_post),
        patch("tools.requests.get", side_effect=fake_get),
        patch("tools.get_stream_writer", return_value=emitted.append),
        patch("tools.time.strftime", return_value="20260708030405"),
    ):
        try:
            parse_documents([str(source)])
        except RuntimeError as exc:
            assert str(exc) == "bad pdf"
        else:
            raise AssertionError("parse_documents must raise the MinerU failure")

    assert [call["timeout"] for call in calls] == [45, 45]
    assert [event["status"] for event in emitted] == ["submitted", "failed"]
    assert emitted[-1]["task_id"] == "task-3"
    assert emitted[-1]["failed_count"] == 1
    assert emitted[-1]["error"] == "bad pdf"


if __name__ == "__main__":
    run()

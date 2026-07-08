from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools import MINERU_POLL_INTERVAL_SECONDS, default_tool_catalog, extract_archives, parse_documents


_MINERU_FORM_DATA = {
    "backend": "vlm-engine",
    "effort": "",
    "return_md": "true",
    "return_content_list": "true",
    "return_images": "true",
    "return_original_file": "true",
    "response_format_zip": "true",
}


class _FakeJsonResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload
        self.text = payload if isinstance(payload, str) else str(payload)

    def json(self) -> object:
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self) -> None:
        return None


class _FakeZipResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 1):
        view = memoryview(self._payload)
        for start in range(0, len(view), chunk_size):
            yield bytes(view[start : start + chunk_size])

    def __enter__(self) -> "_FakeZipResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def _build_zip(members: dict[str, bytes]) -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def run() -> None:
    assert [handler.__name__ for handler in default_tool_catalog().handlers] == [
        "parse_documents",
        "extract_archives",
    ]

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        _check_parse_documents_env_guard(tmp)
        _check_single_file_list(tmp)
        _check_uploaded_sources_reuse_upload_stem(tmp)
        _check_multi_file_partial_failures(tmp)
        _check_all_invalid_inputs(tmp)
        _check_failed_status_progress(tmp)
        _check_extract_archives(tmp)


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
    zip_bytes = _build_zip({"single/auto/single.md": b"# parsed"})

    def fake_post(url: str, **kwargs: object) -> _FakeJsonResponse:
        files = kwargs["files"]
        assert isinstance(files, list)
        post_calls.append(
            {
                "url": url,
                "timeout": kwargs["timeout"],
                "names": [item[1][0] for item in files],
            }
        )
        assert kwargs["data"] == _MINERU_FORM_DATA
        return _FakeJsonResponse(
            {
                "task_id": "task-1",
                "status_url": "/tasks/task-1",
                "result_url": "/tasks/task-1/result",
            }
        )

    def fake_get(url: str, **kwargs: object):
        get_calls.append({"url": url, "timeout": kwargs.get("timeout")})
        if url.endswith("/result"):
            return _FakeZipResponse(zip_bytes)
        return _FakeJsonResponse({"status": "completed"})

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
    assert parsed_payload["archive_path"] == "/artifacts/downloads/single.zip"
    assert parsed_payload["failed"] == []
    assert parsed_payload["succeeded"] == [{"file_path": str(source)}]
    archive_file = api_data_dir / "artifacts" / "downloads" / "single.zip"
    assert archive_file.read_bytes() == zip_bytes
    assert post_calls == [
        {
            "url": "https://mineru.example/tasks",
            "timeout": 321,
            "names": ["single.pdf"],
        }
    ]
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
            "succeeded_count": 0,
            "failed_count": 0,
        },
        {
            "name": "parse_documents",
            "status": "completed",
            "task_id": "task-1",
            "file_paths": [str(source)],
            "archive_path": "/artifacts/downloads/single.zip",
            "succeeded_count": 1,
            "failed_count": 0,
        },
    ]


def _check_uploaded_sources_reuse_upload_stem(tmp: str) -> None:
    api_data_dir = Path(tmp) / "uploaded-source-data"
    artifacts_dir = api_data_dir / "artifacts"
    first = artifacts_dir / "uploads" / "report_20260708000000.pdf"
    second = artifacts_dir / "uploads" / "report_20260708000000_2.pdf"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    zip_bytes = _build_zip(
        {
            "report_20260708000000/auto/full.md": b"# first",
            "report_20260708000000_2/auto/full.md": b"# second",
        }
    )

    def fake_post(_url: str, **_kwargs: object) -> _FakeJsonResponse:
        return _FakeJsonResponse(
            {
                "task_id": "task-upload",
                "status_url": "/tasks/task-upload",
                "result_url": "/tasks/task-upload/result",
            }
        )

    def fake_get(url: str, **_kwargs: object):
        if url.endswith("/result"):
            return _FakeZipResponse(zip_bytes)
        return _FakeJsonResponse({"status": "completed"})

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

    assert parsed_payload["archive_path"] == "/artifacts/downloads/report_20260708000000_etc_20260708040506.zip"
    assert parsed_payload["succeeded"] == [
        {"file_path": file_paths[0]},
        {"file_path": file_paths[1]},
    ]
    assert parsed_payload["failed"] == []
    archive_file = artifacts_dir / "downloads" / "report_20260708000000_etc_20260708040506.zip"
    assert archive_file.read_bytes() == zip_bytes


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
    zip_bytes = _build_zip({"first/auto/first.md": b"# first"})
    status_payloads = iter(
        [
            {"status": "pending"},
            {"status": "processing"},
            {"status": "completed"},
        ]
    )

    def fake_post(url: str, **kwargs: object) -> _FakeJsonResponse:
        files = kwargs["files"]
        assert isinstance(files, list)
        post_calls.append(
            {
                "url": url,
                "timeout": kwargs["timeout"],
                "names": [item[1][0] for item in files],
            }
        )
        return _FakeJsonResponse(
            {
                "task_id": "task-2",
                "status_url": "https://mineru.example/tasks/task-2",
                "result_url": "https://mineru.example/tasks/task-2/result",
            }
        )

    def fake_get(url: str, **kwargs: object):
        get_calls.append({"url": url, "timeout": kwargs.get("timeout")})
        if url.endswith("/result"):
            return _FakeZipResponse(zip_bytes)
        return _FakeJsonResponse(next(status_payloads))

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
    assert parsed_payload["archive_path"] == "/artifacts/downloads/first_etc_20260708020304.zip"
    assert parsed_payload["succeeded"] == [
        {"file_path": "/artifacts/uploads/first.pdf"},
        {"file_path": "/artifacts/uploads/second.pdf"},
    ]
    assert parsed_payload["failed"] == [
        {
            "file_path": missing,
            "error": f"File not found: {(artifacts_dir / 'uploads' / 'missing.pdf').resolve()}",
        }
    ]
    archive_file = artifacts_dir / "downloads" / "first_etc_20260708020304.zip"
    assert archive_file.read_bytes() == zip_bytes
    assert post_calls == [
        {
            "url": "https://mineru.example/tasks",
            "timeout": 45,
            "names": ["first.pdf", "second.pdf"],
        }
    ]
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
    assert emitted[-1]["succeeded_count"] == 2
    assert emitted[-1]["failed_count"] == 1
    assert emitted[-1]["archive_path"] == "/artifacts/downloads/first_etc_20260708020304.zip"


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
        "archive_path": None,
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
            "succeeded_count": 0,
            "failed_count": 2,
        }
    ]


def _check_failed_status_progress(tmp: str) -> None:
    source = Path(tmp) / "broken.pdf"
    source.write_bytes(b"demo")
    emitted: list[dict[str, object]] = []
    calls: list[dict[str, object]] = []

    def fake_post(url: str, **kwargs: object) -> _FakeJsonResponse:
        calls.append({"url": url, "timeout": kwargs["timeout"]})
        return _FakeJsonResponse(
            {
                "task_id": "task-3",
                "status_url": "https://mineru.example/tasks/task-3",
                "result_url": "https://mineru.example/tasks/task-3/result",
            }
        )

    def fake_get(url: str, **kwargs: object) -> _FakeJsonResponse:
        calls.append({"url": url, "timeout": kwargs["timeout"]})
        return _FakeJsonResponse({"status": "failed", "error": "bad pdf"})

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

    assert [call["url"] for call in calls] == [
        "https://mineru.example/tasks",
        "https://mineru.example/tasks/task-3",
    ]
    assert [call["timeout"] for call in calls] == [45, 45]
    assert [event["status"] for event in emitted] == ["submitted", "failed"]
    assert emitted[-1]["task_id"] == "task-3"
    assert emitted[-1]["failed_count"] == 1
    assert emitted[-1]["error"] == "bad pdf"


def _check_extract_archives(tmp: str) -> None:
    api_data_dir = Path(tmp) / "extract-data"
    artifacts_dir = api_data_dir / "artifacts"
    downloads_dir = artifacts_dir / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)

    members = {
        "report/auto/report.md": b"# parsed",
        "report/auto/content_list.json": b"[]",
        "report/auto/images/fig1.png": b"PNGDATA",
        "report/origin/report.pdf": b"%PDF-1.4",
    }
    archive_path = downloads_dir / "report.zip"
    archive_path.write_bytes(_build_zip(members))

    invalid_virtual = "/artifacts/downloads/missing.zip"

    with (
        patch("tools._artifacts_root", return_value=artifacts_dir.resolve()),
        patch("tools.get_stream_writer", return_value=lambda _payload: None),
    ):
        payload = extract_archives(["/artifacts/downloads/report.zip", invalid_virtual])

    assert payload["succeeded"] == [
        {
            "archive_path": "/artifacts/downloads/report.zip",
            "output_dir": "/artifacts/downloads/report",
            "files": [
                "/artifacts/downloads/report/report/auto/content_list.json",
                "/artifacts/downloads/report/report/auto/images/fig1.png",
                "/artifacts/downloads/report/report/auto/report.md",
                "/artifacts/downloads/report/report/origin/report.pdf",
            ],
        }
    ]
    assert payload["failed"] == [
        {
            "zip_path": invalid_virtual,
            "error": f"File not found: {(downloads_dir / 'missing.zip').resolve()}",
        }
    ]
    assert (downloads_dir / "report" / "report" / "auto" / "report.md").read_text(encoding="utf-8") == "# parsed"
    assert (downloads_dir / "report" / "report" / "auto" / "images" / "fig1.png").read_bytes() == b"PNGDATA"


if __name__ == "__main__":
    run()

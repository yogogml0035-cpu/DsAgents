from __future__ import annotations

import argparse
import mimetypes
import os
import shutil
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

import requests


DEFAULT_BASE_URL = "http://127.0.0.1:8500"
DEFAULT_PDF_DIR = Path(__file__).resolve().parent / "tests_file" / "测试用例1"
DEFAULT_DOWNLOAD_DIR = Path(__file__).resolve().parent / "tests_file" / "测试用例1" / "downloaded"
DEFAULT_REQUEST = "将这些pdf调用工具解析并下载下来"
DEFAULT_TIMEOUT_SECONDS = 7200.0
DEFAULT_UPLOAD_TIMEOUT_SECONDS = 600.0
DEFAULT_POLL_SECONDS = 0.5


def run() -> None:
    if os.getenv("DSAGENTS_RUN_REAL_MULTI_PDF_TEST") != "1":
        print("skipped real multi-pdf integration test; set DSAGENTS_RUN_REAL_MULTI_PDF_TEST=1 to run it")
        return
    _exercise_real_multi_pdf_run(
        base_url=os.getenv("DSAGENTS_API_BASE_URL", DEFAULT_BASE_URL),
        pdf_paths=_pdf_paths(Path(os.getenv("DSAGENTS_PDF_DIR", str(DEFAULT_PDF_DIR)))),
        download_dir=Path(os.getenv("DSAGENTS_MD_DOWNLOAD_DIR", str(DEFAULT_DOWNLOAD_DIR))),
        request=os.getenv("DSAGENTS_MULTI_PDF_REQUEST", DEFAULT_REQUEST),
        timeout_seconds=float(os.getenv("DSAGENTS_REAL_MULTI_PDF_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)),
        upload_timeout_seconds=float(os.getenv("DSAGENTS_REAL_MULTI_PDF_UPLOAD_TIMEOUT_SECONDS", DEFAULT_UPLOAD_TIMEOUT_SECONDS)),
        poll_seconds=float(os.getenv("DSAGENTS_REAL_MULTI_PDF_POLL_SECONDS", DEFAULT_POLL_SECONDS)),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload PDFs, ask the agent to parse them with MinerU, and copy generated markdown files locally."
    )
    parser.add_argument("--base-url", default=os.getenv("DSAGENTS_API_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--pdf-dir", type=Path, default=Path(os.getenv("DSAGENTS_PDF_DIR", str(DEFAULT_PDF_DIR))))
    parser.add_argument("--pdf", type=Path, action="append", default=[])
    parser.add_argument(
        "--download-dir",
        type=Path,
        default=Path(os.getenv("DSAGENTS_MD_DOWNLOAD_DIR", str(DEFAULT_DOWNLOAD_DIR))),
    )
    parser.add_argument("--request", default=os.getenv("DSAGENTS_MULTI_PDF_REQUEST", DEFAULT_REQUEST))
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("DSAGENTS_REAL_MULTI_PDF_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)),
    )
    parser.add_argument(
        "--upload-timeout",
        type=float,
        default=float(os.getenv("DSAGENTS_REAL_MULTI_PDF_UPLOAD_TIMEOUT_SECONDS", DEFAULT_UPLOAD_TIMEOUT_SECONDS)),
    )
    parser.add_argument(
        "--poll",
        type=float,
        default=float(os.getenv("DSAGENTS_REAL_MULTI_PDF_POLL_SECONDS", DEFAULT_POLL_SECONDS)),
    )
    args = parser.parse_args()
    _exercise_real_multi_pdf_run(
        base_url=args.base_url,
        pdf_paths=args.pdf or _pdf_paths(args.pdf_dir),
        download_dir=args.download_dir,
        request=args.request,
        timeout_seconds=args.timeout,
        upload_timeout_seconds=args.upload_timeout,
        poll_seconds=args.poll,
    )


def _exercise_real_multi_pdf_run(
    *,
    base_url: str,
    pdf_paths: list[Path],
    download_dir: Path,
    request: str,
    timeout_seconds: float,
    upload_timeout_seconds: float,
    poll_seconds: float,
) -> None:
    if not pdf_paths:
        raise AssertionError("No PDF files found")
    for pdf_path in pdf_paths:
        if not pdf_path.is_file():
            raise AssertionError(f"PDF file not found: {pdf_path}")

    session = requests.Session()
    uploaded_files = _upload_pdfs(session, base_url, pdf_paths, upload_timeout_seconds)
    output_specs = _output_specs(uploaded_files)
    run_payload = _start_run(session, base_url, uploaded_files, _build_request(request, output_specs))
    final_payload = _wait_for_run(session, base_url, run_payload["run_id"], timeout_seconds, poll_seconds)
    _assert_parse_document_called(final_payload, output_specs)
    downloads = _copy_markdown_outputs(output_specs, download_dir)

    print("\ndownloaded markdown files:")
    for path in downloads:
        print(path)


def _pdf_paths(pdf_dir: Path) -> list[Path]:
    return sorted(pdf_dir.glob("*.pdf"))


def _upload_pdfs(
    session: requests.Session,
    base_url: str,
    pdf_paths: list[Path],
    upload_timeout_seconds: float,
) -> list[dict[str, Any]]:
    uploaded_files = []
    for pdf_path in pdf_paths:
        mime_type = mimetypes.guess_type(pdf_path.name)[0] or "application/pdf"
        # ponytail: requests buffers multipart bodies, so upload huge PDFs one at a time.
        with pdf_path.open("rb") as pdf_file:
            response = session.post(
                _url(base_url, "/upload"),
                files=[("files", (pdf_path.name, pdf_file, mime_type))],
                timeout=upload_timeout_seconds,
            )
        response.raise_for_status()
        file_info = response.json()["files"][0]
        assert file_info["size"] == pdf_path.stat().st_size
        uploaded_files.append(file_info)
        print(f"uploaded: {file_info['file_path']}")
    return uploaded_files


def _output_specs(uploaded_files: list[dict[str, Any]]) -> list[dict[str, str]]:
    batch = f"multi_pdf_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    seen: dict[str, int] = {}
    specs = []
    for file_info in uploaded_files:
        stem = Path(file_info["name"]).stem or "document"
        seen[stem] = seen.get(stem, 0) + 1
        suffix = "" if seen[stem] == 1 else f"_{seen[stem]}"
        specs.append(
            {
                "name": file_info["name"],
                "file_path": file_info["file_path"],
                "output_path": f"/artifacts/generated/{batch}/{stem}{suffix}.md",
            }
        )
    return specs


def _build_request(request: str, output_specs: list[dict[str, str]]) -> str:
    lines = [
        request,
        "",
        "必须对下面每个 PDF 各调用一次 parse_document；output_path 必须严格使用清单里的路径。",
        "全部完成后，只列出生成的文件路径，不要只总结 PDF 内容。",
        "",
        "PDF 清单：",
    ]
    for index, spec in enumerate(output_specs, 1):
        lines.append(f"{index}. file_path={spec['file_path']} output_path={spec['output_path']}")
    return "\n".join(lines)


def _start_run(
    session: requests.Session,
    base_url: str,
    uploaded_files: list[dict[str, Any]],
    request: str,
) -> dict[str, Any]:
    response = session.post(
        _url(base_url, "/runs"),
        json={
            "session_id": None,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": request}]
                    + [{"type": "artifact", "path": file_info["file_path"]} for file_info in uploaded_files],
                }
            ],
        },
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    assert payload["status"] == "queued"
    print(f"run queued: {payload['run_id']}")
    return payload


def _wait_for_run(
    session: requests.Session,
    base_url: str,
    run_id: str,
    timeout_seconds: float,
    poll_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_latest_text = ""
    while time.monotonic() < deadline:
        response = session.get(_url(base_url, f"/runs/{run_id}"), timeout=30)
        response.raise_for_status()
        payload = response.json()
        run = payload["run"]
        latest_text = _latest_text(payload.get("latest_content_event"))
        if latest_text and latest_text != last_latest_text:
            last_latest_text = latest_text
            print("\nlatest result:")
            print(latest_text)

        if run["status"] == "succeeded":
            assert run["reply"] or last_latest_text, "model returned an empty final reply"
            return payload
        if run["status"] == "failed":
            raise AssertionError(f"run failed: {run.get('error')}")

        time.sleep(poll_seconds)

    raise AssertionError(f"run {run_id} did not finish within {timeout_seconds} seconds")


def _assert_parse_document_called(payload: dict[str, Any], output_specs: list[dict[str, str]]) -> None:
    calls = [
        event.get("payload", {})
        for event in payload.get("events", [])
        if event.get("type") == "tool_call" and event.get("payload", {}).get("name") == "parse_document"
    ]
    called_paths = {call.get("args", {}).get("file_path") for call in calls}
    missing = [spec["file_path"] for spec in output_specs if spec["file_path"] not in called_paths]
    if missing:
        raise AssertionError(f"parse_document was not called for: {missing}")


def _copy_markdown_outputs(output_specs: list[dict[str, str]], download_dir: Path) -> list[Path]:
    download_dir.mkdir(parents=True, exist_ok=True)
    downloads = []
    for spec in output_specs:
        source = _local_artifact_path(spec["output_path"])
        if not source.is_file():
            raise AssertionError(f"Markdown output not found: {source}")
        target = download_dir / source.name
        shutil.copy2(source, target)
        assert target.stat().st_size > 0
        downloads.append(target)
    return downloads


def _local_artifact_path(artifact_path: str) -> Path:
    virtual_path = PurePosixPath(artifact_path)
    if ".." in virtual_path.parts:
        raise AssertionError(f"Invalid artifact path: {artifact_path}")
    if artifact_path == "/artifacts" or artifact_path.startswith("/artifacts/"):
        relative = virtual_path.relative_to("/artifacts")
        return (Path(__file__).resolve().parents[1] / "data" / "artifacts").joinpath(*relative.parts)
    return Path(artifact_path)


def _latest_text(event: dict[str, Any] | None) -> str:
    if not event:
        return ""
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        return ""
    return payload.get("text") or payload.get("content") or ""


def _url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + path


if __name__ == "__main__":
    main()

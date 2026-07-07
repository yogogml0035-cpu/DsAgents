from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from tools import _extract_markdown, _find_value, default_tool_catalog, parse_document


def run() -> None:
    assert _find_value({"data": {"task_id": "abc"}}, {"task_id"}) == "abc"
    assert _extract_markdown({"result": {"md_content": "# ok"}}) == "# ok"
    assert default_tool_catalog().handlers[0].__name__ == "parse_document"

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        _check_parse_document_env_guard(tmp)
        _check_virtual_artifacts(tmp)


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


def _check_virtual_artifacts(tmp: str) -> None:
    api_data_dir = Path(tmp) / "api-data"
    virtual_source = api_data_dir / "artifacts" / "uploads" / "source.pdf"
    virtual_source.parent.mkdir(parents=True, exist_ok=True)
    virtual_source.write_text("demo", encoding="utf-8")
    with patch("tools._artifacts_root", return_value=(api_data_dir / "artifacts").resolve()):
        with patch.dict(
            os.environ,
            {
                "MINERU_BASE_URL": "https://mineru.example",
                "MINERU_BACKEND": "pipeline",
                "MINERU_EFFORT": "standard",
                "MINERU_TIMEOUT_SECONDS": "10",
            },
            clear=True,
        ):
            with patch("tools._submit_mineru_task", return_value="task-1"), patch(
                "tools._wait_for_mineru_result",
                return_value={"md_content": "# parsed"},
            ):
                parsed = parse_document(
                    "/artifacts/uploads/source.pdf",
                    "/artifacts/generated/output.md",
                )
        parsed_payload = json.loads(parsed)
        assert Path(parsed_payload["source"]) == virtual_source.resolve()
        assert Path(parsed_payload["output_path"]) == (api_data_dir / "artifacts" / "generated" / "output.md").resolve()
        assert Path(parsed_payload["output_path"]).read_text(encoding="utf-8") == "# parsed"
        try:
            parse_document("/artifacts/../x")
        except ValueError as exc:
            assert str(exc) == "Invalid /artifacts path: /artifacts/../x"
        else:
            raise AssertionError("parse_document must reject /artifacts path escapes")


if __name__ == "__main__":
    run()

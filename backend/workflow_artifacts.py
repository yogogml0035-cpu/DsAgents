from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

from artifact_names import make_unique_name


def artifacts_root() -> Path:
    from resources import ResourceConfig

    return ResourceConfig().artifacts_dir.resolve()


def resolve_artifact_path(
    raw_path: str | None,
    *,
    root: Path | None = None,
    allow_local: bool = False,
) -> Path:
    if not raw_path:
        raise ValueError("Artifact path is required")
    if raw_path == "/artifacts" or raw_path.startswith("/artifacts/"):
        virtual = PurePosixPath(raw_path)
        if ".." in virtual.parts:
            raise ValueError(f"Invalid /artifacts path: {raw_path}")
        artifact_root = (root or artifacts_root()).resolve()
        resolved = artifact_root.joinpath(*virtual.relative_to("/artifacts").parts).resolve()
        resolved.relative_to(artifact_root)
        return resolved
    if allow_local:
        return Path(raw_path).expanduser().resolve()
    raise ValueError(f"Expected an explicit /artifacts/... path: {raw_path}")


def to_virtual_artifact_path(path: Path, *, root: Path | None = None) -> str:
    artifact_root = (root or artifacts_root()).resolve()
    relative = path.resolve().relative_to(artifact_root)
    return f"/artifacts/{relative.as_posix()}"


def unique_download_path(stem: str, suffix: str) -> Path:
    """Atomically reserve and return a new download path."""
    downloads = artifacts_root() / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d%H%M%S")
    while True:
        path = downloads / make_unique_name(downloads, f"{stem}_{timestamp}{suffix}")
        try:
            path.touch(exist_ok=False)
        except FileExistsError:
            continue
        return path


def write_json_artifact(stem: str, payload: dict[str, Any]) -> str:
    path = unique_download_path(stem, ".json")
    try:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(_json_value(payload), handle, ensure_ascii=False, indent=2)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return to_virtual_artifact_path(path)


def read_json_artifact(raw_path: str) -> dict[str, Any]:
    path = resolve_artifact_path(raw_path)
    if path.suffix.lower() != ".json" or not path.is_file():
        raise ValueError(f"JSON artifact not found: {raw_path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON artifact must contain an object: {raw_path}")
    return payload


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value

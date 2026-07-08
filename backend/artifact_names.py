from __future__ import annotations

import re
from pathlib import Path


UPLOAD_FALLBACK_NAME = "upload"
_UPLOAD_SUFFIX_RE = re.compile(r"^(?P<base>.+)_(?P<timestamp>\d{14})(?:_(?P<index>[2-9]\d*))?$")


def clean_filename(filename: str | None) -> str:
    if not filename:
        return UPLOAD_FALLBACK_NAME
    cleaned = filename.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(" " if ch.isspace() else ch for ch in cleaned).strip()
    return cleaned or UPLOAD_FALLBACK_NAME


def make_unique_name(
    directory: Path,
    filename: str | None,
    reserved_names: set[str] | None = None,
) -> str:
    cleaned = clean_filename(filename)
    stem = Path(cleaned).stem
    suffix = Path(cleaned).suffix
    reserved = reserved_names if reserved_names is not None else set()
    counter = 1
    while True:
        suffix_index = "" if counter == 1 else f"_{counter}"
        candidate = f"{stem}{suffix_index}{suffix}"
        if candidate not in reserved and not (directory / candidate).exists():
            reserved.add(candidate)
            return candidate
        counter += 1


def make_timestamped_name(
    directory: Path,
    filename: str | None,
    timestamp: str,
    reserved_names: set[str] | None = None,
) -> str:
    cleaned = clean_filename(filename)
    stem = Path(cleaned).stem
    suffix = Path(cleaned).suffix
    return make_unique_name(directory, f"{stem}_{timestamp}{suffix}", reserved_names)


def has_upload_suffix(stem: str) -> bool:
    return _UPLOAD_SUFFIX_RE.fullmatch(stem) is not None

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from dsagents.integrations.mineru import extract_archives, parse_documents
from dsagents.skills.philipswgqimport.scripts.tools import (
    generate_philips_wgq_import,
    save_philips_wgq_extraction,
)
from dsagents.skills.tecanimport.scripts.tools import (
    generate_tecan_import,
    save_tecan_extraction,
)

ToolHandler = Callable[..., Any]


@dataclass(frozen=True)
class ToolCatalog:
    handlers: tuple[ToolHandler, ...]

    def as_list(self) -> list[ToolHandler]:
        return list(self.handlers)


def default_tool_catalog() -> ToolCatalog:
    """Static registration: MinerU 通用工具 + 两个 Skill 各两个业务工具。

    新增 Skill 时在此追加一行静态 import + 一行注册，不复制 runtime、不自动扫描。
    """
    return ToolCatalog(
        (
            parse_documents,
            extract_archives,
            save_philips_wgq_extraction,
            generate_philips_wgq_import,
            save_tecan_extraction,
            generate_tecan_import,
        )
    )

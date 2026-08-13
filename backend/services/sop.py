"""按任务和输入证据加载静态 SOP 与通用规则，不依赖企业知识库。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from ..config import ROOT_DIR

KNOWLEDGE_DIR = ROOT_DIR / "agent_knowledge"


def _read(relative: str) -> str:
    path = (KNOWLEDGE_DIR / relative).resolve()
    if KNOWLEDGE_DIR.resolve() not in path.parents:
        raise ValueError("SOP 路径越界")
    return path.read_text(encoding="utf-8").strip()


def industry_profile(texts: Iterable[str]) -> str:
    corpus = "\n".join(str(item or "") for item in texts).lower()
    # “烧结”“金属化”也常见于粉末冶金和表面处理，不能仅凭一个通用工艺词
    # 把项目路由到电子陶瓷 SOP。专用路线只由明确材料/产品词，或陶瓷语境中的
    # 工艺组合触发。
    strong_markers = (
        "电子陶瓷", "陶瓷基板", "陶瓷衬底", "氮化铝", "氧化铝陶瓷",
        "aln substrate", "al2o3 substrate",
    )
    if any(marker in corpus for marker in strong_markers):
        return "electronic_ceramics"
    has_ceramic_context = any(marker in corpus for marker in ("陶瓷", "ceramic"))
    has_electronic_process = any(marker in corpus for marker in ("金属化", "厚膜", "薄膜", "共烧"))
    return "electronic_ceramics" if has_ceramic_context and has_electronic_process else "general"


def load(task: str, *, profile: str = "general", template: str = "") -> tuple[str, str]:
    files = ["common/base.md"]
    versions = []
    if task == "drawing":
        files.append("drawing/base.md")
        versions.append("drawing-1.0")
    elif task == "drawing_verify":
        files.extend(["drawing/base.md", "drawing/verification.md"])
        versions.append("drawing-verify-1.0")
    elif task == "process":
        files.append("process/base.md")
        if template:
            files.append(f"process/{template}.md")
        versions.append("process-1.0")
    files.append(f"industry/{profile if profile in {'general', 'electronic_ceramics'} else 'general'}.md")
    return "\n\n".join(_read(item) for item in files), "+".join(versions) or "common-1.0"


def process_rules() -> dict:
    return json.loads(_read("rules/process_rules.json"))

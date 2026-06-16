"""
版本对比(校核审签的核心):对两份 IR 快照做结构化 diff,产出可读的变更清单。

纯函数,不做 IO —— 版本快照的存取在 storage.store(record_version / list_versions
/ get_version / set_version_status),审签流(草稿→送审→通过/驳回)在 main.py 串接。
这样"谁、在什么时间、把哪个参数从多少改成多少、经谁审签"形成可追溯证据链(PRD 6.5)。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# 零件级标量字段(直接比较)
_PART_SCALARS = ("name", "role", "tolerance_general", "quantity", "confidence", "parent_id")


def summarize(ir: Optional[dict]) -> dict:
    """一份 IR 的概览(用于版本列表展示,不含完整内容)。"""
    ir = ir or {}
    parts = ir.get("parts", []) or []
    confs = [p.get("confidence") for p in parts if isinstance(p.get("confidence"), (int, float))]
    avg = round(sum(confs) / len(confs), 3) if confs else None
    return {
        "device_name": ir.get("device_name"),
        "parts": len(parts),
        "assemblies": len(ir.get("assemblies", []) or []),
        "standard_parts": len(ir.get("standard_parts", []) or []),
        "open_questions": len(ir.get("open_questions", []) or []),
        "avg_confidence": avg,
    }


def _part_changes(old_p: dict, new_p: dict) -> List[dict]:
    """单个零件(同 part_id)从 old 到 new 的字段级变更。"""
    changes: List[dict] = []
    for f in _PART_SCALARS:
        if old_p.get(f) != new_p.get(f):
            changes.append({"field": f, "old": old_p.get(f), "new": new_p.get(f)})

    om = old_p.get("material") or {}
    nm = new_p.get("material") or {}
    if om.get("spec") != nm.get("spec"):
        changes.append({"field": "material.spec", "old": om.get("spec"), "new": nm.get("spec")})

    of = old_p.get("features") or []
    nf = new_p.get("features") or []
    if len(of) != len(nf):
        changes.append({"field": "features.count", "old": len(of), "new": len(nf)})
    for i in range(min(len(of), len(nf))):
        oo, nn = of[i], nf[i]
        if oo.get("type") != nn.get("type"):
            changes.append({"field": f"features[{i}].type", "old": oo.get("type"), "new": nn.get("type")})
        for k in sorted(set(oo) | set(nn)):
            if k in ("type",):
                continue
            if oo.get(k) != nn.get(k):
                changes.append({"field": f"features[{i}].{k}", "old": oo.get(k), "new": nn.get(k)})
    return changes


def diff_ir(old: Optional[dict], new: Optional[dict]) -> dict:
    """两份 IR 的结构化差异。零件按 part_id 对齐: 增 / 删 / 改(字段级)。"""
    old = old or {}
    new = new or {}
    header: List[dict] = []
    for f in ("device_name", "design_intent", "overall_dims", "assembly_notes"):
        if old.get(f) != new.get(f):
            header.append({"field": f, "old": old.get(f), "new": new.get(f)})

    old_parts = {p.get("part_id"): p for p in (old.get("parts") or [])}
    new_parts = {p.get("part_id"): p for p in (new.get("parts") or [])}

    added = [
        {"part_id": pid, "name": new_parts[pid].get("name")}
        for pid in new_parts if pid not in old_parts
    ]
    removed = [
        {"part_id": pid, "name": old_parts[pid].get("name")}
        for pid in old_parts if pid not in new_parts
    ]
    modified: List[dict] = []
    for pid, np in new_parts.items():
        if pid in old_parts:
            ch = _part_changes(old_parts[pid], np)
            if ch:
                modified.append({"part_id": pid, "name": np.get("name"), "changes": ch})

    n_changes = len(header) + len(added) + len(removed) + sum(len(m["changes"]) for m in modified)
    return {
        "header": header,
        "parts": {"added": added, "removed": removed, "modified": modified},
        "total_changes": n_changes,
        "summary_old": summarize(old),
        "summary_new": summarize(new),
    }

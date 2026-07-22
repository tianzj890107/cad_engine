"""
版本对比(校核审签的核心):对两份 IR 快照做结构化 diff,产出可读的变更清单。

纯函数,不做 IO —— 版本快照的存取在 storage.store(record_version / list_versions
/ get_version / set_version_status),审签流(草稿→送审→通过/驳回)在 main.py 串接。
这样"谁、在什么时间、把哪个参数从多少改成多少、经谁审签"形成可追溯证据链(PRD 6.5)。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# 零件级标量字段(直接比较)
_PART_SCALARS = (
    "name", "model_no", "manufacturer", "model_specification", "model_lookup_evidence",
    "role", "tolerance_general", "quantity", "confidence", "parent_id",
)
_STANDARD_SCALARS = ("spec", "category", "quantity", "model_no", "manufacturer", "model_specification")
_FIELD_LABELS = {
    "name": "名称", "model_no": "型号", "manufacturer": "制造商", "model_specification": "规格摘要",
    "model_lookup_evidence": "联网核验依据", "role": "功能角色", "tolerance_general": "一般公差",
    "quantity": "数量", "confidence": "识别置信度", "parent_id": "所属总成", "material.spec": "材料",
    "features.count": "特征数量",
}


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

    # 标准件/外购件没有稳定的 part_id；平台写入时只追加或按型号更新，因此用
    # model_no（无型号时回退 spec）作为对比键，保证型号核验同步可在版本里看见。
    def standard_key(item: dict, index: int) -> str:
        return str(item.get("model_no") or item.get("spec") or f"#{index}").strip().upper()

    old_standards = {standard_key(item, index): item for index, item in enumerate(old.get("standard_parts") or [])}
    new_standards = {standard_key(item, index): item for index, item in enumerate(new.get("standard_parts") or [])}
    standard_added = [new_standards[key] for key in new_standards if key not in old_standards]
    standard_removed = [old_standards[key] for key in old_standards if key not in new_standards]
    standard_modified: List[dict] = []
    for key, current in new_standards.items():
        previous = old_standards.get(key)
        if previous is None:
            continue
        changes = [
            {"field": field, "old": previous.get(field), "new": current.get(field)}
            for field in _STANDARD_SCALARS if previous.get(field) != current.get(field)
        ]
        if changes:
            standard_modified.append({"key": key, "spec": current.get("spec"), "changes": changes})

    n_changes = (
        len(header) + len(added) + len(removed) + sum(len(m["changes"]) for m in modified)
        + len(standard_added) + len(standard_removed) + sum(len(m["changes"]) for m in standard_modified)
    )
    return {
        "header": header,
        "parts": {"added": added, "removed": removed, "modified": modified},
        "standard_parts": {"added": standard_added, "removed": standard_removed, "modified": standard_modified},
        "total_changes": n_changes,
        "summary_old": summarize(old),
        "summary_new": summarize(new),
    }


def change_summary(old: Optional[dict], new: Optional[dict], max_items: int = 5) -> str:
    """把版本 diff 压缩成版本卡片可直接阅读的中文说明。"""
    diff = diff_ir(old, new)
    if not diff["total_changes"]:
        return "与上一版本相比，业务数据未发生变化。"
    items: List[str] = []
    for change in diff["header"]:
        label = _FIELD_LABELS.get(change["field"], change["field"])
        items.append(f"{label}：{change.get('old') or '—'} → {change.get('new') or '—'}")
    for part in diff["parts"]["added"]:
        items.append(f"新增零件 {part.get('part_id')} {part.get('name') or ''}".strip())
    for part in diff["parts"]["removed"]:
        items.append(f"删除零件 {part.get('part_id')} {part.get('name') or ''}".strip())
    for part in diff["parts"]["modified"]:
        for change in part["changes"]:
            label = _FIELD_LABELS.get(change["field"], change["field"])
            items.append(f"{part.get('part_id')} {label}：{change.get('old') if change.get('old') is not None else '—'} → {change.get('new') if change.get('new') is not None else '—'}")
    for standard in diff["standard_parts"]["added"]:
        items.append(f"新增 BOM / 外购件：{standard.get('spec') or standard.get('model_no') or '未命名项'}")
    for standard in diff["standard_parts"]["removed"]:
        items.append(f"删除 BOM / 外购件：{standard.get('spec') or standard.get('model_no') or '未命名项'}")
    for standard in diff["standard_parts"]["modified"]:
        for change in standard["changes"]:
            label = _FIELD_LABELS.get(change["field"], change["field"])
            items.append(f"BOM {label}：{change.get('old') if change.get('old') is not None else '—'} → {change.get('new') if change.get('new') is not None else '—'}")
    if len(items) > max_items:
        return "；".join(items[:max_items]) + f"；另有 {len(items) - max_items} 项变更"
    return "；".join(items)

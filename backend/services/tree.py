"""
层级结构树(PRD 6.3 P0): 由 IR 的 assemblies + parts(parent_id) 派生出
  设备(Equipment) → 总成/子总成(Assembly) → 零件(Part)
的嵌套树，并给出层级编号(1 / 1.1 / 1.1.2 ...)供 BOM 与前端使用。

非破坏式: parts 仍是扁平列表，几何/2D/BOM 生成不受影响; 本模块只负责"组织视图"。
对脏数据稳健: 未知 parent / 自环 / 环路 一律降级挂到设备根节点下,绝不死循环。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..models.ir import Assembly, DesignIR, Part


def _safe_parent(node_id: str, parent_id: Optional[str],
                 valid_ids: set, seen_chain: set) -> Optional[str]:
    """返回合法的父 id;非法(不存在/自环)则归 None(挂根)。"""
    if not parent_id or parent_id == node_id or parent_id not in valid_ids:
        return None
    return parent_id


def build_tree(ir: DesignIR) -> dict:
    """构建嵌套树。返回以设备为根的 dict: {type,name,children:[...]}"""
    asm_by_id: Dict[str, Assembly] = {a.assembly_id: a for a in ir.assemblies}
    valid_asm_ids = set(asm_by_id.keys())

    # 总成节点
    asm_nodes: Dict[str, dict] = {
        a.assembly_id: {
            "type": "assembly",
            "id": a.assembly_id,
            "name": a.name,
            "role": a.role,
            "quantity": a.quantity,
            "children": [],
        }
        for a in ir.assemblies
    }

    root = {"type": "equipment", "id": None, "name": ir.device_name or "设备",
            "children": []}

    # 挂总成(按 parent_id;含环路检测)
    def asm_parent(a: Assembly) -> Optional[str]:
        pid = _safe_parent(a.assembly_id, a.parent_id, valid_asm_ids, set())
        # 环路检测: 顺着 parent 链走,若回到自己则归根
        cur, chain = pid, {a.assembly_id}
        while cur:
            if cur in chain:
                return None
            chain.add(cur)
            cur = asm_by_id[cur].parent_id if cur in asm_by_id else None
        return pid

    for a in ir.assemblies:
        pid = asm_parent(a)
        (asm_nodes[pid]["children"] if pid in asm_nodes else root["children"]).append(
            asm_nodes[a.assembly_id]
        )

    # 挂零件
    for p in ir.parts:
        node = {
            "type": "part",
            "id": p.part_id,
            "name": p.name,
            "role": p.role,
            "quantity": p.quantity,
            "material": p.material.spec if p.material else None,
            "confidence": p.confidence,
            "children": [],
        }
        pid = p.parent_id if p.parent_id in valid_asm_ids else None
        (asm_nodes[pid]["children"] if pid in asm_nodes else root["children"]).append(node)

    return root


def part_levels(ir: DesignIR) -> Dict[str, str]:
    """返回 {part_id: 层级编号字符串}，如 'P-003' -> '1.2.1'。"""
    root = build_tree(ir)
    levels: Dict[str, str] = {}

    def walk(node: dict, prefix: str):
        idx = 0
        for child in node.get("children", []):
            idx += 1
            num = f"{prefix}{idx}" if not prefix else f"{prefix}.{idx}"
            if child["type"] == "part":
                levels[child["id"]] = num
            walk(child, num)

    walk(root, "")
    return levels

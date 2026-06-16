"""
BOM(物料清单)生成与导出(PRD 6.3 P0)。

从设计意图 IR 派生层级 BOM:
  - 加工件: 来自 parts[](含材料/数量/特征摘要/置信度/推荐);
  - 标准件: 来自 standard_parts[](外购)。
导出 CSV(UTF-8 BOM 头,Excel 直接打开不乱码)。后续可扩展 Excel/层级树。
"""
from __future__ import annotations

import csv
import io
from typing import Dict, List

from ..models.ir import DesignIR, Feature
from . import tree

_HEADERS = [
    "序号", "层级", "编号", "名称", "类别", "材料", "数量", "规格/特征摘要", "置信度", "备注",
]


def _feature_summary(features: List[Feature]) -> str:
    parts: List[str] = []
    for f in features:
        dims = []
        for k in ("length", "width", "thickness", "height", "diameter", "radius", "distance"):
            v = getattr(f, k, None)
            if v is not None:
                dims.append(f"{k}={v}")
        seg = f.type.value if hasattr(f.type, "value") else str(f.type)
        if dims:
            seg += "(" + ",".join(dims) + ")"
        parts.append(seg)
    return "; ".join(parts)


def build_bom(ir: DesignIR) -> List[Dict[str, object]]:
    """生成层级 BOM 行(list of dict)，按结构树层级排序。"""
    levels = tree.part_levels(ir)
    # 按层级编号排序(无层级的排最后)
    ordered = sorted(
        ir.parts,
        key=lambda p: _level_key(levels.get(p.part_id, "")),
    )
    rows: List[Dict[str, object]] = []
    idx = 1
    for p in ordered:
        rows.append({
            "序号": idx,
            "层级": levels.get(p.part_id, ""),
            "编号": p.part_id,
            "名称": p.name,
            "类别": "加工件",
            "材料": (p.material.spec if p.material else ""),
            "数量": p.quantity,
            "规格/特征摘要": _feature_summary(p.features),
            "置信度": f"{int((p.confidence or 0) * 100)}%",
            "备注": (p.recommendation or ""),
        })
        idx += 1
    for s in ir.standard_parts:
        rows.append({
            "序号": idx,
            "层级": "",
            "编号": "",
            "名称": s.spec,
            "类别": f"标准件/外购({s.category or ''})".rstrip("()"),
            "材料": "",
            "数量": s.quantity,
            "规格/特征摘要": s.spec,
            "置信度": "",
            "备注": "标准件",
        })
        idx += 1
    return rows


def _level_key(level: str):
    """'1.2.10' -> (1,2,10);空串排最后。"""
    if not level:
        return (9999,)
    try:
        return tuple(int(x) for x in level.split("."))
    except ValueError:
        return (9999,)


def to_csv(ir: DesignIR) -> bytes:
    """导出 CSV(UTF-8 with BOM，Excel 友好)。"""
    rows = build_bom(ir)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_HEADERS)
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")

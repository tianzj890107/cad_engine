"""
3D STEP 模型导入解析(PRD 6.2 P0)。

用 OCCT(via CadQuery)读取客户上传的 STEP/STP 3D 模型,反解出:
  - 每个实体(零件)的 包围盒 / 体积;
  - 基础形状分类(plate / cylinder / box)与 孔特征(圆柱面检测);
  - 组装成 DesignIR(与"图→IR"统一),供结构树 / BOM / 3D / 2D 复用。

注意: 导入零件的"真实几何"被保留(后续直接用该实体导出 STEP/STL/2D),
IR 中的 features 是用于结构树/BOM 展示与再参数化的"最佳近似 + 孔摘要"。
这与平台主线一致: 几何来自确定性 CAD 内核,可制造、可校验、可追溯。
"""
from __future__ import annotations

import tempfile
from collections import Counter
from pathlib import Path
from typing import List, Tuple

from ..models.ir import DesignIR, Feature, FeatureType, Part, Provenance

try:
    import cadquery as cq  # type: ignore
    AVAILABLE = True
except Exception:  # pragma: no cover
    AVAILABLE = False


def _cyl_diameters(solid) -> List[float]:
    out = []
    for f in solid.Faces():
        if f.geomType() == "CYLINDER":
            try:
                out.append(round(f._geomAdaptor().Cylinder().Radius() * 2.0, 2))
            except Exception:
                pass
    return out


def _classify(solid) -> Tuple[List[Feature], str, float]:
    """返回 (近似特征列表, 溯源说明, 置信度)。"""
    bb = solid.BoundingBox()
    dx, dy, dz = round(bb.xlen, 2), round(bb.ylen, 2), round(bb.zlen, 2)
    vol = round(solid.Volume(), 1)
    dims = sorted([dx, dy, dz])
    thin = dims[0] / dims[2] if dims[2] else 1.0
    cyls = _cyl_diameters(solid)

    feats: List[Feature] = []
    footprint = min(dx, dy)

    # 圆柱体: XY 近似等径且存在与外径相当的圆柱面
    round_xy = abs(dx - dy) <= 0.05 * max(dx, dy, 1)
    body_diam = max(cyls) if cyls else None
    is_cylinder = round_xy and body_diam is not None and abs(body_diam - dx) <= 0.1 * dx

    if is_cylinder:
        feats.append(Feature(type=FeatureType.cylinder, diameter=dx, height=dz))
        hole_cyls = [d for d in cyls if d < body_diam - 0.1]
        footprint = dx
    elif thin < 0.3:
        # 板件: 最小尺寸为厚度
        t = dims[0]
        lw = sorted([dx, dy, dz], reverse=True)[:2]
        feats.append(Feature(type=FeatureType.plate, length=lw[0], width=lw[1], thickness=t))
        hole_cyls = [d for d in cyls if d < min(lw[0], lw[1])]
    else:
        feats.append(Feature(type=FeatureType.box, length=dx, width=dy, height=dz))
        hole_cyls = [d for d in cyls if d < footprint]

    # 孔: 按直径分组计数
    hole_note = ""
    if hole_cyls:
        grouped = Counter(hole_cyls)
        for d, cnt in sorted(grouped.items()):
            if cnt == 1:
                feats.append(Feature(type=FeatureType.hole, diameter=d, purpose="导入检测孔"))
            else:
                feats.append(Feature(
                    type=FeatureType.hole_pattern, diameter=d, count_x=cnt, count_y=1,
                    purpose=f"导入检测孔 ×{cnt}",
                ))
        hole_note = "; 孔: " + ", ".join(f"⌀{d}×{c}" for d, c in sorted(grouped.items()))

    types = Counter(f.geomType() for f in solid.Faces())
    note = (
        f"从STEP导入: 包围盒 {dx}×{dy}×{dz}mm, 体积 {vol}mm³, "
        f"面 {sum(types.values())}({dict(types)}){hole_note}"
    )
    # 几何精确但分类为近似 → 中高置信度
    return feats, note, 0.7


def import_step(file_bytes: bytes, filename: str) -> Tuple[DesignIR, List[Tuple[str, object]]]:
    """解析 STEP 文件 -> (DesignIR, [(part_id, solid), ...])。"""
    if not AVAILABLE:
        raise RuntimeError("CadQuery 未安装，无法解析 STEP。")

    suffix = Path(filename).suffix or ".step"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
        tf.write(file_bytes)
        tmp = tf.name
    try:
        wp = cq.importers.importStep(tmp)
        solids = wp.solids().vals()
    finally:
        try:
            Path(tmp).unlink()
        except Exception:
            pass

    if not solids:
        raise RuntimeError("STEP 中未找到任何实体(solid)。")

    parts: List[Part] = []
    solid_map: List[Tuple[str, object]] = []
    for i, s in enumerate(solids, 1):
        pid = f"P-{i:03d}"
        feats, note, conf = _classify(s)
        parts.append(Part(
            part_id=pid, name=f"零件{i}", role="STEP 导入实体",
            features=feats, confidence=conf, provenance=Provenance(note=note),
        ))
        solid_map.append((pid, s))

    ir = DesignIR(
        device_name=Path(filename).stem,
        design_intent="由 3D 模型(STEP)导入解析得到。几何为客户原始实体，特征为近似分类。",
        overall_dims=None,
        parts=parts,
        assembly_notes=f"共解析出 {len(parts)} 个实体。" if len(parts) > 1 else None,
    )
    return ir, solid_map

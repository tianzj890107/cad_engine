"""
2D 工程图生成(PRD 6.4 P0 关键缺口)。

从零件的参数化实体(复用 geometry.build_solid)确定性地投影出工程图:
  - 多视图: 主视(front)、俯视(top)、侧视(right)、等轴测(iso)，
    用 OCCT 的隐藏线消除(HLR)生成,导出 SVG 供 Web 查看;
  - 下料/加工 DXF: 取零件中截面(section)导出 DXF,供激光/水切割等制造;
  - 标题栏信息(零件号/名称/材料/数量/总体尺寸)由前端结合 IR 与几何属性展示。

与平台主线一致: 大模型不参与几何; 2D 图同样由确定性 CAD 内核(OCCT)产出,
可制造、可校验、可追溯。FreeCAD 非必需(用已安装的 CadQuery/OCP 即可)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ..models.ir import Part
from . import geometry

try:
    import cadquery as cq  # type: ignore
    from cadquery import exporters  # type: ignore
    from cadquery.occ_impl.exporters.svg import getSVG  # type: ignore

    AVAILABLE = geometry.CADQUERY_AVAILABLE
except Exception:  # pragma: no cover
    AVAILABLE = False


# 视图名 -> 投影方向(相机朝向)
_VIEWS = {
    "front": (0, -1, 0),   # 主视(从 -Y 看)
    "top": (0, 0, 1),      # 俯视(从 +Z 看)
    "right": (1, 0, 0),    # 侧视(从 +X 看)
    "iso": (-1.75, 1.1, 5),  # 等轴测
}
_VIEW_LABEL = {"front": "主视图", "top": "俯视图", "right": "侧视图", "iso": "等轴测"}


@dataclass
class PartDrawingResult:
    part_id: str
    name: str
    ok: bool
    views: Dict[str, str] = field(default_factory=dict)  # view -> filename
    dxf: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None


def _save_view_svg(shape, view: str, out_path: Path, warnings: List[str]) -> bool:
    try:
        svg = getSVG(
            shape,
            {
                "projectionDir": _VIEWS[view],
                "showAxes": False,
                "width": 420,
                "height": 320,
                "marginLeft": 12,
                "marginTop": 12,
                "strokeWidth": -1,
            },
        )
        out_path.write_text(svg, encoding="utf-8")
        return True
    except Exception as e:
        warnings.append(f"{_VIEW_LABEL.get(view, view)} 生成失败: {e}")
        return False


def _save_dxf(wp, out_path: Path, warnings: List[str]) -> bool:
    """优先用中截面导 DXF(适合板/壳下料); 失败则退回实体投影。"""
    try:
        section = wp.section()
        exporters.export(section, str(out_path), exportType="DXF")
        if out_path.exists() and out_path.stat().st_size > 0:
            return True
    except Exception as e:
        warnings.append(f"截面 DXF 失败，改用实体投影: {e}")
    try:
        exporters.export(wp, str(out_path), exportType="DXF")
        return out_path.exists() and out_path.stat().st_size > 0
    except Exception as e:
        warnings.append(f"DXF 导出失败: {e}")
        return False


def _render(part_id: str, name: str, wp, out_dir: Path) -> PartDrawingResult:
    """从一个 Workplane 投影出多视图 SVG + DXF。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: List[str] = []
    try:
        shape = wp.val()
        views: Dict[str, str] = {}
        for view in _VIEWS:
            fname = f"{part_id}_{view}.svg"
            if _save_view_svg(shape, view, out_dir / fname, warnings):
                views[view] = fname

        dxf_name = f"{part_id}.dxf"
        dxf = dxf_name if _save_dxf(wp, out_dir / dxf_name, warnings) else None

        return PartDrawingResult(
            part_id, name, ok=bool(views), views=views, dxf=dxf, warnings=warnings
        )
    except Exception as e:
        return PartDrawingResult(part_id, name, ok=False, warnings=warnings, error=str(e))


def generate_drawings(part: Part, out_dir: Path) -> PartDrawingResult:
    """从零件特征(IR)构建实体并投影 2D 工程图。"""
    if not AVAILABLE:
        return PartDrawingResult(
            part.part_id, part.name, ok=False, error="CadQuery 未安装，无法生成 2D 工程图。"
        )
    try:
        wp = geometry.build_solid(part)
    except Exception as e:
        return PartDrawingResult(part.part_id, part.name, ok=False, error=str(e))
    return _render(part.part_id, part.name, wp, out_dir)


def generate_from_solid(part_id: str, name: str, solid, out_dir: Path) -> PartDrawingResult:
    """从一个已有实体(如 STEP 导入)投影 2D 工程图。"""
    if not AVAILABLE:
        return PartDrawingResult(part_id, name, ok=False, error="CadQuery 未安装。")
    wp = cq.Workplane().newObject([solid])
    return _render(part_id, name, wp, out_dir)


def generate_all(parts: List[Part], out_dir: Path) -> List[PartDrawingResult]:
    return [generate_drawings(p, out_dir) for p in parts]

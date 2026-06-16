"""
离线烟雾测试: 不需要 API Key，直接用一个内置示例 IR 验证几何内核。

运行: python scripts\smoke_geometry.py
输出: data/_smoke/ 下的 STEP/STL 文件 + 控制台打印质量属性与校验告警。
"""
from __future__ import annotations

import sys
from pathlib import Path

# Windows 控制台默认 cp1252，无法打印中文 —— 强制 stdout 用 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 允许从项目根直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.models.ir import (  # noqa: E402
    DesignIR, Feature, FeatureType, Material, Part,
)
from backend.services import geometry  # noqa: E402


def sample_ir() -> DesignIR:
    base_plate = Part(
        part_id="P-001",
        name="底板",
        role="结构基座",
        material=Material(spec="Q235", density=7.85),
        quantity=1,
        confidence=0.9,
        features=[
            Feature(type=FeatureType.plate, length=320, width=180, thickness=12),
            Feature(
                type=FeatureType.hole_pattern, diameter=9,
                count_x=2, count_y=2, spacing_x=280, spacing_y=140,
                purpose="M8 安装孔",
            ),
            Feature(type=FeatureType.chamfer, distance=2),
        ],
    )
    bushing = Part(
        part_id="P-002",
        name="导向衬套",
        role="轴向定位",
        material=Material(spec="6061-T6", density=2.70),
        quantity=2,
        confidence=0.8,
        features=[
            Feature(type=FeatureType.cylinder, diameter=40, height=30),
            Feature(type=FeatureType.hole, diameter=20, purpose="轴孔"),
        ],
    )
    return DesignIR(
        device_name="传动支架总成(示例)",
        design_intent="承载电机并固定到底板，需可拆卸",
        overall_dims="320 x 180 x 95 mm",
        parts=[base_plate, bushing],
    )


def main() -> None:
    print(f"CadQuery 可用: {geometry.CADQUERY_AVAILABLE}")
    if not geometry.CADQUERY_AVAILABLE:
        print("未安装 CadQuery，无法生成几何。请 `pip install cadquery`。")
        return

    ir = sample_ir()
    out = Path(__file__).resolve().parent.parent / "data" / "_smoke"
    results = geometry.generate_all(ir.parts, out)

    for r in results:
        print(f"\n=== {r.part_id} {r.name} ===")
        print(f"  状态: {'OK' if r.ok else 'FAILED'}")
        if r.error:
            print(f"  错误: {r.error}")
        if r.bbox:
            print(f"  包围盒: {r.bbox} mm")
        if r.volume_mm3:
            print(f"  体积: {r.volume_mm3} mm^3")
        if r.mass_g:
            print(f"  质量: {r.mass_g} g")
        for w in r.warnings:
            print(f"  ⚠ {w}")
        if r.step_path:
            print(f"  STEP: {r.step_path}")
            print(f"  STL : {r.stl_path}")

    print(f"\n输出目录: {out}")


if __name__ == "__main__":
    main()

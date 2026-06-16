"""
设计意图中间表示 (Design Intent IR) —— 整个平台的"契约"。

大模型(Claude)把设备需求原图解析成本结构，CAD 内核据此确定性地生成几何。
关键设计:
  - 用"特征(feature)"语义描述几何(板、孔、孔阵列、圆柱、凸台...)，而非裸坐标，
    便于参数化重建与改参。
  - 每个零件带 confidence(置信度) / provenance(可追溯到原图区域) / 让平台"人在环"。
  - 结构保持扁平(零件列表 + 装配关系)，不递归，以兼容 Claude 的 structured outputs。

注意: 为兼容 structured outputs 的 JSON Schema 限制，这里不使用数值约束
(minimum/maximum 等)；数值合法性在 Python 侧用 validator 校验。
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# 特征 (Feature) —— CAD 内核可识别并参数化生成的几何原语
# --------------------------------------------------------------------------- #
class FeatureType(str, Enum):
    plate = "plate"            # 矩形板: 参数 length/width/thickness
    box = "box"                # 长方体: length/width/height
    cylinder = "cylinder"      # 圆柱: diameter/height
    hole = "hole"              # 单孔: diameter, (x,y) 在顶面
    hole_pattern = "hole_pattern"  # 孔阵列: diameter + count + spacing
    fillet = "fillet"          # 整体倒圆角: radius
    chamfer = "chamfer"        # 整体倒角: distance


class Feature(BaseModel):
    type: FeatureType = Field(..., description="特征类型")
    # 通用尺寸参数(按需填写，单位 mm)
    length: Optional[float] = Field(None, description="长 (plate/box) mm")
    width: Optional[float] = Field(None, description="宽 (plate/box) mm")
    thickness: Optional[float] = Field(None, description="厚 (plate) mm")
    height: Optional[float] = Field(None, description="高 (box/cylinder) mm")
    diameter: Optional[float] = Field(None, description="直径 (cylinder/hole/hole_pattern) mm")
    radius: Optional[float] = Field(None, description="半径 (fillet) mm")
    distance: Optional[float] = Field(None, description="倒角距离 (chamfer) mm")
    # 孔/阵列定位
    x: Optional[float] = Field(None, description="孔中心 X (相对零件中心) mm")
    y: Optional[float] = Field(None, description="孔中心 Y (相对零件中心) mm")
    count_x: Optional[int] = Field(None, description="孔阵列 X 方向数量")
    count_y: Optional[int] = Field(None, description="孔阵列 Y 方向数量")
    spacing_x: Optional[float] = Field(None, description="孔阵列 X 方向间距 mm")
    spacing_y: Optional[float] = Field(None, description="孔阵列 Y 方向间距 mm")
    purpose: Optional[str] = Field(None, description="该特征的用途说明，如 'M8 安装孔'")


# --------------------------------------------------------------------------- #
# 材料 / 公差
# --------------------------------------------------------------------------- #
class Material(BaseModel):
    spec: str = Field(..., description="材料牌号，如 Q235 / 6061-T6 / 304")
    density: Optional[float] = Field(None, description="密度 g/cm^3，用于估算质量")


class Provenance(BaseModel):
    """可追溯: 该零件解析自原图的哪个区域/标注。"""
    bbox: Optional[List[float]] = Field(
        None, description="原图中的归一化包围盒 [x, y, w, h]，取值 0~1"
    )
    note: Optional[str] = Field(None, description="依据的标注/视图说明")


# --------------------------------------------------------------------------- #
# 零件
# --------------------------------------------------------------------------- #
class Part(BaseModel):
    part_id: str = Field(..., description="零件唯一编号，如 P-001")
    name: str = Field(..., description="零件名称")
    role: Optional[str] = Field(None, description="在装配中的角色/功能")
    features: List[Feature] = Field(
        default_factory=list, description="构成该零件的特征列表(按建模顺序)"
    )
    material: Optional[Material] = Field(None, description="材料")
    tolerance_general: Optional[str] = Field(
        None, description="一般公差等级，如 'ISO 2768-m'"
    )
    quantity: int = Field(1, description="该零件数量")
    parent_id: Optional[str] = Field(
        None,
        description="所属总成的 assembly_id；为空表示直接挂在设备根节点下。用于构建层级结构树。",
    )
    confidence: float = Field(
        0.5, description="解析置信度 0~1，低置信度需人工确认"
    )
    provenance: Optional[Provenance] = Field(None, description="原图溯源")
    recommendation: Optional[str] = Field(
        None, description="对该零件的生成/复用建议(平台拆解推荐结果)"
    )


# --------------------------------------------------------------------------- #
# 总成 / 部件(层级结构树的中间节点)
# --------------------------------------------------------------------------- #
class Assembly(BaseModel):
    assembly_id: str = Field(..., description="总成唯一编号，如 A-001")
    name: str = Field(..., description="总成/部件名称")
    parent_id: Optional[str] = Field(
        None, description="父总成 assembly_id；为空表示直接挂在设备根节点下"
    )
    role: Optional[str] = Field(None, description="功能/作用")
    quantity: int = Field(1, description="该总成在父级中的数量")


# --------------------------------------------------------------------------- #
# 标准件
# --------------------------------------------------------------------------- #
class StandardPart(BaseModel):
    spec: str = Field(..., description="标准件规格，如 'GB/T 5783 M8x25'")
    category: Optional[str] = Field(None, description="类别: bolt/nut/washer/bearing...")
    quantity: int = Field(1, description="数量")


# --------------------------------------------------------------------------- #
# 待澄清问题(置信度不足、标注模糊时由大模型提出，交人工)
# --------------------------------------------------------------------------- #
class OpenQuestion(BaseModel):
    field: str = Field(..., description="涉及的字段，如 'P-001.thickness'")
    reason: str = Field(..., description="为何不确定")
    guess: Optional[str] = Field(None, description="模型的最佳猜测")


# --------------------------------------------------------------------------- #
# 整机/设备级 IR (顶层)
# --------------------------------------------------------------------------- #
class DesignIR(BaseModel):
    device_name: str = Field(..., description="设备/总成名称")
    design_intent: str = Field(..., description="对整体设计意图的概括")
    overall_dims: Optional[str] = Field(
        None, description="总体外形尺寸描述，如 '320 x 180 x 95 mm'"
    )
    assemblies: List[Assembly] = Field(
        default_factory=list,
        description="总成/部件中间节点(可嵌套);用于构成 设备-总成-子总成-零件 层级结构树。",
    )
    parts: List[Part] = Field(default_factory=list, description="拆解出的零件列表")
    standard_parts: List[StandardPart] = Field(
        default_factory=list, description="识别到的标准件"
    )
    assembly_notes: Optional[str] = Field(
        None, description="装配关系/配合说明"
    )
    open_questions: List[OpenQuestion] = Field(
        default_factory=list, description="需人工澄清的问题"
    )

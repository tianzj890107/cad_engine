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
from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .coercion import StrList
from .ai import AIResultStatus, FieldEvidence, ValidationSummary


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

    @model_validator(mode="before")
    @classmethod
    def normalize_base_feature_dimensions(cls, value):
        """兼容视觉模型常用的轴向尺寸命名，且只使用它已给出的数值。

        圆柱的轴向尺寸经常被写成 length，而 CAD 内核的 cylinder 原语使用
        height；长方体的 thickness 也常表示竖向厚度。这两种映射不改变尺寸含义。
        """
        if not isinstance(value, dict):
            return value
        data = dict(value)
        feature_type = str(data.get("type") or "").strip().lower()
        if feature_type == FeatureType.cylinder.value:
            if data.get("height") is None and data.get("length") is not None:
                data["height"] = data["length"]
        elif feature_type == FeatureType.box.value:
            if data.get("height") is None and data.get("thickness") is not None:
                data["height"] = data["thickness"]
        return data


# --------------------------------------------------------------------------- #
# 材料 / 公差
# --------------------------------------------------------------------------- #
class Material(BaseModel):
    spec: str = Field(..., description="材料牌号，如 Q235 / 6061-T6 / 304")
    density: Optional[float] = Field(None, description="密度 g/cm^3，用于估算质量")

    @model_validator(mode="before")
    @classmethod
    def normalize_common_model_keys(cls, value):
        """兼容模型常见的 name/grade/material_spec 写法，不臆造材料信息。"""
        if isinstance(value, str):
            return {"spec": value}
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if not str(data.get("spec") or "").strip():
            for key in ("grade", "material_spec", "name"):
                candidate = data.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    data["spec"] = candidate.strip()
                    break
        return data


class Provenance(BaseModel):
    """可追溯: 该零件解析自原图的哪个区域/标注。"""
    bbox: Optional[List[float]] = Field(
        None, description="原图中的归一化包围盒 [x, y, w, h]，取值 0~1"
    )
    note: Optional[str] = Field(None, description="依据的标注/视图说明")

    @model_validator(mode="before")
    @classmethod
    def normalize_plain_note(cls, value):
        """模型有时直接给出处说明文本；它等价于 note，不应使整份 IR 失效。"""
        if isinstance(value, str):
            return {"note": value.strip()} if value.strip() else None
        return value


# --------------------------------------------------------------------------- #
# 零件
# --------------------------------------------------------------------------- #
class Part(BaseModel):
    part_id: str = Field(..., description="零件唯一编号，如 P-001")
    name: str = Field(..., description="零件名称")
    model_no: Optional[str] = Field(
        None, description="图纸/BOM/技术资料中明确出现的制造商型号或料号；不确定时留空"
    )
    manufacturer: Optional[str] = Field(
        None, description="经型号核验确认的制造商；无可靠公开证据时留空"
    )
    model_specification: Optional[str] = Field(
        None, description="经型号核验得到的公开规格摘要；用于 BOM 与工艺评估留痕"
    )
    model_lookup_evidence: Optional[str] = Field(
        None, description="型号联网核验的简要证据；完整来源保存在型号核验记录中"
    )
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

    @field_validator("material", mode="before")
    @classmethod
    def empty_material_means_unknown(cls, value):
        """空材料对象没有可用工程信息，按未知处理而不是让整份 IR 失败。"""
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, dict):
            if not any(
                isinstance(value.get(key), str) and value.get(key).strip()
                for key in ("spec", "grade", "material_spec", "name")
            ):
                return None
        return value


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
    spec: str = Field("待确认规格", description="标准件规格，如 'GB/T 5783 M8x25'")
    category: Optional[str] = Field(None, description="类别: bolt/nut/washer/bearing...")
    quantity: int = Field(1, description="数量")
    model_no: Optional[str] = Field(None, description="外购件型号或料号")
    manufacturer: Optional[str] = Field(None, description="经型号核验确认的制造商")
    model_specification: Optional[str] = Field(None, description="经型号核验得到的公开规格摘要")
    model_lookup_evidence: Optional[str] = Field(None, description="型号联网核验证据摘要")

    @model_validator(mode="before")
    @classmethod
    def normalize_common_model_keys(cls, value):
        """兼容 name/model/type 等常见写法；规格未知时保留为待确认而不中断整份 IR。"""
        if isinstance(value, str):
            return {"spec": value}
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if not str(data.get("spec") or "").strip():
            for key in ("name", "model", "part_number", "standard", "designation", "type"):
                candidate = data.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    data["spec"] = candidate.strip()
                    break
        if not str(data.get("spec") or "").strip():
            kind = data.get("category") or data.get("kind")
            size = data.get("size") or data.get("dimension")
            if isinstance(kind, str) and kind.strip() and isinstance(size, str) and size.strip():
                data["spec"] = f"{kind.strip()} {size.strip()}"
            elif isinstance(kind, str) and kind.strip():
                data["spec"] = kind.strip()
            else:
                # 删除空值，让默认“待确认规格”生效，而不是以空字符串污染 BOM。
                data.pop("spec", None)
        return data


# --------------------------------------------------------------------------- #
# 待澄清问题(置信度不足、标注模糊时由大模型提出，交人工)
# --------------------------------------------------------------------------- #
class OpenQuestion(BaseModel):
    field: str = Field("待确认项", description="涉及的字段，如 'P-001.thickness'")
    reason: str = Field("需人工确认", description="为何不确定")
    guess: Optional[str] = Field(None, description="模型的最佳猜测")

    @model_validator(mode="before")
    @classmethod
    def normalize_common_model_keys(cls, value):
        """把模型偶尔给出的纯文本问题转换为可展示、可追踪的结构。"""
        if isinstance(value, str):
            return {"field": "待确认项", "reason": value.strip() or "需人工确认"}
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if not str(data.get("field") or "").strip():
            for key in ("item", "target", "name", "parameter"):
                candidate = data.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    data["field"] = candidate.strip()
                    break
        if not str(data.get("reason") or "").strip():
            for key in ("question", "description", "text", "message", "content"):
                candidate = data.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    data["reason"] = candidate.strip()
                    break
        if not str(data.get("field") or "").strip():
            data.pop("field", None)
        if not str(data.get("reason") or "").strip():
            data.pop("reason", None)
        return data


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
    evidence_ledger: List[FieldEvidence] = Field(
        default_factory=list, description="字段级证据台账；关键尺寸应逐项提供来源"
    )
    assumptions: StrList = Field(
        default_factory=list, description="解析时采用但尚未被项目资料证实的假设"
    )
    ai_status: AIResultStatus = Field(
        AIResultStatus.partial, description="READY/PARTIAL/BLOCKED"
    )
    validation: ValidationSummary = Field(default_factory=ValidationSummary)
    sop_version: str = Field("", description="本次解析采用的 SOP 版本")

    @field_validator("overall_dims", mode="before")
    @classmethod
    def normalize_overall_dims(cls, value: Any):
        """兼容模型输出的 length/width/height 对象，统一成界面使用的尺寸字符串。"""
        if value is None or isinstance(value, str):
            return value
        if isinstance(value, dict):
            data = dict(value)
            unit = data.get("unit") if isinstance(data.get("unit"), str) else "mm"
            ordered = (("length", "长"), ("width", "宽"), ("height", "高"), ("depth", "深"))
            dimensions = [data[key] for key, _label in ordered if data.get(key) is not None]
            if dimensions:
                return " × ".join(str(item) for item in dimensions) + f" {unit}"
            pairs = [
                f"{key}={item}"
                for key, item in data.items()
                if key != "unit" and item not in (None, "")
            ]
            return "；".join(pairs) if pairs else None
        if isinstance(value, (list, tuple)):
            return " × ".join(str(item) for item in value) + " mm"
        return str(value)

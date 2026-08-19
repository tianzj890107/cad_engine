"""
产线匹配与产能评估 IR —— 技术工艺第 7 步。

延续平台思路:Claude 依据制造工艺所需设备与**真实设备台账**(自有产线 + 外协厂商),
产出**结构化**的设备需求、自有产线匹配、外协安排建议与产能评估;"确认"由人来点。
设备能否满足图纸要求的判定基于维护的设备目录(可追溯)。

注意: 为兼容工具调用承载的结构化输出,不使用数值约束(min/max),在 Python 侧校验。
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from .coercion import StrList
from .cost import WebSource
from .ir import OpenQuestion
from .material import Timing


# --------------------------------------------------------------------------- #
# 设备需求(源自制造工艺工序 + 图纸要求)
# --------------------------------------------------------------------------- #
class EquipReq(BaseModel):
    process: Optional[str] = Field(None, description="对应工序,如 '高温共烧' / '精密研磨'")
    equipment_type: str = Field(..., description="设备类型,如 '烧结炉' / '环抛机'")
    key_requirement: Optional[str] = Field(None, description="关键要求,如 '最高温度≥1700℃/可控气氛' / '面型精度<1µm'")
    qty: Optional[str] = Field(None, description="数量/节拍需求")


# --------------------------------------------------------------------------- #
# 自有产线匹配
# --------------------------------------------------------------------------- #
class InhouseMatch(BaseModel):
    equipment_type: str = Field(..., description="设备类型")
    requirement: Optional[str] = Field(None, description="对应要求")
    matched_equipment: Optional[str] = Field(None, description="匹配到的自有设备名/型号")
    satisfied: bool = Field(False, description="自有产线是否满足")
    capacity: Optional[str] = Field(None, description="产能,如 '500 件/月'")
    gap_notes: Optional[str] = Field(None, description="差距/说明")


# --------------------------------------------------------------------------- #
# 外协安排
# --------------------------------------------------------------------------- #
class OutsourcePlan(BaseModel):
    equipment_type: str = Field(..., description="设备类型")
    requirement: Optional[str] = Field(None, description="对应要求")
    vendor: Optional[str] = Field(None, description="外协厂商")
    equipment: Optional[str] = Field(None, description="外协方设备/型号")
    satisfied: bool = Field(False, description="外协设备是否满足要求")
    cost: Optional[str] = Field(None, description="外协成本/加工费")
    lead_time: Optional[str] = Field(None, description="交期")
    recommend: bool = Field(False, description="是否建议外协(满足且成本合适)")
    reason: Optional[str] = Field(None, description="建议/不建议外协的理由")


# --------------------------------------------------------------------------- #
# Claude 产出的建议
# --------------------------------------------------------------------------- #
class ProductionRecommendation(BaseModel):
    requirements: List[EquipReq] = Field(default_factory=list, description="设备需求清单")
    inhouse_matches: List[InhouseMatch] = Field(default_factory=list, description="自有产线匹配结果")
    outsourcing: List[OutsourcePlan] = Field(default_factory=list, description="外协安排建议")
    capacity_summary: Optional[str] = Field(None, description="产能评估结论")
    conclusion: Optional[str] = Field(None, description="总体匹配/外协结论")
    assumptions: StrList = Field(default_factory=list, description="关键假设")
    open_questions: List[OpenQuestion] = Field(default_factory=list, description="需澄清的问题")
    search_sources: List[WebSource] = Field(default_factory=list, description="联网检索来源(可追溯)")


# --------------------------------------------------------------------------- #
# 平台聚合 / 持久化的完整计划
# --------------------------------------------------------------------------- #
class InhouseSection(BaseModel):
    matches: List[InhouseMatch] = Field(default_factory=list)
    confirmed: bool = False
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None


class OutsourcingSection(BaseModel):
    plans: List[OutsourcePlan] = Field(default_factory=list)
    confirmed: bool = False
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None


class ProductionPlan(BaseModel):
    project_id: Optional[str] = None
    requirements: List[EquipReq] = Field(default_factory=list)
    inhouse: InhouseSection = Field(default_factory=InhouseSection)
    outsourcing: OutsourcingSection = Field(default_factory=OutsourcingSection)
    capacity_summary: Optional[str] = None
    conclusion: Optional[str] = None
    timing: Timing = Field(default_factory=Timing)
    assumptions: StrList = Field(default_factory=list)
    open_questions: List[OpenQuestion] = Field(default_factory=list)
    search_sources: List[WebSource] = Field(default_factory=list)
    updated_at: Optional[str] = None


# --------------------------------------------------------------------------- #
# 设备资源台账(全局,种子可维护;自有产线 + 外协厂商)
# --------------------------------------------------------------------------- #
class EquipmentResource(BaseModel):
    id: Optional[str] = Field(None, description="主键(留空由后端生成)")
    name: str = Field(..., description="设备名/型号")
    type: str = Field(..., description="设备类型,如 '烧结炉' / '环抛机'")
    owner: str = Field("自有", description="归属:自有 | 外协")
    vendor: Optional[str] = Field(None, description="外协厂商(owner=外协 时填)")
    capability: Optional[str] = Field(None, description="关键能力,如 '最高 1800℃/真空' / '面型 0.5µm'")
    capacity: Optional[str] = Field(None, description="产能,如 '800 件/月'")
    cost: Optional[str] = Field(None, description="外协加工费/单价(owner=外协 时)")
    lead_time: Optional[str] = Field(None, description="交期")
    note: Optional[str] = Field(None, description="备注")

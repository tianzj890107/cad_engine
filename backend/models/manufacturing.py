"""
制造工艺路径规划和 BOM 编制 IR —— 技术工艺第 4 步。

延续平台思路:Claude 只产出**结构化建议**(核心制造工艺路径 + 其它关键工艺评估 +
按工序分解的工艺 BOM),可联网检索工艺/设备信息作为依据;平台做确定性的 BOM 汇总/导出,
"确认"由人来点。读取项目 IR 与第 3 步材料计划(选定材料/金属化/粉末要求)作为输入。

注意: 为兼容工具调用承载的结构化输出,不使用数值约束(min/max),在 Python 侧校验。
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from .cost import WebSource
from .ir import OpenQuestion
from .material import Timing


# --------------------------------------------------------------------------- #
# ① 核心制造工艺路径
# --------------------------------------------------------------------------- #
class RouteStep(BaseModel):
    seq: int = Field(..., description="工序序号(从 1 递增)")
    name: str = Field(..., description="工序名称,如 '生坯流延成型' / '高温共烧' / '精密研磨'")
    category: Optional[str] = Field(None, description="工序类别,如 '成型/共烧/机加工/金属化/检测'")
    equipment: Optional[str] = Field(None, description="关键设备")
    params: Optional[str] = Field(None, description="关键工艺参数,如 '烧结温度 1600℃/保温 2h'")
    purpose: Optional[str] = Field(None, description="该工序目的/产出")
    quality: Optional[str] = Field(None, description="质量控制要点")
    critical: bool = Field(False, description="是否为关键工序")


# --------------------------------------------------------------------------- #
# ② 其它关键制造工艺评估
# --------------------------------------------------------------------------- #
class AdditionalProcess(BaseModel):
    name: str = Field(..., description="工艺名称,如 '共烧工艺' / '面型控制' / '激光打孔' / '丝网印刷金属化'")
    needed: bool = Field(False, description="是否需要")
    reason: Optional[str] = Field(None, description="需要/不需要的理由")
    notes: Optional[str] = Field(None, description="补充说明/风险")


# --------------------------------------------------------------------------- #
# ③ 工艺 BOM(按制造工序分解)
# --------------------------------------------------------------------------- #
class BomItem(BaseModel):
    ref: Optional[str] = Field(None, description="物料编号/序号")
    item: str = Field(..., description="物料/中间品/产出名称")
    category: Optional[str] = Field(None, description="类别:原材料 / 中间品 / 耗材辅料 / 工序产出")
    spec: Optional[str] = Field(None, description="规格/牌号/要求")
    quantity: Optional[float] = Field(None, description="数量/用量")
    unit: Optional[str] = Field(None, description="单位")
    from_step: Optional[str] = Field(None, description="来源/消耗于哪道工序(对应核心路径工序名或序号)")
    note: Optional[str] = Field(None, description="备注")


# --------------------------------------------------------------------------- #
# Claude 产出的建议
# --------------------------------------------------------------------------- #
class ManufacturingRecommendation(BaseModel):
    core_path: List[RouteStep] = Field(default_factory=list, description="核心制造工艺路径(有序)")
    path_summary: Optional[str] = Field(None, description="工艺路径总体思路")
    additional: List[AdditionalProcess] = Field(default_factory=list, description="其它关键工艺评估清单")
    bom: List[BomItem] = Field(default_factory=list, description="按工序分解的工艺 BOM")
    bom_summary: Optional[str] = Field(None, description="BOM 结构组成说明")
    assumptions: List[str] = Field(default_factory=list, description="关键假设")
    open_questions: List[OpenQuestion] = Field(default_factory=list, description="需澄清的问题")
    search_sources: List[WebSource] = Field(default_factory=list, description="联网检索来源(可追溯)")


# --------------------------------------------------------------------------- #
# 平台聚合 / 持久化的完整计划
# --------------------------------------------------------------------------- #
class RoutePlan(BaseModel):
    steps: List[RouteStep] = Field(default_factory=list)
    summary: Optional[str] = None
    confirmed: bool = False
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None


class BomPlan(BaseModel):
    items: List[BomItem] = Field(default_factory=list)
    summary: Optional[str] = None
    confirmed: bool = False
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None


class ManufacturingPlan(BaseModel):
    project_id: Optional[str] = None
    path: RoutePlan = Field(default_factory=RoutePlan)
    additional: List[AdditionalProcess] = Field(default_factory=list)
    bom: BomPlan = Field(default_factory=BomPlan)
    timing: Timing = Field(default_factory=Timing)
    assumptions: List[str] = Field(default_factory=list)
    open_questions: List[OpenQuestion] = Field(default_factory=list)
    search_sources: List[WebSource] = Field(default_factory=list)
    updated_at: Optional[str] = None

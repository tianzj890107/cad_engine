"""
清洗与洁净度管控方案制定 IR —— 技术工艺第 5 步。

延续平台思路:Claude 只产出**结构化方案**(依据图纸标注的洁净度等级,定制化学清洗工步 +
高纯水终漂洗流程 + 洁净度管控措施),可联网检索清洗剂/标准作为依据;"确认"由人来点。
输入为项目 IR(含图纸洁净度标注)与第 3 步材料计划(主体材料/金属化,决定可用清洗剂与禁忌)。

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
# 化学清洗工步
# --------------------------------------------------------------------------- #
class ChemStep(BaseModel):
    seq: int = Field(..., description="工步序号(从 1 递增)")
    name: str = Field(..., description="工步名称,如 '碱洗除油' / '酸洗去金属污染' / '超声清洗'")
    agent: Optional[str] = Field(None, description="清洗剂/酸洗液,如 '稀 HNO3' / 'HF:HNO3 混酸' / 'RCA SC-1'")
    concentration: Optional[str] = Field(None, description="浓度/配比")
    temperature: Optional[str] = Field(None, description="温度,如 '常温' / '60℃'")
    duration_min: Optional[float] = Field(None, description="时长(分钟)")
    target: Optional[str] = Field(None, description="去除对象,如 '表面金属离子/有机物/颗粒'")
    equipment: Optional[str] = Field(None, description="设备/槽体")
    notes: Optional[str] = Field(None, description="注意事项/材料禁忌")


# --------------------------------------------------------------------------- #
# 高纯水终漂洗流程
# --------------------------------------------------------------------------- #
class RinseStep(BaseModel):
    seq: int = Field(..., description="漂洗序号(从 1 递增)")
    medium: Optional[str] = Field(None, description="介质,如 '高纯水(18.2 MΩ·cm)' / 'DI 水'")
    method: Optional[str] = Field(None, description="方式,如 '溢流漂洗' / '超声/兆声' / '喷淋'")
    spec: Optional[str] = Field(None, description="终点指标,如 '出水电阻率 ≥18 MΩ·cm' / 'TOC < 50 ppb'")
    duration_min: Optional[float] = Field(None, description="时长(分钟)")
    notes: Optional[str] = Field(None, description="备注,如干燥方式(IPA 蒸气干燥/氮气吹干)")


# --------------------------------------------------------------------------- #
# 洁净度管控措施
# --------------------------------------------------------------------------- #
class ControlItem(BaseModel):
    item: str = Field(..., description="管控项,如 '洁净间环境等级' / '颗粒度检测' / '离子残留检测'")
    method: Optional[str] = Field(None, description="管控/检测方法")
    criteria: Optional[str] = Field(None, description="判定标准/限值")


# --------------------------------------------------------------------------- #
# Claude 产出的建议
# --------------------------------------------------------------------------- #
class CleaningRecommendation(BaseModel):
    cleanliness_grade: Optional[str] = Field(None, description="洁净度等级(优先取图纸标注,如 'ISO Class 5' / '表面金属离子 < 1E10 atoms/cm²')")
    grade_source: Optional[str] = Field(None, description="等级来源,如 '图纸标注' / '依据材料与应用推断'")
    grade_notes: Optional[str] = Field(None, description="等级说明/换算")
    chemical_steps: List[ChemStep] = Field(default_factory=list, description="化学清洗工步(有序)")
    rinse_steps: List[RinseStep] = Field(default_factory=list, description="高纯水终漂洗流程(有序)")
    controls: List[ControlItem] = Field(default_factory=list, description="洁净度管控措施")
    summary: Optional[str] = Field(None, description="方案总体思路")
    assumptions: StrList = Field(default_factory=list, description="关键假设")
    open_questions: List[OpenQuestion] = Field(default_factory=list, description="需澄清的问题(如图纸未标注洁净度等级)")
    search_sources: List[WebSource] = Field(default_factory=list, description="联网检索来源(可追溯)")


# --------------------------------------------------------------------------- #
# 平台聚合 / 持久化的完整计划
# --------------------------------------------------------------------------- #
class CleaningPlan(BaseModel):
    project_id: Optional[str] = None
    cleanliness_grade: Optional[str] = None
    grade_source: Optional[str] = None
    grade_notes: Optional[str] = None
    chemical_steps: List[ChemStep] = Field(default_factory=list)
    rinse_steps: List[RinseStep] = Field(default_factory=list)
    controls: List[ControlItem] = Field(default_factory=list)
    summary: Optional[str] = None
    confirmed: bool = False
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None
    timing: Timing = Field(default_factory=Timing)
    assumptions: StrList = Field(default_factory=list)
    open_questions: List[OpenQuestion] = Field(default_factory=list)
    search_sources: List[WebSource] = Field(default_factory=list)
    updated_at: Optional[str] = None

"""
组装与检测方案制定 IR —— 技术工艺第 6 步。

延续平台思路:Claude 只产出**结构化方案**(陶瓷板与金属基座的焊接/胶粘组装工艺 +
电性能/吸附力/气密性检测方案),可联网检索焊料/胶粘剂/测试标准作为依据;"确认"由人来点。
输入为项目 IR、第 3 步材料计划(主体材料/金属化)与第 4 步制造工艺。

注意: 为兼容工具调用承载的结构化输出,不使用数值约束(min/max),在 Python 侧校验。
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from .cost import WebSource
from .ir import OpenQuestion
from .material import Timing


# --------------------------------------------------------------------------- #
# ① 组装(陶瓷板 ↔ 金属基座 焊接/胶粘)工步
# --------------------------------------------------------------------------- #
class AssemblyStep(BaseModel):
    seq: int = Field(..., description="工步序号(从 1 递增)")
    name: str = Field(..., description="工步名称,如 '表面预处理' / '点胶' / '贴合定位' / '固化' / '钎焊'")
    method: Optional[str] = Field(None, description="连接方式,如 '导热胶胶粘' / '钎焊(AuSn)' / '共晶焊'")
    material: Optional[str] = Field(None, description="焊料/胶粘剂牌号,如 'AuSn80/20' / '导热环氧胶 XX'")
    glue_thickness: Optional[str] = Field(None, description="涂胶厚度/键合层厚度,如 '50±10 µm'")
    cure_temperature: Optional[str] = Field(None, description="固化/焊接温度,如 '150℃' / '回流峰值 320℃'")
    cure_time: Optional[str] = Field(None, description="固化/保温时间,如 '60 min'")
    pressure: Optional[str] = Field(None, description="压力/夹持,如 '0.1 MPa'")
    equipment: Optional[str] = Field(None, description="设备/工装")
    quality: Optional[str] = Field(None, description="质量控制要点,如 '空洞率<5%/对位精度'")
    notes: Optional[str] = Field(None, description="备注/材料禁忌")


# --------------------------------------------------------------------------- #
# ② 检测项(电性能 / 吸附力 / 气密性 等)
# --------------------------------------------------------------------------- #
class TestItem(BaseModel):
    name: str = Field(..., description="检测项,如 '漏电流' / '击穿电压' / '吸附力均匀性' / '氦气漏率'")
    category: Optional[str] = Field(None, description="类别,如 '电性能' / '机械/吸附' / '气密性'")
    method: Optional[str] = Field(None, description="测试方法/原理")
    equipment: Optional[str] = Field(None, description="测试设备/仪器")
    criteria: Optional[str] = Field(None, description="判定标准/限值,如 '漏电流<1µA@1kV' / '漏率<1E-9 Pa·m³/s'")
    sample: Optional[str] = Field(None, description="抽样方案,如 '全检' / 'AQL 1.0'")
    notes: Optional[str] = Field(None, description="备注")


# --------------------------------------------------------------------------- #
# Claude 产出的建议
# --------------------------------------------------------------------------- #
class AssemblyRecommendation(BaseModel):
    bonding_method: Optional[str] = Field(None, description="推荐的陶瓷-金属连接方式(焊接/胶粘等)")
    bonding_rationale: Optional[str] = Field(None, description="连接方式选择理由(CTE 匹配/导热/工艺成本等)")
    assembly_steps: List[AssemblyStep] = Field(default_factory=list, description="组装工艺工步(有序)")
    tests: List[TestItem] = Field(default_factory=list, description="检测方案(电性能/吸附力/气密性等)")
    summary: Optional[str] = Field(None, description="方案总体思路")
    assumptions: List[str] = Field(default_factory=list, description="关键假设")
    open_questions: List[OpenQuestion] = Field(default_factory=list, description="需澄清的问题")
    search_sources: List[WebSource] = Field(default_factory=list, description="联网检索来源(可追溯)")


# --------------------------------------------------------------------------- #
# 平台聚合 / 持久化的完整计划
# --------------------------------------------------------------------------- #
class AssemblySection(BaseModel):
    method: Optional[str] = None
    rationale: Optional[str] = None
    steps: List[AssemblyStep] = Field(default_factory=list)
    confirmed: bool = False
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None


class InspectionSection(BaseModel):
    tests: List[TestItem] = Field(default_factory=list)
    confirmed: bool = False
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None


class AssemblyPlan(BaseModel):
    project_id: Optional[str] = None
    assembly: AssemblySection = Field(default_factory=AssemblySection)
    inspection: InspectionSection = Field(default_factory=InspectionSection)
    summary: Optional[str] = None
    timing: Timing = Field(default_factory=Timing)
    assumptions: List[str] = Field(default_factory=list)
    open_questions: List[OpenQuestion] = Field(default_factory=list)
    search_sources: List[WebSource] = Field(default_factory=list)
    updated_at: Optional[str] = None

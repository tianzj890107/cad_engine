"""
定价方案制定 IR —— 报价流程第 2 步。

成本加成模式(原材料成本 + 生产加工成本 + 管理成本)为基础,叠加订单规模、技术难度与工艺
复杂度、客户战略价值、行业竞争态势、原材料价格联动等维度的调整;再经销售测算→财务审核确认。

Claude 给出加成率/管理费率与各维度调整建议(可联网),平台做**确定性**算价;审批留痕。
注意: 为兼容工具调用承载的结构化输出,不使用数值约束(min/max),在 Python 侧校验。
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from .cost import WebSource
from .ir import OpenQuestion
from .material import Timing


# --------------------------------------------------------------------------- #
# 调整维度(订单规模/技术难度/客户战略价值/竞争态势/原材料价格联动)
# --------------------------------------------------------------------------- #
class PricingFactor(BaseModel):
    factor: str = Field(..., description="维度,如 '订单规模' / '技术难度与工艺复杂度' / '客户战略价值' / '行业竞争态势' / '原材料价格联动'")
    assessment: Optional[str] = Field(None, description="对该维度的判断/取值")
    adjustment_pct: Optional[float] = Field(None, description="价格调整系数(%),正为上浮、负为下浮")
    rationale: Optional[str] = Field(None, description="调整理由")


# --------------------------------------------------------------------------- #
# 成本加成构成
# --------------------------------------------------------------------------- #
class CostPlus(BaseModel):
    material_cost: float = Field(0.0, description="原材料成本(元/件,取自成本测算)")
    production_cost: float = Field(0.0, description="生产加工成本(元/件,取自成本测算)")
    management_rate: float = Field(10.0, description="管理成本费率(%,基于材料+加工)")
    management_cost: float = Field(0.0, description="管理成本(元/件,平台按费率重算)")
    base_cost: float = Field(0.0, description="成本基数(元/件)= 材料+加工+管理(平台重算)")


# --------------------------------------------------------------------------- #
# 审批流(销售测算 → 财务审核)
# --------------------------------------------------------------------------- #
class PricingApproval(BaseModel):
    status: str = Field("draft", description="draft(草稿) | submitted(已提交财务) | approved(财务通过) | rejected(财务驳回)")
    sales_by: Optional[str] = None
    sales_at: Optional[str] = None
    finance_by: Optional[str] = None
    finance_at: Optional[str] = None
    finance_comment: Optional[str] = None


# --------------------------------------------------------------------------- #
# Claude 产出的建议(费率 + 各维度调整 + 思路)
# --------------------------------------------------------------------------- #
class PricingRecommendation(BaseModel):
    management_rate: Optional[float] = Field(None, description="建议管理成本费率(%)")
    markup_rate: Optional[float] = Field(None, description="建议成本加成率/利润率(%)")
    factors: List[PricingFactor] = Field(default_factory=list, description="各维度调整建议(覆盖 5 个维度)")
    summary: Optional[str] = Field(None, description="定价思路/说明")
    assumptions: List[str] = Field(default_factory=list, description="关键假设")
    open_questions: List[OpenQuestion] = Field(default_factory=list, description="需澄清的问题")
    search_sources: List[WebSource] = Field(default_factory=list, description="联网检索来源(可追溯)")


# --------------------------------------------------------------------------- #
# 平台聚合 / 持久化的完整定价方案
# --------------------------------------------------------------------------- #
class PricingPlan(BaseModel):
    project_id: Optional[str] = None
    currency: str = "CNY"
    costs: CostPlus = Field(default_factory=CostPlus)
    markup_rate: float = Field(20.0, description="成本加成率/目标利润率(%)")
    base_price: float = Field(0.0, description="加成后基准价(元/件)= 成本基数×(1+加成率)(平台重算)")
    factors: List[PricingFactor] = Field(default_factory=list)
    factor_multiplier: float = Field(1.0, description="各维度调整综合系数(平台重算)")
    suggested_price: float = Field(0.0, description="建议单价(元/件)= 基准价×综合系数(平台重算)")
    final_price: Optional[float] = Field(None, description="最终报价(元/件,可人工覆盖;默认=建议单价)")
    approval: PricingApproval = Field(default_factory=PricingApproval)
    summary: Optional[str] = None
    timing: Timing = Field(default_factory=Timing)
    assumptions: List[str] = Field(default_factory=list)
    open_questions: List[OpenQuestion] = Field(default_factory=list)
    search_sources: List[WebSource] = Field(default_factory=list)
    updated_at: Optional[str] = None

"""
商务及谈判策略 IR —— 报价流程第 3 步。

Claude 依据定价结果与项目背景产出**结构化**的商务条款(价格/交付/付款/质量保证/知识产权/
违约责任)与**分客户类型**(战略客户/新客户/海外客户)的差异化谈判策略与授权权限;"确认"由人来点。

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
# ① 商务条款
# --------------------------------------------------------------------------- #
class TermItem(BaseModel):
    category: str = Field(..., description="条款类别: 价格条款 / 交付条款 / 付款条款 / 质量保证条款 / 知识产权条款 / 违约责任")
    clause: Optional[str] = Field(None, description="标准条款表述/我方主张")
    bottom_line: Optional[str] = Field(None, description="底线(不可突破)")
    concession: Optional[str] = Field(None, description="可让步空间")
    note: Optional[str] = Field(None, description="备注/风险提示")


# --------------------------------------------------------------------------- #
# ② 分客户类型的差异化策略与授权
# --------------------------------------------------------------------------- #
class CustomerStrategy(BaseModel):
    customer_type: str = Field(..., description="客户类型: 战略客户 / 新客户 / 海外客户")
    strategy: Optional[str] = Field(None, description="差异化谈判策略")
    authorization: Optional[str] = Field(None, description="授权权限,如 '折扣上限 8%/超出需总监审批'")
    note: Optional[str] = Field(None, description="备注")


# --------------------------------------------------------------------------- #
# Claude 产出的建议
# --------------------------------------------------------------------------- #
class NegotiationRecommendation(BaseModel):
    terms: List[TermItem] = Field(default_factory=list, description="商务条款(覆盖六类)")
    strategies: List[CustomerStrategy] = Field(default_factory=list, description="分客户类型策略(覆盖战略/新/海外客户)")
    summary: Optional[str] = Field(None, description="商务谈判总体思路")
    assumptions: StrList = Field(default_factory=list, description="关键假设")
    open_questions: List[OpenQuestion] = Field(default_factory=list, description="需澄清的问题")
    search_sources: List[WebSource] = Field(default_factory=list, description="联网检索来源(可追溯)")


# --------------------------------------------------------------------------- #
# 平台聚合 / 持久化
# --------------------------------------------------------------------------- #
class NegotiationPlan(BaseModel):
    project_id: Optional[str] = None
    terms: List[TermItem] = Field(default_factory=list)
    strategies: List[CustomerStrategy] = Field(default_factory=list)
    summary: Optional[str] = None
    confirmed: bool = False
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None
    timing: Timing = Field(default_factory=Timing)
    assumptions: StrList = Field(default_factory=list)
    open_questions: List[OpenQuestion] = Field(default_factory=list)
    search_sources: List[WebSource] = Field(default_factory=list)
    updated_at: Optional[str] = None

"""
报价审批与决策 IR —— 报价流程第 6 步(收尾)。

分级审批:协商确定的价格与条款须通过内部审批后方可正式报价/签约。
  级别 1: 销售总监审核(常规订单、符合标准定价区间)
  级别 2: 销售总监 + 财务负责人联合审核(非常规定价:大幅让利/特殊付款条件)
  级别 3: 总经理/董事长审批(重大订单、战略客户、超权限定价)

Claude 依据定价/协商结果**研判应走的审批级别**与依据;平台**确定性**地按级别生成审批链
并按级流转(逐级通过/驳回);全程留痕。

注意: 为兼容工具调用承载的结构化输出,不使用数值约束(min/max),在 Python 侧校验。
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from .cost import WebSource
from .ir import OpenQuestion
from .material import Timing


# 级别 → 审批角色链(累进:高级别包含低级别)
LEVEL_ROLES = {
    1: ["销售总监"],
    2: ["销售总监", "财务负责人"],
    3: ["销售总监", "财务负责人", "总经理/董事长"],
}
LEVEL_DESC = {
    1: "常规订单,符合标准定价区间",
    2: "非常规定价(大幅让利 / 特殊付款条件)",
    3: "重大订单 / 战略客户 / 超权限定价",
}


class ApprovalClassification(BaseModel):
    order_scale: Optional[str] = Field(None, description="订单规模")
    pricing_standard: Optional[str] = Field(None, description="是否符合标准定价区间")
    concession: Optional[str] = Field(None, description="让利幅度")
    payment_special: Optional[str] = Field(None, description="是否特殊付款条件")
    customer_type: Optional[str] = Field(None, description="客户类型(是否战略客户)")
    over_authority: Optional[str] = Field(None, description="是否超出销售授权")
    notes: Optional[str] = Field(None, description="补充说明")


class ApprovalNode(BaseModel):
    seq: int = Field(..., description="审批顺序")
    role: str = Field(..., description="审批角色")
    status: str = Field("waiting", description="waiting(待前序) | pending(待审) | approved(通过) | rejected(驳回)")
    approver: Optional[str] = Field(None, description="审批人")
    at: Optional[str] = Field(None, description="审批时间")
    comment: Optional[str] = Field(None, description="审批意见")


# --------------------------------------------------------------------------- #
# Claude 产出的研判(级别 + 依据)
# --------------------------------------------------------------------------- #
class ApprovalRecommendation(BaseModel):
    level: int = Field(1, description="建议审批级别 1/2/3")
    level_reason: Optional[str] = Field(None, description="定级依据")
    classification: ApprovalClassification = Field(default_factory=ApprovalClassification, description="定级所依据的判断")
    summary: Optional[str] = Field(None, description="审批要点/风险提示")
    assumptions: List[str] = Field(default_factory=list, description="关键假设")
    open_questions: List[OpenQuestion] = Field(default_factory=list, description="需澄清的问题")
    search_sources: List[WebSource] = Field(default_factory=list, description="联网检索来源(可追溯)")


# --------------------------------------------------------------------------- #
# 平台聚合 / 持久化
# --------------------------------------------------------------------------- #
class QuoteApproval(BaseModel):
    project_id: Optional[str] = None
    level: int = Field(1, description="审批级别 1/2/3")
    level_reason: Optional[str] = None
    classification: ApprovalClassification = Field(default_factory=ApprovalClassification)
    chain: List[ApprovalNode] = Field(default_factory=list)
    status: str = Field("draft", description="draft(草稿) | in_review(审批中) | approved(通过,可签约) | rejected(驳回)")
    approvers: List[str] = Field(default_factory=list, description="已选择并发送审批流的审批人/角色(送审人)")
    sent_at: Optional[str] = Field(None, description="发送审批流的时间")
    decision_note: Optional[str] = None
    summary: Optional[str] = None
    timing: Timing = Field(default_factory=Timing)
    assumptions: List[str] = Field(default_factory=list)
    open_questions: List[OpenQuestion] = Field(default_factory=list)
    search_sources: List[WebSource] = Field(default_factory=list)
    updated_at: Optional[str] = None

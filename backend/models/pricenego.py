"""
价格协商及谈判 IR —— 报价流程第 4 步。

Claude 依据定价结果与商务条款产出**结构化**的初步报价单、阶梯价格、调价联动机制与特殊条款
建议(可联网检索行情);多轮价格协商记录由销售在协商过程中录入。"确认"由人来点。

注意: 为兼容工具调用承载的结构化输出,不使用数值约束(min/max),在 Python 侧校验。
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from .cost import WebSource
from .ir import OpenQuestion
from .material import Timing


# --------------------------------------------------------------------------- #
# ① 初步报价单
# --------------------------------------------------------------------------- #
class InitialQuote(BaseModel):
    price: Optional[float] = Field(None, description="报价单价(元/件)")
    currency: str = Field("CNY", description="币种")
    delivery_period: Optional[str] = Field(None, description="交付周期,如 '收到订单后 6 周'")
    payment_terms: Optional[str] = Field(None, description="付款条件,如 '预付 30%,发货前付清'")
    valid_until: Optional[str] = Field(None, description="报价有效期")
    note: Optional[str] = Field(None, description="备注")


# --------------------------------------------------------------------------- #
# ② 价格协商记录(多轮)
# --------------------------------------------------------------------------- #
class NegotiationRound(BaseModel):
    round_no: int = Field(..., description="轮次")
    date: Optional[str] = Field(None, description="日期")
    customer_feedback: Optional[str] = Field(None, description="客户反馈/诉求")
    our_response: Optional[str] = Field(None, description="我方应对")
    adjusted_price: Optional[float] = Field(None, description="调整后价格(元/件)")
    conditions: Optional[str] = Field(None, description="附加条件")


# --------------------------------------------------------------------------- #
# ③ 阶梯价格(批量折扣)
# --------------------------------------------------------------------------- #
class PriceTier(BaseModel):
    min_qty: Optional[float] = Field(None, description="数量下限(含)")
    max_qty: Optional[float] = Field(None, description="数量上限(空表示及以上)")
    unit_price: Optional[float] = Field(None, description="该档单价(元/件)")
    discount: Optional[str] = Field(None, description="对应折扣,如 '9.5 折'")
    note: Optional[str] = Field(None, description="备注")


# --------------------------------------------------------------------------- #
# ④ 调价联动机制(与原材料市场联动)
# --------------------------------------------------------------------------- #
class PriceLinkage(BaseModel):
    mechanism: Optional[str] = Field(None, description="联动机制说明")
    formula: Optional[str] = Field(None, description="调价公式,如 '新价=基准价×(1+材料涨幅×权重)'")
    trigger: Optional[str] = Field(None, description="触发条件,如 '主材价格波动 >±5%'")
    review_cycle: Optional[str] = Field(None, description="复核周期,如 '每季度'")


# --------------------------------------------------------------------------- #
# ⑤ 特殊(非价格)条款谈判
# --------------------------------------------------------------------------- #
class SpecialTerm(BaseModel):
    item: str = Field(..., description="条款,如 '交付周期' / '质保条款' / '技术支持'")
    our_position: Optional[str] = Field(None, description="我方立场")
    customer_request: Optional[str] = Field(None, description="客户诉求")
    agreed: Optional[str] = Field(None, description="达成情况/最终约定")
    note: Optional[str] = Field(None, description="备注")


# --------------------------------------------------------------------------- #
# Claude 产出的建议(协商轮次由人录入,不在此)
# --------------------------------------------------------------------------- #
class PriceNegoRecommendation(BaseModel):
    initial_quote: InitialQuote = Field(default_factory=InitialQuote, description="初步正式报价单")
    tiered_prices: List[PriceTier] = Field(default_factory=list, description="阶梯价格(批量折扣)")
    price_linkage: PriceLinkage = Field(default_factory=PriceLinkage, description="调价联动机制")
    special_terms: List[SpecialTerm] = Field(default_factory=list, description="特殊条款谈判建议")
    summary: Optional[str] = Field(None, description="协商策略/底线提示")
    assumptions: List[str] = Field(default_factory=list, description="关键假设")
    open_questions: List[OpenQuestion] = Field(default_factory=list, description="需澄清的问题")
    search_sources: List[WebSource] = Field(default_factory=list, description="联网检索来源(可追溯)")


# --------------------------------------------------------------------------- #
# 平台聚合 / 持久化
# --------------------------------------------------------------------------- #
class PriceNegotiation(BaseModel):
    project_id: Optional[str] = None
    initial_quote: InitialQuote = Field(default_factory=InitialQuote)
    rounds: List[NegotiationRound] = Field(default_factory=list)
    tiered_prices: List[PriceTier] = Field(default_factory=list)
    price_linkage: PriceLinkage = Field(default_factory=PriceLinkage)
    special_terms: List[SpecialTerm] = Field(default_factory=list)
    agreed_price: Optional[float] = Field(None, description="最终达成价格(元/件)")
    summary: Optional[str] = None
    confirmed: bool = False
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None
    timing: Timing = Field(default_factory=Timing)
    assumptions: List[str] = Field(default_factory=list)
    open_questions: List[OpenQuestion] = Field(default_factory=list)
    search_sources: List[WebSource] = Field(default_factory=list)
    updated_at: Optional[str] = None

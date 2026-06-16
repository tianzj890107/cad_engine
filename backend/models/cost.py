"""
成本分析 IR (Cost Analysis) —— 对单个零件做专业成本拆解。

与设计意图 IR / 工艺 IR 一脉相承: 大模型(Claude)只产出**结构化**成本明细
(分项 + 单价 + 金额 + 价格来源 + 置信度),并可**联网检索**当前材料/标准件行情作为
依据;平台做确定性的金额/合计重算与校验。从而可校验、可编辑、可追溯。

注意: 同样为兼容工具调用承载的结构化输出,不使用数值约束。
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from .ir import OpenQuestion


class CostCategory(str, Enum):
    material = "material"        # 材料费(毛坯/板材/棒料)
    machining = "machining"     # 机加工费(工时×费率)
    standard_part = "standard_part"  # 标准件/外购件
    heat_treat = "heat_treat"   # 热处理
    surface = "surface"         # 表面处理(电镀/喷涂/阳极)
    welding = "welding"         # 焊接
    assembly = "assembly"       # 装配
    inspection = "inspection"   # 检验
    tooling = "tooling"         # 工装/夹具摊销
    logistics = "logistics"     # 物流/包装
    overhead = "overhead"       # 管理费/制造费用
    profit = "profit"           # 利润
    other = "other"


class CostItem(BaseModel):
    category: CostCategory = Field(..., description="成本类别")
    name: str = Field(..., description="分项名称，如 'Q235 钢板' / 'CNC 铣削' / 'M8 螺栓'")
    basis: Optional[str] = Field(None, description="计算依据/说明，如 '0.66kg×4.5元/kg' 或 '15min×1.2元/min'")
    quantity: Optional[float] = Field(None, description="数量/用量")
    unit: Optional[str] = Field(None, description="单位，如 kg / 件 / min / 元")
    unit_price: Optional[float] = Field(None, description="单价(元)")
    amount: Optional[float] = Field(None, description="金额(元) = 数量×单价(平台会重算校验)")
    source: Optional[str] = Field(None, description="价格来源，如 '网络检索:钢材现货' / '经验估算'")
    confidence: float = Field(0.6, description="该分项置信度 0~1")


class PriceReference(BaseModel):
    item: str = Field(..., description="被引用的物料/服务，如 'Q235 热轧钢板'")
    price: str = Field(..., description="检索到的市场价格，如 '约 4200-4600 元/吨'")
    source: Optional[str] = Field(None, description="出处/网站/平台名称")
    url: Optional[str] = Field(None, description="该价格来源的网页链接(尽量给出可点击的具体网址)")
    date: Optional[str] = Field(None, description="价格时间，如 '2026-06'")


class WebSource(BaseModel):
    """平台自动从 web_search 结果中收集的检索来源(可追溯证据)。"""
    title: str = Field(..., description="网页标题")
    url: str = Field(..., description="网页链接")


class CostAnalysis(BaseModel):
    part_id: str = Field(..., description="对应零件编号")
    part_name: str = Field(..., description="零件名称")
    material: Optional[str] = Field(None, description="材料牌号")
    quantity: int = Field(1, description="核算批量(用于摊销工装/采购折扣判断)")
    currency: str = Field("CNY", description="币种")
    summary: str = Field(..., description="成本构成概述与定价思路")
    items: List[CostItem] = Field(default_factory=list, description="成本分项明细")
    unit_cost: Optional[float] = Field(None, description="单件成本估算(元,平台会按明细重算)")
    price_references: List[PriceReference] = Field(
        default_factory=list, description="联网检索到的市场价格引用(作为依据,可追溯;尽量带 url)"
    )
    search_sources: List[WebSource] = Field(
        default_factory=list, description="平台自动收集的 web_search 检索来源(标题+链接,可点击核查)"
    )
    assumptions: List[str] = Field(
        default_factory=list, description="估算所做的关键假设(批量/损耗/费率等)"
    )
    open_questions: List[OpenQuestion] = Field(
        default_factory=list, description="影响报价精度、需澄清的问题"
    )

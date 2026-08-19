"""
成本测算 IR —— 报价流程第 1 步(成本测算)。

延续平台思路:Claude 只产出**结构化**成本明细(材料/制造/技术附加三类 + 市场价格与供应
稳定性),可联网检索当前行情作为依据;平台做**确定性**的金额/合计重算。结合 BOM 与制造
工艺路径,细化到每道工序。"确认"由人来点。

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
# ① 材料成本(核心原材料单位用量成本)
# --------------------------------------------------------------------------- #
class MaterialCostItem(BaseModel):
    item: str = Field(..., description="原材料,如 '超高纯金属粉' / '氧化铝陶瓷粉' / 'Ag-Pd 浆料'")
    spec: Optional[str] = Field(None, description="规格/纯度/牌号")
    unit_usage: Optional[float] = Field(None, description="单位用量(每件)")
    unit: Optional[str] = Field(None, description="单位,如 g / kg")
    unit_price: Optional[float] = Field(None, description="单价(元/单位)")
    amount: Optional[float] = Field(None, description="单件材料成本(元)= 用量×单价(平台会重算)")
    market_price_source: Optional[str] = Field(None, description="市场价来源/出处")
    supply_stability: Optional[str] = Field(None, description="供应稳定性,如 '高/中/低' 或说明")
    note: Optional[str] = Field(None, description="备注(损耗率等)")


# --------------------------------------------------------------------------- #
# ② 制造成本(按关键工序:人工/设备折旧/能耗)
# --------------------------------------------------------------------------- #
class ProcessCostItem(BaseModel):
    process: str = Field(..., description="工序,如 '流延成型' / '高温共烧' / '精密研磨'")
    labor_cost: Optional[float] = Field(None, description="人工成本(元/件)")
    equipment_depreciation: Optional[float] = Field(None, description="设备折旧分摊(元/件)")
    energy_cost: Optional[float] = Field(None, description="能耗成本(元/件)")
    other: Optional[float] = Field(None, description="其它(辅料/工装等,元/件)")
    subtotal: Optional[float] = Field(None, description="工序小计(元/件,平台会重算)")
    basis: Optional[str] = Field(None, description="计算依据,如 '工时×费率 / 设备小时折旧'")
    note: Optional[str] = Field(None, description="备注")


# --------------------------------------------------------------------------- #
# ③ 技术附加成本(定制研发/客户验证/特殊检测 分摊)
# --------------------------------------------------------------------------- #
class TechCostItem(BaseModel):
    item: str = Field(..., description="项目,如 '定制化研发' / '客户验证' / '特殊检测(氦检)'")
    basis: Optional[str] = Field(None, description="分摊依据,如 '一次性投入÷批量'")
    amount: Optional[float] = Field(None, description="分摊到单件的成本(元)")
    note: Optional[str] = Field(None, description="备注")


# --------------------------------------------------------------------------- #
# ④ 物流仓储成本(运输/仓储/包装/装卸 分摊)
# --------------------------------------------------------------------------- #
class LogisticsCostItem(BaseModel):
    item: str = Field(..., description="项目,如 '运输' / '仓储' / '包装' / '装卸' / '保险'")
    basis: Optional[str] = Field(None, description="计算依据,如 '运距×费率 / 仓储周期×单价 / 按批量分摊'")
    amount: Optional[float] = Field(None, description="分摊到单件的成本(元)")
    note: Optional[str] = Field(None, description="备注")


# --------------------------------------------------------------------------- #
# 确定性合计
# --------------------------------------------------------------------------- #
class CostTotals(BaseModel):
    material_total: float = 0.0
    manufacturing_total: float = 0.0
    technical_total: float = 0.0
    logistics_total: float = 0.0
    grand_total: float = 0.0
    currency: str = "CNY"


# --------------------------------------------------------------------------- #
# Claude 产出的建议
# --------------------------------------------------------------------------- #
class CostEstimateRecommendation(BaseModel):
    material_costs: List[MaterialCostItem] = Field(default_factory=list, description="材料成本明细")
    manufacturing_costs: List[ProcessCostItem] = Field(default_factory=list, description="制造成本(按工序)")
    technical_costs: List[TechCostItem] = Field(default_factory=list, description="技术附加成本分摊")
    logistics_costs: List[LogisticsCostItem] = Field(default_factory=list, description="物流仓储成本(运输/仓储/包装/装卸)分摊")
    market_notes: Optional[str] = Field(None, description="当前原材料市场价格与供应稳定性概述(采购视角)")
    summary: Optional[str] = Field(None, description="成本构成与测算思路")
    assumptions: StrList = Field(default_factory=list, description="关键假设(批量/损耗/费率等)")
    open_questions: List[OpenQuestion] = Field(default_factory=list, description="需澄清的问题")
    search_sources: List[WebSource] = Field(default_factory=list, description="联网检索来源(可追溯)")


# --------------------------------------------------------------------------- #
# 平台聚合 / 持久化的完整测算
# --------------------------------------------------------------------------- #
class CostEstimate(BaseModel):
    project_id: Optional[str] = None
    currency: str = "CNY"
    material_costs: List[MaterialCostItem] = Field(default_factory=list)
    manufacturing_costs: List[ProcessCostItem] = Field(default_factory=list)
    technical_costs: List[TechCostItem] = Field(default_factory=list)
    logistics_costs: List[LogisticsCostItem] = Field(default_factory=list)
    market_notes: Optional[str] = None
    totals: CostTotals = Field(default_factory=CostTotals)
    summary: Optional[str] = None
    confirmed: bool = False
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None
    timing: Timing = Field(default_factory=Timing)
    assumptions: StrList = Field(default_factory=list)
    open_questions: List[OpenQuestion] = Field(default_factory=list)
    search_sources: List[WebSource] = Field(default_factory=list)
    updated_at: Optional[str] = None

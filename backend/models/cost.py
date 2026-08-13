"""
成本分析 IR (Cost Analysis) —— 对单个零件做专业成本拆解。

与设计意图 IR / 工艺 IR 一脉相承: 大模型(Claude)只产出**结构化**成本明细
(分项 + 单价 + 金额 + 价格来源 + 置信度),并可**联网检索**当前材料/标准件行情作为
依据;平台做确定性的金额/合计重算与校验。从而可校验、可编辑、可追溯。

注意: 同样为兼容工具调用承载的结构化输出,不使用数值约束。
"""
from __future__ import annotations

from enum import Enum
import re
from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

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


_COST_CATEGORY_ALIASES = {
    "material": ("材料", "原料", "毛坯", "板材"),
    "machining": ("机加工", "加工", "c nc", "cnc", "工时"),
    "standard_part": ("标准件", "外购", "采购件"),
    "heat_treat": ("热处理",),
    "surface": ("表面处理", "阳极", "电镀", "喷涂"),
    "welding": ("焊接",), "assembly": ("装配",), "inspection": ("检验", "检测"),
    "tooling": ("工装", "夹具", "刀具"), "logistics": ("物流", "包装", "运输"),
    "overhead": ("管理", "制造费用", "间接"), "profit": ("利润",),
}


def _cost_number(value: Any) -> Any:
    if value is None or isinstance(value, (int, float)):
        return value
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group()) if match else value


def _cost_category(value: Any) -> CostCategory:
    raw = str(value or "").strip().lower().replace("_", "")
    for item in CostCategory:
        if raw == item.value.replace("_", ""):
            return item
    for category, labels in _COST_CATEGORY_ALIASES.items():
        if any(label.replace(" ", "") in raw for label in labels):
            return CostCategory(category)
    return CostCategory.other


def _use_alias(data: dict, target: str, *aliases: str) -> None:
    if data.get(target) not in (None, "", [], {}):
        return
    for alias in aliases:
        if data.get(alias) not in (None, "", [], {}):
            data[target] = data[alias]
            return


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

    @model_validator(mode="before")
    @classmethod
    def normalize_model_output(cls, value):
        if isinstance(value, str):
            return {"category": _cost_category(value), "name": value}
        if not isinstance(value, dict):
            return value
        data = dict(value)
        _use_alias(data, "category", "type", "cost_type", "类别")
        _use_alias(data, "name", "item", "item_name", "项目", "名称")
        _use_alias(data, "basis", "calculation_basis", "说明", "计算依据")
        _use_alias(data, "quantity", "qty", "用量", "数量")
        _use_alias(data, "unit_price", "price", "单价")
        _use_alias(data, "amount", "total", "cost", "金额", "总价")
        _use_alias(data, "source", "price_source", "来源")
        _use_alias(data, "confidence", "confidence_score", "置信度")
        data["category"] = _cost_category(data.get("category") or data.get("name"))
        if not str(data.get("name") or "").strip():
            data["name"] = "待确认成本项"
        return data

    @field_validator("quantity", "unit_price", "amount", mode="before")
    @classmethod
    def normalize_numbers(cls, value):
        return _cost_number(value)

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value):
        if isinstance(value, str):
            levels = {"高": .85, "high": .85, "中": .6, "medium": .6, "低": .35, "low": .35}
            if value.strip().lower() in levels:
                return levels[value.strip().lower()]
            number = _cost_number(value)
            if isinstance(number, float):
                return number / 100 if "%" in value or number > 1 else number
        return value


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
    part_id: str = Field("", description="对应零件编号")
    part_name: str = Field("", description="零件名称")
    material: Optional[str] = Field(None, description="材料牌号")
    quantity: int = Field(1, description="核算批量(用于摊销工装/采购折扣判断)")
    currency: str = Field("CNY", description="币种")
    summary: str = Field("", description="成本构成概述与定价思路")
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

    @model_validator(mode="before")
    @classmethod
    def normalize_model_output(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        _use_alias(data, "part_id", "part_no", "id", "零件编号")
        _use_alias(data, "part_name", "name", "component_name", "零件名称")
        _use_alias(data, "summary", "overview", "cost_summary", "成本概述")
        _use_alias(data, "items", "cost_items", "breakdown", "明细")
        _use_alias(data, "unit_cost", "total_cost", "estimated_cost", "总成本")
        _use_alias(data, "price_references", "references", "价格依据")
        _use_alias(data, "open_questions", "questions", "clarifications", "待澄清项")
        if not isinstance(data.get("items"), list):
            data["items"] = [data["items"]] if data.get("items") else []
        if not isinstance(data.get("open_questions"), list):
            data["open_questions"] = [data["open_questions"]] if data.get("open_questions") else []
        return data

"""
定价方案制定服务(报价流程第 2 步)。

成本加成(原材料+生产加工+管理)为基础,叠加订单规模/技术难度/客户战略价值/竞争态势/
原材料价格联动等维度调整。Claude 给出费率与各维度调整建议(可联网),平台做**确定性**算价。
"""
from __future__ import annotations

from typing import List, Optional

from ..models.cost import WebSource
from ..models.ir import DesignIR
from ..models.pricing import PricingRecommendation
from . import llm_client as claude_client

FACTORS = ["订单规模", "技术难度与工艺复杂度", "客户战略价值", "行业竞争态势", "原材料价格联动"]

SYSTEM_PROMPT = f"""你是资深制造业定价/商务经理。给定该产品的**成本测算结果**
(原材料成本、生产加工成本)与项目背景,请基于「成本加成模式(原材料成本 + 生产加工成本 +
管理成本)」给出定价建议:

1. 建议**管理成本费率 management_rate**(%,基于材料+加工)与**成本加成率 markup_rate**
   (%,即目标利润率)。
2. 针对以下 5 个维度逐项给出调整建议(factors),每项含 assessment(判断)、adjustment_pct
   (价格调整系数%,正上浮/负下浮)、rationale(理由):{('、'.join(FACTORS))}。
   其中"行业竞争态势""原材料价格联动"可用 web_search 检索当前行情/竞品价位作为依据。
3. summary 给出整体定价思路。不要臆造精确数字,缺背景的写进 open_questions、假设写进 assumptions。
全程中文,调用工具输出结构化 PricingRecommendation(不要自行计算最终价格,价格由平台按费率与系数确定性计算)。"""


def _context(ir: Optional[DesignIR], costest: Optional[dict], note: str) -> str:
    lines: List[str] = []
    if ir and ir.device_name:
        lines.append(f"器件/设备名称: {ir.device_name}")
    ce = costest or {}
    totals = ce.get("totals") or {}
    lines.append("成本测算结果(单件,元):")
    lines.append(f"  - 原材料成本: {totals.get('material_total', 0)}")
    lines.append(f"  - 生产加工成本: {totals.get('manufacturing_total', 0)}")
    lines.append(f"  - 技术附加成本: {totals.get('technical_total', 0)}")
    lines.append(f"  - 成本测算总计: {totals.get('grand_total', 0)}")
    if ce.get("market_notes"):
        lines.append(f"原材料市场与供应: {ce.get('market_notes')}")
    if note and note.strip():
        lines.append(f"\n【用户补充说明(请优先采用,如订单批量/客户/目标价)】\n{note.strip()}")
    lines.append("\n请先联网检索行业竞争态势与原材料价格联动信息,再输出结构化 PricingRecommendation。")
    return "\n".join(lines)


def recommend(
    ir: Optional[DesignIR] = None, costest: Optional[dict] = None,
    note: str = "", web: bool = True,
) -> PricingRecommendation:
    use_web = web and claude_client.WEB_SEARCH_AVAILABLE
    content = [claude_client.text_block(_context(ir, costest, note)),
               claude_client.text_block(claude_client.web_search_notice(use_web))]
    extra_tools = claude_client.web_search_tools(use_web)
    sources: list = []
    rec = claude_client.run(
        SYSTEM_PROMPT, content, PricingRecommendation,
        extra_tools=extra_tools, sources_out=sources,
    )
    have = {s.url for s in rec.search_sources}
    for s in sources:
        if s.get("url") and s["url"] not in have:
            rec.search_sources.append(WebSource(title=s.get("title") or s["url"], url=s["url"]))
            have.add(s["url"])
    return rec


def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) else 0.0


def compute(plan: dict) -> dict:
    """确定性算价: 管理成本/成本基数/基准价/综合系数/建议单价。原地返回更新后的 plan。"""
    costs = plan.get("costs") or {}
    material = _num(costs.get("material_cost"))
    production = _num(costs.get("production_cost"))
    mgmt_rate = _num(costs.get("management_rate"))
    management = round((material + production) * mgmt_rate / 100.0, 2)
    base_cost = round(material + production + management, 2)
    costs["management_cost"] = management
    costs["base_cost"] = base_cost
    plan["costs"] = costs

    markup = _num(plan.get("markup_rate"))
    base_price = round(base_cost * (1 + markup / 100.0), 2)
    plan["base_price"] = base_price

    mult = 1.0
    for f in plan.get("factors", []) or []:
        mult *= (1 + _num(f.get("adjustment_pct")) / 100.0)
    mult = round(mult, 4)
    plan["factor_multiplier"] = mult
    suggested = round(base_price * mult, 2)
    plan["suggested_price"] = suggested
    if plan.get("final_price") in (None, 0, 0.0):
        plan["final_price"] = suggested
    return plan

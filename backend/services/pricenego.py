"""
价格协商及谈判服务(报价流程第 4 步)。

Claude 依据定价结果与商务条款产出初步报价单、阶梯价格、调价联动机制与特殊条款建议,
可联网检索原材料行情作为调价机制依据;协商轮次由销售录入。"确认"由人来点。
"""
from __future__ import annotations

from typing import List, Optional

from ..models.cost import WebSource
from ..models.ir import DesignIR
from ..models.pricenego import PriceNegoRecommendation
from . import llm_client as claude_client

SYSTEM_PROMPT = """你是资深销售/商务经理(高端电子陶瓷报价谈判)。给定定价结果与商务条款,
请产出**结构化**的价格协商材料:

1. **初步报价单(initial_quote)**:含报价单价(取定价的最终报价)、交付周期、付款条件、有效期。
2. **阶梯价格(tiered_prices)**:面向年度采购量较大客户的批量折扣阶梯(给数量区间、该档单价、折扣)。
3. **调价联动机制(price_linkage)**:建立价格与原材料市场联动的机制(机制说明、调价公式、触发条件、
   复核周期);可用 web_search 参考主材当前行情。
4. **特殊条款谈判(special_terms)**:交付周期、质保条款、技术支持等**非价格条款**的我方立场与可让步点。
5. summary 给出协商策略与价格底线提示。缺背景的写进 open_questions、假设写进 assumptions。
注意:不要编造客户的反馈(多轮协商记录由销售录入)。全程中文,调用工具输出结构化 PriceNegoRecommendation。"""


def _context(ir: Optional[DesignIR], pricing: Optional[dict], negotiation: Optional[dict], note: str) -> str:
    lines: List[str] = []
    if ir and ir.device_name:
        lines.append(f"器件/设备名称: {ir.device_name}")
    pp = pricing or {}
    if pp.get("final_price") or pp.get("suggested_price"):
        lines.append(f"定价: 建议单价 {pp.get('suggested_price', '?')}, 最终报价 {pp.get('final_price', '?')} 元/件")
    if pp.get("costs", {}).get("base_cost"):
        lines.append(f"成本基数: {pp['costs']['base_cost']} 元/件(协商价格底线参考)")
    ng = negotiation or {}
    if ng.get("terms"):
        seg = "; ".join(f"{t.get('category','')}:{t.get('clause','') or ''}" for t in ng.get("terms", [])[:6])
        lines.append(f"已定商务条款: {seg}")
    if not lines:
        lines.append("(暂无结构化输入,请基于通用经验给出报价与协商材料,并在 open_questions 标注缺失输入)")
    if note and note.strip():
        lines.append(f"\n【用户补充说明(请优先采用,如客户/年度量/目标价)】\n{note.strip()}")
    lines.append("\n请先联网检索主材当前行情(用于调价联动机制),再输出结构化 PriceNegoRecommendation。")
    return "\n".join(lines)


def recommend(
    ir: Optional[DesignIR] = None, pricing: Optional[dict] = None,
    negotiation: Optional[dict] = None, note: str = "", web: bool = True,
) -> PriceNegoRecommendation:
    content = [claude_client.text_block(_context(ir, pricing, negotiation, note))]
    extra_tools = [claude_client.WEB_SEARCH_TOOL] if web else None
    sources: list = []
    rec = claude_client.run(
        SYSTEM_PROMPT, content, PriceNegoRecommendation,
        extra_tools=extra_tools, sources_out=sources,
    )
    have = {s.url for s in rec.search_sources}
    for s in sources:
        if s.get("url") and s["url"] not in have:
            rec.search_sources.append(WebSource(title=s.get("title") or s["url"], url=s["url"]))
            have.add(s["url"])
    return rec

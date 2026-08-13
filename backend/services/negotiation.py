"""
商务及谈判策略服务(报价流程第 3 步)。

Claude 依据定价结果与项目背景产出结构化商务条款与分客户类型的差异化谈判策略/授权;
可联网检索行业惯例(尤其海外条款)作为参考;"确认"由人来点。
"""
from __future__ import annotations

from typing import List, Optional

from ..models.cost import WebSource
from ..models.ir import DesignIR
from ..models.negotiation import NegotiationRecommendation
from . import llm_client as claude_client

TERMS = ["价格条款", "交付条款", "付款条款", "质量保证条款", "知识产权条款", "违约责任"]
CUSTOMERS = ["战略客户", "新客户", "海外客户"]

SYSTEM_PROMPT = f"""你是资深制造业商务/销售总监。给定该产品的定价结果与项目背景,
请制定**结构化**的商务及谈判策略:

1. **商务条款(terms)**:逐项给出以下六类条款的我方主张、底线(bottom_line)与可让步空间
   (concession):{('、'.join(TERMS))}。要具体可执行(如付款 3-3-4、交期、质保期、IP 归属、违约金比例等)。
2. **分客户类型策略(strategies)**:针对 {('、'.join(CUSTOMERS))} 分别给出差异化谈判策略(strategy)
   与销售授权权限(authorization,如折扣上限/审批层级);海外客户需考虑汇率、关税、国际条款与合规。
3. summary 给出总体商务思路。缺背景的写进 open_questions、假设写进 assumptions。
全程中文,调用工具输出结构化 NegotiationRecommendation。"""


def _context(ir: Optional[DesignIR], pricing: Optional[dict], note: str) -> str:
    lines: List[str] = []
    if ir and ir.device_name:
        lines.append(f"器件/设备名称: {ir.device_name}")
    pp = pricing or {}
    if pp.get("final_price") or pp.get("suggested_price"):
        lines.append(f"定价结果(单件,元): 建议单价 {pp.get('suggested_price', '?')}, "
                     f"最终报价 {pp.get('final_price', '?')}")
    if pp.get("summary"):
        lines.append(f"定价思路: {pp.get('summary')}")
    if not lines:
        lines.append("(暂无结构化输入,请基于通用商务经验给出策略,并在 open_questions 标注缺失输入)")
    if note and note.strip():
        lines.append(f"\n【用户补充说明(请优先采用,如客户名称/类型/已知诉求)】\n{note.strip()}")
    return "\n".join(lines)


def recommend(
    ir: Optional[DesignIR] = None, pricing: Optional[dict] = None,
    note: str = "", web: bool = False,
) -> NegotiationRecommendation:
    content = [claude_client.text_block(_context(ir, pricing, note))]
    use_web = web and claude_client.WEB_SEARCH_AVAILABLE
    extra_tools = claude_client.web_search_tools(use_web)
    content.append(claude_client.text_block(claude_client.web_search_notice(use_web)))
    sources: list = []
    rec = claude_client.run(
        SYSTEM_PROMPT, content, NegotiationRecommendation,
        extra_tools=extra_tools, sources_out=sources,
    )
    have = {s.url for s in rec.search_sources}
    for s in sources:
        if s.get("url") and s["url"] not in have:
            rec.search_sources.append(WebSource(title=s.get("title") or s["url"], url=s["url"]))
            have.add(s["url"])
    return rec

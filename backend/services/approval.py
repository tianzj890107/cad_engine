"""
报价审批与决策服务(报价流程第 6 步)。

Claude 依据定价/协商结果研判应走的审批级别(1 销售总监 / 2 +财务联合 / 3 总经理董事长)
与依据;平台据级别确定性生成审批链并按级流转。
"""
from __future__ import annotations

from typing import List, Optional

from ..models.approval import ApprovalNode, ApprovalRecommendation, LEVEL_ROLES
from ..models.cost import WebSource
from ..models.ir import DesignIR
from . import claude_client

SYSTEM_PROMPT = """你是高端电子陶瓷企业的报价管控负责人。给定该报价的定价方案与价格协商结果,
请**研判应走的内部审批级别**(分级审批):
  级别 1 — 销售总监审核:常规订单,价格符合标准定价区间;
  级别 2 — 销售总监 + 财务负责人联合审核:非常规定价(大幅让利、特殊付款条件);
  级别 3 — 总经理/董事长审批:重大订单、战略客户、超权限定价。

请输出 level(1/2/3)、level_reason(定级依据),并在 classification 中给出判断:订单规模、是否符合
标准定价区间、让利幅度、是否特殊付款条件、客户类型(是否战略客户)、是否超出销售授权。
取"就高不就低"原则:命中任一更高级别条件即升级。summary 给审批要点/风险。
缺背景的写进 open_questions、假设写进 assumptions。全程中文,调用工具输出结构化 ApprovalRecommendation。"""


def _context(ir: Optional[DesignIR], pricing: Optional[dict], pricenego: Optional[dict], note: str) -> str:
    lines: List[str] = []
    if ir and ir.device_name:
        lines.append(f"器件/设备名称: {ir.device_name}")
    pp = pricing or {}
    costs = pp.get("costs") or {}
    if costs.get("base_cost"):
        lines.append(f"成本基数: {costs.get('base_cost')} 元/件;加成后基准价: {pp.get('base_price','?')} 元/件")
    if pp.get("suggested_price") or pp.get("final_price"):
        lines.append(f"定价: 建议单价 {pp.get('suggested_price','?')}, 最终报价 {pp.get('final_price','?')} 元/件")
    pn = pricenego or {}
    if pn.get("agreed_price"):
        lines.append(f"协商达成价: {pn.get('agreed_price')} 元/件")
    iq = pn.get("initial_quote") or {}
    if iq.get("payment_terms"):
        lines.append(f"付款条件: {iq.get('payment_terms')}")
    if pn.get("rounds"):
        lines.append(f"已进行 {len(pn.get('rounds'))} 轮价格协商")
    if not lines:
        lines.append("(暂无结构化输入,请基于补充说明研判,并在 open_questions 标注缺失输入)")
    if note and note.strip():
        lines.append(f"\n【用户补充说明(请优先采用,如订单金额/客户是否战略/折扣幅度)】\n{note.strip()}")
    return "\n".join(lines)


def recommend(
    ir: Optional[DesignIR] = None, pricing: Optional[dict] = None,
    pricenego: Optional[dict] = None, note: str = "", web: bool = False,
) -> ApprovalRecommendation:
    content = [claude_client.text_block(_context(ir, pricing, pricenego, note))]
    extra_tools = [claude_client.WEB_SEARCH_TOOL] if web else None
    sources: list = []
    rec = claude_client.run(
        SYSTEM_PROMPT, content, ApprovalRecommendation,
        extra_tools=extra_tools, sources_out=sources,
    )
    if rec.level not in (1, 2, 3):
        rec.level = 1
    have = {s.url for s in rec.search_sources}
    for s in sources:
        if s.get("url") and s["url"] not in have:
            rec.search_sources.append(WebSource(title=s.get("title") or s["url"], url=s["url"]))
            have.add(s["url"])
    return rec


def build_chain(level: int) -> List[ApprovalNode]:
    """据级别确定性生成审批链(累进角色),全部置 waiting。"""
    roles = LEVEL_ROLES.get(level, LEVEL_ROLES[1])
    return [ApprovalNode(seq=i + 1, role=r, status="waiting") for i, r in enumerate(roles)]

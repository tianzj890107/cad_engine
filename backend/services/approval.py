"""
报价审批与决策服务(报价流程第 6 步)。

审批级别由公开的本地通用矩阵决定；模型只归纳风险与待澄清信息。
"""
from __future__ import annotations

from typing import List, Optional

from ..models.approval import ApprovalNode, ApprovalRecommendation, LEVEL_ROLES
from ..models.cost import WebSource
from ..models.ir import DesignIR
from . import llm_client as claude_client

SYSTEM_PROMPT = """你是制造企业的报价管控助手。平台已用确定性矩阵给出最低审批级别，
你只负责归纳风险、分类和待澄清信息，不得降低平台级别。分级审批:
  级别 1 — 销售总监审核:常规订单,价格符合标准定价区间;
  级别 2 — 销售总监 + 财务负责人联合审核:非常规定价(大幅让利、特殊付款条件);
  级别 3 — 总经理/董事长审批:重大订单、战略客户、超权限定价。

请输出 level(1/2/3)、level_reason(定级依据),并在 classification 中给出判断:订单规模、是否符合
标准定价区间、让利幅度、是否特殊付款条件、客户类型(是否战略客户)、是否超出销售授权。
取"就高不就低"原则:命中任一更高级别条件即升级。summary 给审批要点/风险。
缺背景的写进 open_questions、假设写进 assumptions。全程中文,调用工具输出结构化 ApprovalRecommendation。"""


def determine_level(pricing: Optional[dict], pricenego: Optional[dict], note: str = "") -> tuple[int, str]:
    """企业授权表缺失时采用保守、透明的通用矩阵。"""
    pp, pn = pricing or {}, pricenego or {}
    final_price = float(pn.get("agreed_price") or pp.get("final_price") or pp.get("suggested_price") or 0)
    suggested = float(pp.get("suggested_price") or 0)
    base_cost = float((pp.get("costs") or {}).get("base_cost") or 0)
    quantity = float((pn.get("initial_quote") or {}).get("quantity") or 1)
    amount = final_price * quantity
    discount = ((suggested - final_price) / suggested * 100) if suggested > 0 and final_price > 0 else 0
    margin = ((final_price - base_cost) / final_price * 100) if final_price > 0 and base_cost > 0 else None
    corpus = f"{note} {(pn.get('initial_quote') or {}).get('payment_terms', '')}"
    strategic = any(word in corpus for word in ("战略客户", "重大项目", "董事长", "总经理特批"))
    special_payment = any(word in corpus for word in ("账期", "分期", "预付款低于", "特殊付款"))
    if strategic or amount >= 1_000_000 or discount >= 15 or (margin is not None and margin < 5):
        return 3, "通用保守矩阵命中重大金额、战略客户、大幅让利或极低毛利条件"
    if special_payment or amount >= 200_000 or discount >= 5 or margin is None:
        return 2, "通用矩阵命中较大金额、非常规付款、让利或毛利信息不完整条件"
    return 1, "通用矩阵判定为常规金额、无明显非常规条款且价格风险可控"


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
    level, reason = determine_level(pricing, pricenego, note)
    content = [claude_client.text_block(
        _context(ir, pricing, pricenego, note)
        + f"\n\n【平台确定性结论】level={level}；{reason}。不得降低。"
    )]
    use_web = web and claude_client.WEB_SEARCH_AVAILABLE
    extra_tools = claude_client.web_search_tools(use_web)
    sources: list = []
    rec = claude_client.run(
        SYSTEM_PROMPT, content, ApprovalRecommendation,
        extra_tools=extra_tools, sources_out=sources,
    )
    rec.level = level
    rec.level_reason = reason
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

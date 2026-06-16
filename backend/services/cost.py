"""
成本分析服务: 对单个零件做专业成本拆解,允许 Claude 联网检索当前行情作为依据。

延续平台思路 —— Claude 只产出**结构化**成本明细(CostAnalysis)并附价格来源,
平台做确定性的金额/合计重算与校验(compute)。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from ..models.cost import CostAnalysis, WebSource
from ..models.ir import DesignIR, Part
from . import claude_client

SYSTEM_PROMPT = """你是资深机械零件成本工程师(应成本/报价)。给定一个零件的结构化设计意图
(特征、材料、尺寸、公差、工艺角色)与可选的几何属性(体积/质量),请做**专业的成本拆解**。

要求:
1. **联网检索当前行情**:用 web_search 工具查当前的原材料价格(如 Q235/45 钢、6061 铝、304
   不锈钢的现货价 元/吨 或 元/kg)、外购标准件价格、表面/热处理与机加工费率等,作为估算依据。
   把检索到的关键价格写进 price_references,**每条尽量给出 source(出处网站) 与 url(可点击的
   具体网页链接) 与 date(价格时间)** —— 让依据可核查、可追溯。分项 items 的 source 字段也尽量
   写明出处。没有检索到链接的价格,请明确标注为"估算"并降低置信度,不要编造网址。
2. 按成本类别拆分 items:材料费、机加工费(工时×费率)、标准件/外购、热处理、表面处理、
   焊接、装配、检验、工装摊销、物流、管理费、利润等(按零件实际涉及的项给出,不要硬凑)。
3. 每个分项尽量给出: 计算依据 basis、数量 quantity、单位 unit、单价 unit_price、金额 amount、
   价格来源 source、置信度 confidence。材料费应基于零件体积/质量×材料密度×单价估算并计损耗。
4. 给出 summary(成本构成与定价思路)、unit_cost(单件成本估算)、assumptions(批量/损耗率/
   费率等关键假设)。凡缺尺寸/材料/批量而影响报价精度的,写进 open_questions 并降低相应置信度。
5. 金额单位统一为人民币元(currency=CNY)。**不要臆造**精确单价 —— 没检索到就标注为估算并降置信度。
6. 全程用中文填写各字段。"""


def _part_prompt(part: Part, overall: Optional[DesignIR], geom: Optional[dict], quantity: int) -> str:
    lines = [f"零件编号: {part.part_id}", f"零件名称: {part.name}", f"核算批量: {quantity} 件"]
    if part.role:
        lines.append(f"装配角色/功能: {part.role}")
    if part.material:
        m = part.material
        lines.append(f"材料: {m.spec}" + (f" (密度 {m.density} g/cm³)" if m.density else ""))
    if part.tolerance_general:
        lines.append(f"一般公差: {part.tolerance_general}")

    if part.features:
        lines.append("特征(几何构成):")
        for i, f in enumerate(part.features, 1):
            dims = ", ".join(
                f"{k}={v}" for k, v in f.model_dump().items()
                if v is not None and k not in ("type", "purpose")
            )
            purpose = f" — {f.purpose}" if f.purpose else ""
            lines.append(f"  {i}. {f.type.value}: {dims}{purpose}")

    if geom:
        g = []
        if geom.get("bbox"):
            g.append("包围盒 " + "×".join(str(x) for x in geom["bbox"]) + " mm")
        if geom.get("volume_mm3"):
            g.append(f"体积 {geom['volume_mm3']} mm³")
        if geom.get("mass_g"):
            g.append(f"质量 {geom['mass_g']} g")
        if g:
            lines.append("几何属性(CAD 内核实测): " + "; ".join(g))

    if overall and overall.device_name:
        lines.append(f"\n所属设备: {overall.device_name}")

    lines.append("\n请先联网检索相关材料/外购/加工的当前行情,再做成本拆解,"
                 "调用工具输出结构化 CostAnalysis。")
    return "\n".join(lines)


def analyze_cost(
    part: Part, overall: Optional[DesignIR] = None, geom: Optional[dict] = None,
    quantity: int = 1, web: bool = True, note: str = "",
    attachments: Optional[List[Tuple[str, bytes]]] = None,
) -> CostAnalysis:
    prompt = _part_prompt(part, overall, geom, quantity)
    content = [claude_client.text_block(prompt)]
    if note and note.strip():
        content.append(claude_client.text_block(f"【用户补充说明(请优先采用)】\n{note.strip()}"))
    content.extend(claude_client.attachment_blocks(attachments))

    extra_tools = [claude_client.WEB_SEARCH_TOOL] if web else None
    sources: list = []
    analysis = claude_client.run(
        SYSTEM_PROMPT, content, CostAnalysis, extra_tools=extra_tools, sources_out=sources,
    )
    # 确定性归一: 锚定 part_id/name/批量;并把平台收集的检索来源合并进 search_sources(去重)
    analysis.part_id = part.part_id
    if not analysis.part_name:
        analysis.part_name = part.name
    if not analysis.material and part.material:
        analysis.material = part.material.spec
    analysis.quantity = quantity

    have = {s.url for s in analysis.search_sources}
    for s in sources:
        if s.get("url") and s["url"] not in have:
            analysis.search_sources.append(WebSource(title=s.get("title") or s["url"], url=s["url"]))
            have.add(s["url"])
    return analysis


def compute(analysis: dict) -> dict:
    """确定性派生量: 按明细重算金额与合计、分类汇总、勾稽校验告警。"""
    items = analysis.get("items", []) or []
    warnings: list[str] = []
    total = 0.0
    by_category: dict[str, float] = {}

    for it in items:
        amt = it.get("amount")
        qty = it.get("quantity")
        up = it.get("unit_price")
        # 数量×单价 与 金额 勾稽
        if isinstance(qty, (int, float)) and isinstance(up, (int, float)):
            calc = round(qty * up, 2)
            if amt is None:
                amt = calc
            elif abs(calc - amt) > max(0.01, abs(amt) * 0.02):
                warnings.append(
                    f"分项「{it.get('name')}」金额 {amt} 与 数量×单价 ({calc}) 不符")
        if isinstance(amt, (int, float)):
            total += amt
            cat = it.get("category", "other")
            by_category[cat] = round(by_category.get(cat, 0.0) + amt, 2)

    computed_total = round(total, 2) if items else None
    stated = analysis.get("unit_cost")
    if isinstance(stated, (int, float)) and computed_total is not None:
        if abs(stated - computed_total) > max(0.5, abs(computed_total) * 0.05):
            warnings.append(f"声明单件成本 {stated} 与 明细合计 {computed_total} 不一致")

    return {
        "item_count": len(items),
        "computed_total": computed_total,
        "by_category": by_category,
        "currency": analysis.get("currency", "CNY"),
        "warnings": warnings,
    }

"""
成本测算服务(报价流程第 1 步)。

Claude 依据 IR、材料计划与制造工艺(工序+BOM)产出**结构化**成本明细(材料/制造/技术附加),
并联网检索当前原材料市场价格与供应稳定性作为依据(回填可追溯来源);平台做**确定性**的
金额/合计重算。结合 BOM 与工艺路线,细化到每道工序。
"""
from __future__ import annotations

import csv
import io
from typing import List, Optional

from ..models.cost import WebSource
from ..models.costest import CostEstimateRecommendation, CostTotals
from ..models.ir import DesignIR
from . import llm_client as claude_client

SYSTEM_PROMPT = """你是通用制造业成本/报价工程师。给定产品设计意图、材料方案、
制造工艺路径与 BOM,请做**专业的成本测算**,输出结构化明细:

1. **材料成本(material_costs)**:按项目 BOM 核算核心原材料的**单位用量成本**,
   给出规格、单位用量、单价、金额,并**联网检索当前市场价格**写明来源(market_price_source)
   与**供应稳定性**(supply_stability:高/中/低及原因)。金额按 用量×单价 估算并计损耗。
2. **制造成本(manufacturing_costs)**:对项目实际制造路径中的**关键工序逐道**核算
   人工(labor_cost)、设备折旧(equipment_depreciation)、能耗(energy_cost)及其它,给出工序小计与依据。
3. **技术附加成本(technical_costs)**:定制化研发、客户验证、特殊检测项目的**成本分摊**
   (basis 写明分摊依据,如一次性投入÷批量)。
4. **物流仓储成本(logistics_costs)**:核算运输、仓储、包装、装卸、保险等费用的**单件分摊**
   (basis 写明依据,如运距×费率 / 仓储周期×单价 / 按批量分摊);可结合交付方式与批量估算。
5. **市场与供应(market_notes)**:从采购视角概述当前原材料市场价格水平与供应稳定性。
6. 全部金额以人民币元(CNY)、单件口径;**不要臆造**精确单价,没检索到就标注估算并降低置信度,
   缺尺寸/批量/费率的写进 open_questions、关键假设写进 assumptions。
全程中文,调用工具输出结构化 CostEstimateRecommendation。"""


def _context(ir: Optional[DesignIR], material_plan: Optional[dict],
             manufacturing_plan: Optional[dict], note: str) -> str:
    lines: List[str] = []
    if ir and ir.device_name:
        lines.append(f"器件/设备名称: {ir.device_name}")

    mp = material_plan or {}
    body = mp.get("body") or {}
    if body.get("selected"):
        lines.append(f"主体材料: {body.get('selected')}")
    supply = mp.get("supply") or {}
    if supply.get("requirements"):
        rq = "; ".join(
            f"{r.get('material','')} 纯度≥{r.get('purity_pct_min','?')}%" for r in supply.get("requirements", [])
        )
        lines.append(f"粉末要求: {rq}")

    man = manufacturing_plan or {}
    path = (man.get("path") or {}).get("steps") or []
    if path:
        lines.append("制造工艺路径(逐工序核算制造成本):")
        for s in path[:15]:
            lines.append(f"  - {s.get('seq','')} {s.get('name','')}"
                         + (f"(设备: {s.get('equipment')})" if s.get("equipment") else ""))
    bom = (man.get("bom") or {}).get("items") or []
    if bom:
        lines.append("工艺 BOM(材料成本依据):")
        for b in bom[:20]:
            lines.append(f"  - {b.get('item','')} {b.get('spec','') or ''} "
                         f"{b.get('quantity','') or ''}{b.get('unit','') or ''}")

    if not lines:
        lines.append("(暂无结构化输入,请基于补充说明与通用经验测算,并在 open_questions 标注缺失输入)")
    if note and note.strip():
        lines.append(f"\n【用户补充说明(请优先采用,如批量/目标成本)】\n{note.strip()}")
    lines.append("\n请先联网检索相关原材料当前行情与供应情况,再输出结构化 CostEstimateRecommendation。")
    return "\n".join(lines)


def recommend(
    ir: Optional[DesignIR] = None, material_plan: Optional[dict] = None,
    manufacturing_plan: Optional[dict] = None, note: str = "", web: bool = True,
) -> CostEstimateRecommendation:
    use_web = web and claude_client.WEB_SEARCH_AVAILABLE
    content = [claude_client.text_block(_context(ir, material_plan, manufacturing_plan, note)),
               claude_client.text_block(claude_client.web_search_notice(use_web))]
    extra_tools = claude_client.web_search_tools(use_web)
    sources: list = []
    rec = claude_client.run(
        SYSTEM_PROMPT, content, CostEstimateRecommendation,
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


def compute(estimate: dict) -> CostTotals:
    """确定性合计: 材料(用量×单价 或 amount)、制造(工序各项之和 或 subtotal)、技术分摊。"""
    mat = 0.0
    for it in estimate.get("material_costs", []) or []:
        amt = it.get("amount")
        if not isinstance(amt, (int, float)):
            amt = _num(it.get("unit_usage")) * _num(it.get("unit_price"))
        mat += _num(amt)

    man = 0.0
    for it in estimate.get("manufacturing_costs", []) or []:
        sub = it.get("subtotal")
        if not isinstance(sub, (int, float)):
            sub = (_num(it.get("labor_cost")) + _num(it.get("equipment_depreciation"))
                   + _num(it.get("energy_cost")) + _num(it.get("other")))
        man += _num(sub)

    tech = sum(_num(it.get("amount")) for it in estimate.get("technical_costs", []) or [])
    logi = sum(_num(it.get("amount")) for it in estimate.get("logistics_costs", []) or [])

    return CostTotals(
        material_total=round(mat, 2), manufacturing_total=round(man, 2),
        technical_total=round(tech, 2), logistics_total=round(logi, 2),
        grand_total=round(mat + man + tech + logi, 2),
        currency=estimate.get("currency", "CNY"),
    )


def to_csv(estimate: dict) -> str:
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf)
    w.writerow(["类别", "项目", "规格/工序", "用量/依据", "单价/分项", "金额(元)", "市场价来源/供应稳定性"])
    for it in estimate.get("material_costs", []) or []:
        w.writerow(["材料", it.get("item", ""), it.get("spec", ""),
                    f"{it.get('unit_usage','')}{it.get('unit','') or ''}", it.get("unit_price", ""),
                    it.get("amount", ""), f"{it.get('market_price_source','') or ''} / {it.get('supply_stability','') or ''}"])
    for it in estimate.get("manufacturing_costs", []) or []:
        w.writerow(["制造", it.get("process", ""), "",
                    f"人工{it.get('labor_cost','')}/折旧{it.get('equipment_depreciation','')}/能耗{it.get('energy_cost','')}",
                    "", it.get("subtotal", ""), it.get("basis", "")])
    for it in estimate.get("technical_costs", []) or []:
        w.writerow(["技术附加", it.get("item", ""), "", it.get("basis", ""), "", it.get("amount", ""), ""])
    for it in estimate.get("logistics_costs", []) or []:
        w.writerow(["物流仓储", it.get("item", ""), "", it.get("basis", ""), "", it.get("amount", ""), it.get("note", "")])
    t = compute(estimate)
    w.writerow([])
    w.writerow(["合计", "材料", "", "", "", t.material_total, ""])
    w.writerow(["合计", "制造", "", "", "", t.manufacturing_total, ""])
    w.writerow(["合计", "技术附加", "", "", "", t.technical_total, ""])
    w.writerow(["合计", "物流仓储", "", "", "", t.logistics_total, ""])
    w.writerow(["合计", "总计", "", "", "", t.grand_total, t.currency])
    return buf.getvalue()

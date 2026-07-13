"""
产线匹配与产能评估服务(技术工艺第 7 步)。

Claude 依据制造工艺所需设备与**真实设备台账**(自有产线 + 外协厂商),评估自有产线能否满足
图纸要求、产能是否足够,并在满足且成本合适时给出外协安排建议;可联网检索设备能力参数作为
依据(回填可追溯来源);"确认"由人来点。设备匹配以维护的设备目录为准(可追溯)。
"""
from __future__ import annotations

from typing import List, Optional

from ..models.cost import WebSource
from ..models.ir import DesignIR
from ..models.production import ProductionRecommendation
from . import claude_client

SYSTEM_PROMPT = """你是资深生产工艺与产能规划工程师。给定器件设计意图、已规划的制造工艺路径,
以及**当前可用的设备台账**(自有产线设备 + 外协厂商设备),请完成「产线匹配与产能评估」:

1. **设备需求(requirements)**:从制造工艺工序推导各工序所需设备类型与关键要求
   (如高温共烧→烧结炉(最高温度/气氛要求),精密研磨→环抛机(面型精度))。
2. **自有产线匹配(inhouse_matches)**:对每项需求,在**自有设备台账**中找匹配设备,判断是否
   满足图纸要求(satisfied),给出匹配到的设备型号、产能(capacity)与差距(gap_notes)。
   **只能引用台账中真实存在的设备**,台账中没有的不要臆造,标为不满足并说明。
3. **外协安排(outsourcing)**:对自有无法满足的需求,在**外协厂商台账**中找可满足的设备,
   给出厂商、设备、成本(cost)、交期;在满足且**成本合适**时 recommend=true 并说明理由
   (reason),否则 recommend=false。
4. **产能评估(capacity_summary)与总体结论(conclusion)**:综合给出能否按需求产能交付、
   瓶颈工序与外协必要性的结论。
5. 可用 web_search 查设备能力参数辅助判断,不要臆造;不确定的写进 assumptions/open_questions。
全程用中文,调用工具输出结构化 ProductionRecommendation。"""


def _equipment_brief(equipment: List[dict]) -> str:
    inhouse = [e for e in equipment if e.get("owner") != "外协"]
    outsrc = [e for e in equipment if e.get("owner") == "外协"]

    def fmt(e: dict) -> str:
        seg = f"{e.get('name','')}（{e.get('type','')}"
        if e.get("capability"):
            seg += f"，能力:{e.get('capability')}"
        if e.get("capacity"):
            seg += f"，产能:{e.get('capacity')}"
        if e.get("owner") == "外协":
            if e.get("vendor"):
                seg += f"，厂商:{e.get('vendor')}"
            if e.get("cost"):
                seg += f"，费用:{e.get('cost')}"
            if e.get("lead_time"):
                seg += f"，交期:{e.get('lead_time')}"
        return seg + "）"

    lines = ["【自有产线设备台账】"]
    lines += [f"  - {fmt(e)}" for e in inhouse] or ["  (无)"]
    lines.append("【外协厂商设备台账】")
    lines += [f"  - {fmt(e)}" for e in outsrc] or ["  (无)"]
    return "\n".join(lines)


def _context(ir: Optional[DesignIR], manufacturing_plan: Optional[dict],
             equipment: List[dict], note: str) -> str:
    lines: List[str] = []
    if ir and ir.device_name:
        lines.append(f"器件/设备名称: {ir.device_name}")

    man = manufacturing_plan or {}
    path = (man.get("path") or {}).get("steps") or []
    if path:
        lines.append("制造工艺路径(工序→设备线索):")
        for s in path[:15]:
            seg = f"  - {s.get('seq','')} {s.get('name','')}"
            if s.get("equipment"):
                seg += f"(设备: {s.get('equipment')})"
            if s.get("params"):
                seg += f" 参数: {s.get('params')}"
            lines.append(seg)
    else:
        lines.append("(暂无制造工艺路径,请基于器件与通用工艺推断所需设备)")

    lines.append("\n" + _equipment_brief(equipment))

    if note and note.strip():
        lines.append(f"\n【用户补充说明(请优先采用)】\n{note.strip()}")
    lines.append("\n请基于上面的设备台账做匹配与外协建议(只引用台账真实设备),"
                 "必要时联网检索设备能力参数,输出结构化 ProductionRecommendation。")
    return "\n".join(lines)


def recommend(
    ir: Optional[DesignIR] = None, manufacturing_plan: Optional[dict] = None,
    equipment: Optional[List[dict]] = None, note: str = "", web: bool = True,
) -> ProductionRecommendation:
    content = [claude_client.text_block(_context(ir, manufacturing_plan, equipment or [], note))]
    extra_tools = [claude_client.WEB_SEARCH_TOOL] if web else None
    sources: list = []
    rec = claude_client.run(
        SYSTEM_PROMPT, content, ProductionRecommendation,
        extra_tools=extra_tools, sources_out=sources,
    )
    have = {s.url for s in rec.search_sources}
    for s in sources:
        if s.get("url") and s["url"] not in have:
            rec.search_sources.append(WebSource(title=s.get("title") or s["url"], url=s["url"]))
            have.add(s["url"])
    return rec

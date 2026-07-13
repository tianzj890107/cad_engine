"""
组装与检测方案制定服务(技术工艺第 6 步)。

Claude 依据所选陶瓷主体/金属化与金属基座,规划陶瓷板与金属基座的焊接/胶粘组装工艺
(涂胶厚度、固化温度等),并制定电性能/吸附力/气密性检测方案;可联网检索焊料/胶粘剂/
测试标准作为依据(回填可追溯来源);"确认"由人来点。
"""
from __future__ import annotations

from typing import List, Optional

from ..models.assembly import AssemblyRecommendation
from ..models.cost import WebSource
from ..models.ir import DesignIR
from . import claude_client

SYSTEM_PROMPT = """你是资深电子陶瓷封装的组装与可靠性检测工程师。给定器件设计意图、已确定的
材料方案(陶瓷主体/金属化)与制造工艺,请制定结构化的「组装与检测方案」:

1. **组装工艺(assembly_steps)**:规划将**陶瓷板与金属基座**连接的工艺流程。先给出推荐
   连接方式(bonding_method:焊接/钎焊/共晶焊 或 胶粘)及理由(bonding_rationale,考虑 CTE 匹配、
   导热、气密、成本)。再按工序给出工步:表面处理→点胶/上焊料→贴合定位→固化/焊接→后处理,
   每步给出材料(焊料/胶粘剂牌号)、**涂胶厚度/键合层厚度**、**固化/焊接温度**、时间、压力、
   设备与质量控制点(如空洞率、对位精度)。
2. **检测方案(tests)**:至少覆盖
   - 电性能测试:**漏电流、击穿电压**(给方法/设备/判定限值);
   - **吸附力均匀性测试**(如静电吸附/真空吸附的均匀性,给方法与判定);
   - **氦气漏率测试**(气密性,给漏率限值如 <1E-9 Pa·m³/s);
   并按需补充其它必要检测。每项给出 category/method/equipment/criteria/抽样(sample)。
3. **联网检索**:可用 web_search 查焊料/胶粘剂参数与测试标准作为依据,不要臆造精确数字;
   不确定的写进 assumptions/open_questions。
全程用中文,调用工具输出结构化 AssemblyRecommendation。"""


def _context(ir: Optional[DesignIR], material_plan: Optional[dict],
             manufacturing_plan: Optional[dict], note: str) -> str:
    lines: List[str] = []
    if ir and ir.device_name:
        lines.append(f"器件/设备名称: {ir.device_name}")

    mp = material_plan or {}
    body = mp.get("body") or {}
    metal = mp.get("metallization") or {}
    if body.get("selected"):
        lines.append(f"陶瓷主体材料: {body.get('selected')}")
    if metal.get("layers"):
        ls = "; ".join(f"{l.get('layer','')}({l.get('material','')})" for l in metal.get("layers", []))
        lines.append(f"金属化层: {ls}")

    man = manufacturing_plan or {}
    path = (man.get("path") or {}).get("steps") or []
    if path:
        names = " → ".join(s.get("name", "") for s in path[:12])
        lines.append(f"已规划制造工艺路径: {names}")

    if not lines:
        lines.append("(暂无结构化输入,请基于补充说明与通用工程经验给出方案,并在 open_questions 标注缺失输入)")
    if note and note.strip():
        lines.append(f"\n【用户补充说明(请优先采用)】\n{note.strip()}")
    lines.append("\n请先联网检索相关焊料/胶粘剂/测试标准,再输出结构化 AssemblyRecommendation。")
    return "\n".join(lines)


def recommend(
    ir: Optional[DesignIR] = None, material_plan: Optional[dict] = None,
    manufacturing_plan: Optional[dict] = None, note: str = "", web: bool = True,
) -> AssemblyRecommendation:
    content = [claude_client.text_block(_context(ir, material_plan, manufacturing_plan, note))]
    extra_tools = [claude_client.WEB_SEARCH_TOOL] if web else None
    sources: list = []
    rec = claude_client.run(
        SYSTEM_PROMPT, content, AssemblyRecommendation,
        extra_tools=extra_tools, sources_out=sources,
    )
    have = {s.url for s in rec.search_sources}
    for s in sources:
        if s.get("url") and s["url"] not in have:
            rec.search_sources.append(WebSource(title=s.get("title") or s["url"], url=s["url"]))
            have.add(s["url"])
    rec.assembly_steps.sort(key=lambda x: x.seq)
    return rec

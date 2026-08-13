"""
清洗与洁净度管控方案制定服务(技术工艺第 5 步)。

Claude 依据图纸标注的洁净度等级与所选材料,定制**结构化**的化学清洗工步 + 高纯水终漂洗
流程 + 洁净度管控措施,可联网检索清洗剂配方/行业标准作为依据(回填可追溯来源);
"确认"由人来点。输入为项目 IR(含洁净度标注/备注)与第 3 步材料计划。
"""
from __future__ import annotations

from typing import List, Optional

from ..models.cost import WebSource
from ..models.cleaning import CleaningRecommendation
from ..models.ir import DesignIR
from . import llm_client as claude_client
from . import sop

SYSTEM_PROMPT = """你是资深电子陶瓷 / 半导体表面清洗与洁净度管控工程师。给定器件设计意图、
已确定的材料方案,以及**图纸标注的洁净度等级**(若有),请制定结构化清洗与洁净度管控方案:

1. **确定洁净度等级**:优先采用图纸标注的洁净度等级(cleanliness_grade),写明来源
   (grade_source:图纸标注 / 依据材料与应用推断)。若图纸未标注,请基于材料与应用合理推断,
   并在 open_questions 中提示需向客户/设计确认。
2. **定制化学清洗方案(chemical_steps)**:按工序给出化学清洗工步,**针对表面污染物选择
   合适的清洗剂/酸洗液**(如用稀硝酸/混酸去除表面金属污染物、碱洗除油、RCA 清洗等),
   给出清洗剂、浓度/配比、温度、时长、去除对象(target)、设备与**材料禁忌**(如氮化铝忌强碱/长时间水浸)。
3. **高纯水终漂洗流程(rinse_steps)**:给出终漂洗流程,介质(高纯水/超纯水 18.2 MΩ·cm)、
   方式(溢流/超声/兆声)、终点指标(出水电阻率 / TOC / 颗粒)与干燥方式。
4. **洁净度管控措施(controls)**:列出环境(洁净间等级)、过程与检测管控项及判定标准
   (颗粒度、离子残留、表面金属离子等)。
5. **联网检索**:可用 web_search 查清洗剂配方与洁净度标准作为依据,不要臆造精确数字;
   不确定的写进 assumptions/open_questions。
全程用中文,调用工具输出结构化 CleaningRecommendation。"""

GENERAL_SYSTEM_PROMPT = """你是通用工业清洗与表面质量工程师。依据项目明确的材料、污染物、表面处理
和洁净度要求输出 CleaningRecommendation。没有洁净度等级时不得自行套用半导体洁净标准；应提出
待确认项。清洗剂和参数必须考虑实际材料兼容性，不得主动引入 RCA、超纯水、陶瓷或半导体专用方案。
没有可靠参数时给出工艺类别和验证方法，不编造浓度、温度、时长或验收限值。"""


def _context(ir: Optional[DesignIR], material_plan: Optional[dict], note: str, use_web: bool) -> str:
    lines: List[str] = []
    if ir:
        if ir.device_name:
            lines.append(f"器件/设备名称: {ir.device_name}")
        if ir.parts:
            lines.append("零件构成:")
            for p in ir.parts[:20]:
                seg = f"  - {p.part_id} {p.name}"
                if getattr(p, "role", None):
                    seg += f" — {p.role}"
                lines.append(seg)

    mp = material_plan or {}
    body = mp.get("body") or {}
    metal = mp.get("metallization") or {}
    if body.get("selected"):
        lines.append(f"\n【第3步已定材料】陶瓷主体材料: {body.get('selected')}")
    if metal.get("layers"):
        ls = "; ".join(f"{l.get('layer','')}({l.get('material','')})" for l in metal.get("layers", []))
        lines.append(f"金属化层: {ls}(注意清洗剂对金属化层的兼容性)")

    if not lines:
        lines.append("(暂无结构化输入,请基于补充说明与通用工程经验给出方案,并在 open_questions 标注缺失输入)")
    lines.append(
        "\n请重点根据**图纸标注的洁净度等级**定制方案;若以下补充说明或 IR 中含洁净度标注,请据此采用。"
    )
    if note and note.strip():
        lines.append(f"\n【用户补充说明 / 图纸洁净度标注(请优先采用)】\n{note.strip()}")
    lines.append(
        "\n请先联网检索相关清洗剂/洁净度标准,再输出结构化 CleaningRecommendation。"
        if use_web else "\n请基于上述项目资料与通用工程知识输出结构化 CleaningRecommendation。"
    )
    return "\n".join(lines)


def recommend(
    ir: Optional[DesignIR] = None, material_plan: Optional[dict] = None,
    note: str = "", web: bool = True,
) -> CleaningRecommendation:
    use_web = web and claude_client.WEB_SEARCH_AVAILABLE
    profile = sop.industry_profile([
        ir.device_name if ir else "", ir.design_intent if ir else "", note, str(material_plan or ""),
    ])
    knowledge, _ = sop.load("common", profile=profile)
    content = [
        claude_client.text_block(_context(ir, material_plan, note, use_web)),
        claude_client.text_block(claude_client.web_search_notice(use_web)),
        claude_client.text_block("【行业路由与规则】\n" + knowledge),
    ]
    extra_tools = claude_client.web_search_tools(use_web)
    sources: list = []
    rec = claude_client.run(
        SYSTEM_PROMPT if profile == "electronic_ceramics" else GENERAL_SYSTEM_PROMPT,
        content, CleaningRecommendation,
        extra_tools=extra_tools, sources_out=sources,
    )
    have = {s.url for s in rec.search_sources}
    for s in sources:
        if s.get("url") and s["url"] not in have:
            rec.search_sources.append(WebSource(title=s.get("title") or s["url"], url=s["url"]))
            have.add(s["url"])
    # 确定性归一: 工步/漂洗按 seq 排序
    rec.chemical_steps.sort(key=lambda x: x.seq)
    rec.rinse_steps.sort(key=lambda x: x.seq)
    return rec

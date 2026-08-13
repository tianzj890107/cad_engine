"""
制造工艺路径规划和 BOM 编制服务(技术工艺第 4 步)。

Claude 只产出**结构化建议**(核心制造工艺路径 + 其它关键工艺评估 + 按工序分解的工艺 BOM),
允许联网检索工艺/设备资料作为依据(回填可追溯来源);平台做确定性的 BOM 汇总/CSV 导出;
"确认"由人来点。输入为项目 IR 与第 3 步材料计划(选定材料/金属化/粉末要求)。
"""
from __future__ import annotations

import csv
import io
from typing import List, Optional

from ..models.cost import WebSource
from ..models.ir import DesignIR
from ..models.manufacturing import ManufacturingRecommendation
from . import llm_client as claude_client
from . import sop

SYSTEM_PROMPT = """你是资深电子陶瓷 / 先进封装制造工艺工程师。给定器件的结构化设计意图与
已确定的材料方案,请完成「制造工艺路径规划和 BOM 编制」,输出结构化建议:

1. **核心制造工艺路径(core_path)**:给出有序工序(seq 从 1 递增),典型如
   生坯流延/成型 → 叠层/层压 → 高温共烧(脱脂+烧结) → 精密研磨/磨抛 → 金属化 → 检测。
   每道工序给出名称、类别、关键设备、关键参数(如烧结温度/保温时间/磨削量)、目的与质量控制点,
   并标注是否为关键工序(critical)。给出 path_summary 概述选路思路。
2. **其它关键制造工艺评估(additional)**:逐项评估是否还需要其它关键工艺(如共烧工艺细化、
   面型/平整度控制、激光打孔/划片、丝网印刷金属化、镀镍/镀金、气密性/可靠性检测等),
   每项标注 needed(是否需要)与理由 reason。
3. **工艺 BOM(bom)**:**基于上面的制造工序分解**编制 BOM,列出每道工序消耗/产出的
   原材料、中间品、耗材辅料、工序产出,给出 ref/类别/规格/数量/单位,并用 from_step 关联到
   对应工序(工序名或序号)。给出 bom_summary 说明 BOM 结构组成。
4. **联网检索**:可用 web_search 查工艺/设备/参数区间作为依据,不要臆造精确数字;
   没有把握的写进 open_questions 并说明 assumptions。
全程用中文,调用工具输出结构化 ManufacturingRecommendation。"""

GENERAL_SYSTEM_PROMPT = """你是通用制造工艺工程师。根据当前项目零件、材料和结构化设计意图，
输出 ManufacturingRecommendation：core_path 为符合零件类型的实际制造路径，additional 为必要
附加工艺评估，bom 为按工序分解的通用工艺 BOM。没有企业设备数据时只写设备类别，不能编造设备
编号或能力。不得主动引入流延、共烧、陶瓷金属化、半导体封装等行业工艺。缺关键输入时明确提出
open_questions，不得用行业常识伪装成项目事实。"""


def _context(ir: Optional[DesignIR], material_plan: Optional[dict], note: str, use_web: bool) -> str:
    lines: List[str] = []
    if ir:
        if ir.device_name:
            lines.append(f"器件/设备名称: {ir.device_name}")
        if ir.parts:
            lines.append("零件构成:")
            for p in ir.parts[:20]:
                seg = f"  - {p.part_id} {p.name}"
                if getattr(p, "material", None) and p.material and p.material.spec:
                    seg += f"(材料: {p.material.spec})"
                lines.append(seg)

    mp = material_plan or {}
    body = (mp.get("body") or {})
    metal = (mp.get("metallization") or {})
    supply = (mp.get("supply") or {})
    if body.get("selected"):
        lines.append(f"\n【第3步已定材料】陶瓷主体材料: {body.get('selected')}")
    if metal.get("layers"):
        ls = "; ".join(
            f"{l.get('layer','')}({l.get('material','')})" for l in metal.get("layers", [])
        )
        lines.append(f"金属化层: {ls}")
    if metal.get("paste"):
        ps = "、".join(c.get("component", "") for c in metal.get("paste", []))
        lines.append(f"电极浆料成分: {ps}")
    if supply.get("requirements"):
        rq = "; ".join(
            f"{r.get('material','')} 纯度≥{r.get('purity_pct_min','?')}% D50 {r.get('d50_um_min','?')}~{r.get('d50_um_max','?')}µm"
            for r in supply.get("requirements", [])
        )
        lines.append(f"粉末要求: {rq}")

    if not lines:
        lines.append("(暂无结构化输入,请基于补充说明与通用工程经验给出建议,并在 open_questions 标注缺失输入)")
    if note and note.strip():
        lines.append(f"\n【用户补充说明(请优先采用)】\n{note.strip()}")
    lines.append(
        "\n请先联网检索相关工艺/设备/参数,再输出结构化 ManufacturingRecommendation。"
        if use_web else "\n请基于上述项目资料与通用工程知识输出结构化 ManufacturingRecommendation。"
    )
    return "\n".join(lines)


def recommend(
    ir: Optional[DesignIR] = None, material_plan: Optional[dict] = None,
    note: str = "", web: bool = True,
) -> ManufacturingRecommendation:
    use_web = web and claude_client.WEB_SEARCH_AVAILABLE
    profile = sop.industry_profile([
        ir.device_name if ir else "", ir.design_intent if ir else "", note,
        str(material_plan or ""), *[p.name for p in (ir.parts if ir else [])],
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
        content, ManufacturingRecommendation,
        extra_tools=extra_tools, sources_out=sources,
    )
    have = {s.url for s in rec.search_sources}
    for s in sources:
        if s.get("url") and s["url"] not in have:
            rec.search_sources.append(WebSource(title=s.get("title") or s["url"], url=s["url"]))
            have.add(s["url"])
    # 确定性归一: 工序按 seq 排序
    rec.core_path.sort(key=lambda x: x.seq)
    return rec


def bom_csv(items: List[dict]) -> str:
    """把工艺 BOM 导出为 CSV 文本(确定性)。"""
    buf = io.StringIO()
    buf.write("﻿")  # BOM 头,便于 Excel 识别 UTF-8
    w = csv.writer(buf)
    w.writerow(["序号", "项目", "类别", "规格", "数量", "单位", "来源工序", "备注"])
    for it in items or []:
        w.writerow([
            it.get("ref", ""), it.get("item", ""), it.get("category", ""),
            it.get("spec", ""), it.get("quantity", ""), it.get("unit", ""),
            it.get("from_step", ""), it.get("note", ""),
        ])
    return buf.getvalue()

"""
工艺拆解服务: 把单个零件拆成结构化工艺路线(CAPP)。

延续平台思路 —— Claude 只产出**结构化**工艺规程(ProcessPlan),平台做确定性的
归一化与校验(按工序号排序、依赖合法性、工时合计、缺口提示)。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from ..models.ir import DesignIR, Part
from ..models.process import ProcessPlan
from . import claude_client

SYSTEM_PROMPT = """你是资深机械加工工艺工程师(工艺规程编制 / CAPP)。给定一个零件的结构化设计意图
(特征、材料、尺寸、公差、装配角色),请编制其**机械加工工艺路线**,拆解为有序工序步骤。

要求:
1. 工序号 step_no 按 10 递增(10/20/30...),顺序符合工艺逻辑:
   下料/备料 → 粗加工 → 半精加工 →(必要的热处理)→ 精加工 → 钳工去毛刺 → 表面处理 → 检验。
2. 每道工序给出: 类型(type)、内容描述、设备/机床、工装夹具、刀具/量具、
   切削或工艺参数、质量/检验要求、单件工时估算(分钟)、依赖的前序工序号(depends_on)。
3. 给出毛坯 blank(毛坯类型 + 下料规格,合理留加工余量)与工艺方案概述 summary
   (选材、定位基准的确定、整体加工思路)。
4. 只依据给定信息推断。凡缺关键尺寸/公差/材料而影响定工艺参数的,写进 open_questions
   并相应降低该工序的 confidence,**不要臆造精确的切削参数或公差数值**。
5. 工序数量与零件复杂度匹配(简单板件约 3~6 道,复杂件可更多),不要遗漏去毛刺与最终检验。
6. 全程用中文填写各字段。"""


def _part_prompt(part: Part, overall: Optional[DesignIR], geom: Optional[dict]) -> str:
    lines = [f"零件编号: {part.part_id}", f"零件名称: {part.name}"]
    if part.role:
        lines.append(f"装配角色/功能: {part.role}")
    if part.material:
        m = part.material
        lines.append(f"材料: {m.spec}" + (f" (密度 {m.density} g/cm³)" if m.density else ""))
    if part.tolerance_general:
        lines.append(f"一般公差: {part.tolerance_general}")
    lines.append(f"数量: {part.quantity}")

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
        if overall.design_intent:
            lines.append(f"设备设计意图: {overall.design_intent}")

    lines.append("\n请编制该零件的机械加工工艺路线,调用工具输出结构化 ProcessPlan。")
    return "\n".join(lines)


def decompose_process(
    part: Part, overall: Optional[DesignIR] = None, geom: Optional[dict] = None,
    note: str = "", attachments: Optional[List[Tuple[str, bytes]]] = None,
) -> ProcessPlan:
    prompt = _part_prompt(part, overall, geom)
    content = [claude_client.text_block(prompt)]
    if note and note.strip():
        content.append(claude_client.text_block(f"【用户补充说明(请优先采用)】\n{note.strip()}"))
    content.extend(claude_client.attachment_blocks(attachments))
    plan = claude_client.run(SYSTEM_PROMPT, content, ProcessPlan)
    # 确定性归一: 锚定 part_id/name, 按工序号排序
    plan.part_id = part.part_id
    if not plan.part_name:
        plan.part_name = part.name
    if not plan.material and part.material:
        plan.material = part.material.spec
    plan.steps.sort(key=lambda s: s.step_no)
    return plan


def compute(plan: dict) -> dict:
    """确定性派生量: 工序数、合计工时、依赖/顺序校验告警。"""
    steps = plan.get("steps", []) or []
    nos = [s.get("step_no") for s in steps]
    noset = set(nos)
    warnings: list[str] = []

    if len(noset) != len(nos):
        warnings.append("存在重复的工序号")

    total = 0.0
    has_dur = False
    for s in steps:
        d = s.get("duration_min")
        if isinstance(d, (int, float)):
            total += d
            has_dur = True
        sn = s.get("step_no")
        for dep in s.get("depends_on") or []:
            if dep == sn:
                warnings.append(f"工序 {sn} 依赖自身")
            elif dep not in noset:
                warnings.append(f"工序 {sn} 依赖了不存在的工序 {dep}")
            elif dep > sn:
                warnings.append(f"工序 {sn} 依赖了后续工序 {dep}(工序顺序可疑)")

    return {
        "step_count": len(steps),
        "total_duration_min": round(total, 1) if has_dur else None,
        "warnings": warnings,
    }

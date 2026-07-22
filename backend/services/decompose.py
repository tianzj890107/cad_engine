"""
拆解推理 / 推荐模块: 基于已解析的 IR，给出零部件生成与复用建议。

平台诉求: 拆解后"推荐生成的新零部件图纸、视图和描述"。
这里让 Claude 扮演设计评审工程师，对每个零件补全:
  - recommendation: 生成/复用/合并建议 + 工艺建议;
  - 必要时补充 assembly_notes。
输入输出都是 DesignIR(结构化)，因此可与几何生成、前端展示无缝衔接。
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from ..models.ir import DesignIR, StandardPart
from .llm_client import complete_to_model

SYSTEM_PROMPT = """\
你是一名机械设计评审 + 可制造性(DFM)专家。给你一个已解析的设备设计意图 IR(JSON)，
请在不改变已确认尺寸的前提下，对其做"拆解推荐"增强。注意：只输出推荐补丁 JSON，
不要重新输出整份 IR，也不要复述 parts/features/material/尺寸；原 IR 会由本地程序保留并合并。

1. 对需要建议的零件输出 recommendations，每项带 part_id 和 recommendation，内容包含:
   - 制造工艺建议(机加 / 钣金 / 焊接 / 铸造等)及理由;
   - 是否建议拆分或合并、是否可复用常见标准结构;
   - 关键 DFM 注意点(最小壁厚、孔边距、刀具可达性等)。
   每个 recommendation 不超过 120 个中文字符；part_id 必须来自输入。
2. assembly_notes 仅在需要补充时输出，说明零件之间的配合与装配顺序，最多 250 字。
3. 若发现明显缺失的标准件(如紧固件)，写入 additional_standard_parts；最多 10 项。
4. 不要臆造或修改已有的几何尺寸; 不要删除任何零件或特征；不确定时写空数组/省略字段。

只输出一个合法 JSON 对象。"""


class PartRecommendation(BaseModel):
    """一条可本地合并的零件 DFM 建议，避免模型重复输出整份 DesignIR。"""
    part_id: str
    recommendation: str


class DecompositionAdvice(BaseModel):
    recommendations: List[PartRecommendation] = Field(default_factory=list)
    assembly_notes: Optional[str] = None
    additional_standard_parts: List[StandardPart] = Field(default_factory=list)


def _merge_advice(ir: DesignIR, advice: DecompositionAdvice) -> DesignIR:
    """只合并允许被拆解推荐修改的字段；尺寸和几何特征永远保留原值。"""
    recommendation_by_id = {
        item.part_id: item.recommendation.strip()
        for item in advice.recommendations
        if item.part_id and item.recommendation.strip()
    }
    parts = [
        part.model_copy(update={"recommendation": recommendation_by_id[part.part_id]})
        if part.part_id in recommendation_by_id else part
        for part in ir.parts
    ]
    existing = {(part.spec, part.category or "") for part in ir.standard_parts}
    additions = []
    for part in advice.additional_standard_parts:
        key = (part.spec, part.category or "")
        if key not in existing:
            additions.append(part)
            existing.add(key)
    return ir.model_copy(update={
        "parts": parts,
        "assembly_notes": advice.assembly_notes.strip() if advice.assembly_notes and advice.assembly_notes.strip() else ir.assembly_notes,
        "standard_parts": [*ir.standard_parts, *additions],
    })


def enrich_with_recommendations(ir: DesignIR) -> DesignIR:
    """对 IR 做拆解推荐增强，只请求补丁并确定性合并回原始 IR。"""
    user_prompt = (
        "以下是待评审的设备设计意图 IR(JSON):\n\n"
        + ir.model_dump_json(indent=2)
        + "\n\n请输出拆解推荐补丁 JSON。"
    )
    advice = complete_to_model(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        output_model=DecompositionAdvice,
    )
    return _merge_advice(ir, advice)

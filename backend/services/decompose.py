"""
拆解推理 / 推荐模块: 基于已解析的 IR，给出零部件生成与复用建议。

平台诉求: 拆解后"推荐生成的新零部件图纸、视图和描述"。
这里让 Claude 扮演设计评审工程师，对每个零件补全:
  - recommendation: 生成/复用/合并建议 + 工艺建议;
  - 必要时补充 assembly_notes。
输入输出都是 DesignIR(结构化)，因此可与几何生成、前端展示无缝衔接。
"""
from __future__ import annotations

from ..models.ir import DesignIR
from .claude_client import complete_to_model

SYSTEM_PROMPT = """\
你是一名机械设计评审 + 可制造性(DFM)专家。给你一个已解析的设备设计意图 IR(JSON)，
请在不改变已确认尺寸的前提下，对其做"拆解推荐"增强:

1. 为每个零件填写/完善 recommendation 字段，内容包含:
   - 制造工艺建议(机加 / 钣金 / 焊接 / 铸造等)及理由;
   - 是否建议拆分或合并、是否可复用常见标准结构;
   - 关键 DFM 注意点(最小壁厚、孔边距、刀具可达性等)。
2. 完善 assembly_notes，说明零件之间的配合与装配顺序。
3. 若发现明显缺失的标准件(如缺少紧固件)，补入 standard_parts。
4. 不要臆造或修改已有的几何尺寸; 不要删除任何零件或特征。

输出必须是完整的、增强后的同结构 IR。"""


def enrich_with_recommendations(ir: DesignIR) -> DesignIR:
    """对 IR 做拆解推荐增强，返回增强后的 IR。"""
    user_prompt = (
        "以下是待评审的设备设计意图 IR(JSON):\n\n"
        + ir.model_dump_json(indent=2)
        + "\n\n请输出增强后的完整 IR。"
    )
    enriched = complete_to_model(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        output_model=DesignIR,
    )
    return enriched

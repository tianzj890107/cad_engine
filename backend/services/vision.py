"""
图解析模块: 设备需求原图(+补充说明/佐证文件) -> 结构化设计意图 IR，
并提供"自校验"第二遍以提升解析质量与置信度。

核心原则(与平台主线一致):
  - 大模型只负责"理解与意图结构化"，把图读成 特征 + 参数 + 装配关系。
  - 绝不让大模型脑补关键尺寸: 图上有明确标注的数值必须采用标注值;
    无标注的尺寸要给出合理估计，并在 open_questions 中标注、降低 confidence。
  - 输出必须落到 DesignIR schema(由工具调用强约束 + Pydantic 校验)。

提升置信度的两个手段(本模块实现):
  1. 上传时附带文字说明 / 佐证文件(其它视图图片、规格文本) —— 多模态上下文。
  2. 自校验第二遍(verify_drawing) —— 对照原图逐条核对尺寸并重估置信度。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from ..models.ir import DesignIR
from . import claude_client

# 佐证文件分类
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
_TEXT_EXTS = (".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml", ".ini")

# 一份共用的置信度评分标尺，纠正"虚低"
_CONFIDENCE_RUBRIC = """\
置信度(confidence)评分标尺，请严格据此打分，不要过度保守:
- 0.90~1.00: 尺寸在图/说明上有明确标注且清晰可读，特征类型无歧义。
- 0.70~0.89: 特征明确，个别尺寸由比例/常识推断但很合理。
- 0.50~0.69: 关键尺寸缺失或标注模糊，做了估计。
- < 0.50   : 视图不全/无法判断，基本靠猜。
要点: "图上/说明里写明的数字" = 高置信，不要因为是正常读数而压低分;
仅在确有不确定时才降低，并把不确定项写入 open_questions。"""

SYSTEM_PROMPT = f"""\
你是一名资深机械设计工程师 + 工程制图解析专家。你的任务是把用户上传的"设备需求原图"
(可能是规范工程图、概念草图、手绘或照片)，结合用户提供的补充说明与佐证文件，
解析成结构化的"设计意图中间表示(IR)"。

这个 IR 之后会交给确定性的 CAD 内核(OpenCASCADE / CadQuery)来生成真正的 B-rep 几何、
2D 工程图和 3D 模型。因此你的输出必须可制造、可校验、可追溯。

严格遵守以下规则:

1. 用"特征(feature)"语义描述几何，不要输出裸坐标网格。可用的特征类型:
   - plate  : 矩形板，需 length / width / thickness
   - box    : 长方体，需 length / width / height
   - cylinder: 圆柱，需 diameter / height
   - hole   : 单孔，需 diameter，可选 x/y(相对零件中心)
   - hole_pattern: 孔阵列，需 diameter + count_x/count_y + spacing_x/spacing_y
   - fillet : 整体倒圆角，需 radius
   - chamfer: 整体倒角，需 distance
   每个零件通常以一个 plate/box/cylinder 作为基体特征(第一个)，随后叠加孔/阵列/倒角。

2. 尺寸来源:
   - 图上或补充说明里有明确标注的数值，必须原样采用。
   - 没有标注但能从比例/常识推断的，给出合理估计值，并把该字段写入 open_questions。
   - 一切尺寸单位统一为毫米(mm)。

3. 充分利用补充说明 / 佐证文件: 它们是用户给的权威信息，优先级高于你的推断;
   多张图可能是同一设备的不同视图，请综合理解。

4. 拆解: 把设备拆成可独立制造的零件。能识别为标准件的(螺栓/螺母/轴承等)放入
   standard_parts，并尽量给出国标/ISO 规格，不要把标准件当作零件去建模。

4b. 层级结构树: 在 assemblies 中给出 设备-总成-子总成 的中间节点(每个有
   assembly_id、name、可选 parent_id 表示上级总成)；并为每个零件填写 parent_id
   指向其所属总成的 assembly_id(直接挂在设备下的零件 parent_id 留空)。
   若设备结构简单、无明显总成层级，可不产出 assemblies、零件 parent_id 全为空。

5. 可追溯: 尽量为每个零件填写 provenance.bbox(原图中的归一化包围盒 [x,y,w,h]，0~1)
   和 note(依据了哪条标注/哪个视图/哪条说明)。

6. {_CONFIDENCE_RUBRIC}

只通过调用工具输出结构化结果。"""

USER_INSTRUCTION = """\
请综合以上原图与补充资料，解析出完整的设计意图 IR:
- 概括 device_name、design_intent、overall_dims;
- 拆解出所有可独立制造的零件(parts)，每个零件用特征列表描述其几何;
- 识别标准件(standard_parts);
- 给出 assembly_notes 装配/配合说明;
- 对所有不确定的尺寸或判断，写入 open_questions，并按评分标尺给出 confidence。"""

VERIFY_SYSTEM_PROMPT = f"""\
你是一名严格的工程图校验员(QA)。给你: 设备原图(可能多张视图) + 用户补充说明 +
一份"初步解析的 IR(JSON)"。请对照原始资料逐条核对并修正这份 IR:

校验清单:
1. 逐个零件、逐个特征核对尺寸: 凡图/说明上有明确标注的，必须与标注一致; 若初稿读错，纠正它。
2. 检查是否漏掉了零件、孔、阵列或标准件; 补全。
3. 检查特征类型与参数是否自洽(如 plate 必须有 thickness，hole_pattern 必须有 count/spacing)。
4. 重新评估每个零件的 confidence:
   - 经核对与标注一致 → 提高到 0.9 以上;
   - 仍靠估计/模糊 → 保持较低，并确保写入 open_questions。
5. 不要臆造原图中不存在的尺寸。

{_CONFIDENCE_RUBRIC}

只通过调用工具输出"修正后的完整 IR"。"""


def _attachment_blocks(
    attachments: Optional[List[Tuple[str, bytes]]],
) -> List[dict]:
    """把佐证文件转成内容块: 图片→image 块; 文本→text 块; 其它→说明跳过。"""
    blocks: List[dict] = []
    for (name, data) in attachments or []:
        lower = name.lower()
        if lower.endswith(_IMAGE_EXTS):
            blocks.append(claude_client.text_block(f"【佐证图片: {name}】"))
            blocks.append(claude_client.image_block(data, name))
        elif lower.endswith(_TEXT_EXTS):
            try:
                text = data.decode("utf-8", errors="replace")
            except Exception:
                text = "(无法解码为文本)"
            blocks.append(
                claude_client.text_block(f"【佐证文件: {name}】\n{text}")
            )
        else:
            blocks.append(
                claude_client.text_block(f"【佐证文件 {name} 类型暂不支持解析，已忽略】")
            )
    return blocks


def _base_blocks(
    image_bytes: bytes,
    filename: str,
    note: str = "",
    attachments: Optional[List[Tuple[str, bytes]]] = None,
) -> List[dict]:
    """构造解析/校验共用的输入块: 原图 + 补充说明 + 佐证文件。"""
    blocks: List[dict] = [
        claude_client.text_block("【设备需求原图】"),
        claude_client.image_block(image_bytes, filename),
    ]
    if note and note.strip():
        blocks.append(claude_client.text_block(f"【用户补充说明】\n{note.strip()}"))
    blocks.extend(_attachment_blocks(attachments))
    return blocks


def parse_drawing(
    image_bytes: bytes,
    filename: str,
    note: str = "",
    attachments: Optional[List[Tuple[str, bytes]]] = None,
) -> DesignIR:
    """把一张原图(+补充说明/佐证文件)解析成 DesignIR。"""
    content = _base_blocks(image_bytes, filename, note, attachments)
    content.append(claude_client.text_block(USER_INSTRUCTION))
    return claude_client.run(SYSTEM_PROMPT, content, DesignIR)


def verify_drawing(
    ir: DesignIR,
    image_bytes: bytes,
    filename: str,
    note: str = "",
    attachments: Optional[List[Tuple[str, bytes]]] = None,
) -> DesignIR:
    """自校验第二遍: 对照原图核对初步 IR 的尺寸/特征并重估置信度，返回修正后的 IR。"""
    content = _base_blocks(image_bytes, filename, note, attachments)
    content.append(
        claude_client.text_block(
            "【初步解析的 IR(待核对)】\n" + ir.model_dump_json(indent=2)
        )
    )
    content.append(
        claude_client.text_block(
            "请按校验清单逐条核对并修正，调用工具输出修正后的完整 IR。"
        )
    )
    return claude_client.run(VERIFY_SYSTEM_PROMPT, content, DesignIR)

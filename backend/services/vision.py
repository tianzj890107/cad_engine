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

import json
from pathlib import Path
import re
from typing import Any, List, Optional, Tuple
import zipfile
from xml.etree import ElementTree

from ..config import (
    LLM_MAX_ATTACHMENT_IMAGE_BYTES, LLM_MAX_ATTACHMENT_TEXT_CHARS,
    LLM_MAX_ATTACHMENTS,
)
from ..models.ai import (
    AIResultStatus, EvidenceLevel, FieldEvidence, ValidationSummary, VerificationPatch,
)
from ..models.ir import DesignIR, FeatureType
from . import llm_client as claude_client
from .requirement_extract import extract_document_text
from . import sop

# 佐证文件分类
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
_TEXT_EXTS = (".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml", ".ini")
_EXTRACTABLE_DOCUMENT_EXTS = (".pdf", ".docx")

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
   技术文档中的功能、材料、尺寸、公差、洁净度、装配和制造约束，必须作为图纸解析约束;
   多张图可能是同一设备的不同视图，请综合理解。图纸与技术文档存在矛盾时，不能静默覆盖，
   保留各自明确值并在 open_questions 中说明冲突和需确认项。

4. 拆解: 把设备拆成可独立制造的零件。能识别为标准件的(螺栓/螺母/轴承等)放入
   standard_parts，并尽量给出国标/ISO 规格，不要把标准件当作零件去建模。
   若图纸、BOM 或技术文档中明确出现外购件/标准件的制造商型号或料号，填写 parts[].model_no；
   看不清、仅为推测或没有型号时必须省略，不能编造。

4b. 层级结构树: 在 assemblies 中给出 设备-总成-子总成 的中间节点(每个有
   assembly_id、name、可选 parent_id 表示上级总成)；并为每个零件填写 parent_id
   指向其所属总成的 assembly_id(直接挂在设备下的零件 parent_id 留空)。
   若设备结构简单、无明显总成层级，可不产出 assemblies、零件 parent_id 全为空。

5. 可追溯: 仅在原图中能可靠定位时填写 provenance.bbox(原图中的归一化包围盒 [x,y,w,h]，0~1)
   和 note；无法可靠定位时省略 provenance，不要编造区域或重复图纸文字。

6. {_CONFIDENCE_RUBRIC}

7. 材料 JSON 契约(必须遵守):
   - 已知材料时，material 只能写为 {{"spec": "6061-T6", "density": 数值或 null}}；
     必须有 spec，不能改用 name、grade、material_name 等字段。
   - 图纸没有可靠材料信息时，material 写 null，并在 open_questions 说明材料待确认；
     不得编造牌号。

8. 紧凑 JSON（必须遵守，避免无关输出挤占 CAD 预算）:
   - 只输出 CAD 建模、BOM 和待确认问题需要的字段；未知的可选字段直接省略，
     不要成批输出 null、空数组、重复说明或模板化文字。
   - 对完全相同的零件只保留一个 part，用 quantity 表示数量；不要为同一实体拆出多个猜测版本。
   - design_intent 不超过 160 个中文字符，assembly_notes 不超过 300 个中文字符；
     feature.purpose、recommendation、provenance.note 只在确有工程价值时填写。
   - open_questions 合并同类不确定项，每项一句话；不要重复列出同一尺寸/材料问题。

9. 以下字段必须使用这些 JSON 形状，不能换字段名或类型:
   - overall_dims: 字符串，例如 "320 x 180 x 95 mm"；不能写成 length/width/height 对象。
   - standard_parts 的每项: 必须是 {{"spec": "GB/T 5783 M8x25", "category": "bolt", "quantity": 4}}；
     spec 不得缺失，也不能改用 name/model/type。
   - open_questions 的每项: 必须是 {{"field": "P-001.thickness", "reason": "图中未标注", "guess": "10 mm"}}；
     每项必须是对象，不能只写一段字符串。

10. 字段级证据（必须遵守）:
   - 对每个零件的基体尺寸、数量、材料，以及每个孔/阵列的直径和定位参数，写入
     evidence_ledger；field 使用稳定路径，如 parts[P-001].features[0].width。
   - level 只能是 STRONG/MODERATE/WEAK/CONTRADICTORY/NONE；evidence_type 使用
     direct/derived/document/ocr/estimated/general_knowledge。
   - 明确尺寸标注可为 STRONG；多视图一致推导为 MODERATE；OCR、比例估算和常识只能为 WEAK。
   - WEAK/CONTRADICTORY/NONE 的关键字段设置 requires_confirmation=true；只有缺少可建模尺寸、
     存在高影响冲突或确实需要用户补充资料时才进入 open_questions，同类问题必须合并，
     不要把每个估算尺寸逐条列成人工确认事项。
   - assumptions 只记录实际采用的假设；ai_status、validation 和 sop_version 由本地程序最终计算。

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

输出契约（必须遵守）：
- `features[].type` 只能是下列七个英文值之一：`plate`、`box`、`cylinder`、`hole`、
  `hole_pattern`、`fillet`、`chamfer`。零件/组件名称（例如 elbow_fitting、O-ring、
  vacuum_manifold）只能写在 `parts[].name` 或 `standard_parts`，绝不能写在 type。
- 不能从原图可靠地归约为这七种几何原语的外购件/软管/接头，不要伪造 feature；保留初稿中
  已有的合法特征，并把不确定性写入 `open_questions`。
- `provenance` 必须是对象（如 `{{"note":"明细表第 1 项"}}`）或省略，不能是字符串。
- 这是在校正初稿，不是重新发明设计：没有明确证据时保留初稿的合法字段和特征类型。

{_CONFIDENCE_RUBRIC}

输出只允许是字段级补丁 VerificationPatch，禁止重新输出完整 IR。每个 changes 项必须给出：
- field：现有字段路径，仅可修改现有零件/特征的标量字段；
- old_value/new_value：合法 JSON 文本，例如 405、"Q235" 或 null；
- reason、confidence、requires_confirmation；
- evidence：原始文件、页码/视图、证据等级与定位。
只有原图明确标注且证据为 STRONG、confidence >= 0.90 时才可设置 requires_confirmation=false；
其余修改都必须等待人工确认。没有差异时 changes 输出空数组。
只通过调用工具输出 VerificationPatch。"""


_BASE_DIMENSIONS = {
    FeatureType.plate: ("length", "width", "thickness"),
    FeatureType.box: ("length", "width", "height"),
    FeatureType.cylinder: ("diameter", "height"),
}
_FEATURE_DIMENSIONS = {
    FeatureType.hole: ("diameter", "x", "y"),
    FeatureType.hole_pattern: ("diameter", "count_x", "count_y", "spacing_x", "spacing_y"),
    FeatureType.fillet: ("radius",), FeatureType.chamfer: ("distance",),
}


def build_input_manifest(filename: str, image_bytes: bytes,
                         attachments: Optional[List[Tuple[str, bytes]]] = None) -> dict:
    """阶段 1：确定性文件清单和可解析能力分类。"""
    def entry(name: str, data: bytes, role: str) -> dict:
        lower = (name or "").lower()
        if lower.endswith(_IMAGE_EXTS):
            kind, extraction = "image", "vision"
        elif lower.endswith(_EXTRACTABLE_DOCUMENT_EXTS):
            kind, extraction = "document", "local_text+vision_context"
        elif lower.endswith(_TEXT_EXTS):
            kind, extraction = "text", "local_text"
        else:
            kind, extraction = "unsupported", "none"
        return {"name": Path(name).name, "role": role, "kind": kind,
                "bytes": len(data), "extraction": extraction}
    return {
        "version": "drawing-input-1.0",
        "files": [entry(filename, image_bytes, "primary"), *[
            entry(name, data, "attachment") for name, data in (attachments or [])
        ]],
    }


def _evidence_by_field(ir: DesignIR) -> dict[str, FieldEvidence]:
    return {item.field: item for item in ir.evidence_ledger if item.field}


def _without_previous_local_validation(ir: DesignIR) -> tuple[list[str], list[str]]:
    """移除上一轮本地推导的消息，保留模型或其它校验器产生的独立结论。"""
    local_error_patterns = (
        re.compile(r"^零件 .+ 缺少可建模基体特征$"),
        re.compile(r"^parts\[[^\]]+\]\.features\[0\]\.[^.]+ 缺失$"),
        re.compile(r"^关键字段 parts\[[^\]]+\]\..+ (?:缺失|存在冲突证据)$"),
    )
    local_warning_patterns = (
        re.compile(r"^关键字段 parts\[[^\]]+\]\..+ 缺少强/中等证据，正式 CAD 生成前需人工确认$"),
        re.compile(r"^\d+ 个零件未识别材料；.*$"),
        re.compile(r"^\d+ 个工程字段证据置信度较低；.*$"),
        re.compile(r"^\d+ 个工程字段存在证据冲突；.*$"),
    )
    errors = [
        item for item in ir.validation.errors
        if not any(pattern.fullmatch(item) for pattern in local_error_patterns)
    ]
    warnings = [
        item for item in ir.validation.warnings
        if not any(pattern.fullmatch(item) for pattern in local_warning_patterns)
    ]
    return errors, warnings


def _question_targets_field(question: Any, field: str) -> bool:
    """兼容完整路径和旧版 P-001.thickness 写法，识别已被人工确认的问题。"""
    raw = str(getattr(question, "field", "") or "").strip().lower()
    normalized = field.strip().lower()
    if not raw:
        return False
    if raw == normalized:
        return True
    part_match = re.match(r"^parts\[([^\]]+)\]\.(.+)$", normalized)
    if not part_match or part_match.group(1).lower() not in raw:
        return False
    suffix = part_match.group(2).rsplit(".", 1)[-1]
    aliases = {suffix}
    if normalized.endswith(".material.spec"):
        aliases.update({"material", "material.spec", "材料"})
    elif suffix == "quantity":
        aliases.add("数量")
    return any(alias in raw for alias in aliases)


def critical_field_paths(ir: DesignIR) -> list[str]:
    paths: list[str] = []
    for part in ir.parts:
        paths.append(f"parts[{part.part_id}].quantity")
        paths.append(f"parts[{part.part_id}].material.spec")
        if not part.features:
            continue
        for field in _BASE_DIMENSIONS.get(part.features[0].type, ()):
            paths.append(f"parts[{part.part_id}].features[0].{field}")
        for index, feature in enumerate(part.features[1:], 1):
            for field in _FEATURE_DIMENSIONS.get(feature.type, ()):
                if getattr(feature, field) is not None:
                    paths.append(f"parts[{part.part_id}].features[{index}].{field}")
    return paths


def finalize_ir(ir: DesignIR, filename: str, manifest: Optional[dict] = None) -> DesignIR:
    """阶段 4~7：绑定证据、做本地校验并给出安全状态。"""
    evidence = _evidence_by_field(ir)
    errors, warnings = _without_previous_local_validation(ir)
    missing_material_parts: list[str] = []
    weak_evidence_fields: list[str] = []
    conflicting_evidence_fields: list[str] = []
    for part in ir.parts:
        if not part.features:
            errors.append(f"零件 {part.part_id} 缺少可建模基体特征")
            continue
        base = part.features[0]
        for field in _BASE_DIMENSIONS.get(base.type, ()):
            path = f"parts[{part.part_id}].features[0].{field}"
            if getattr(base, field) is None:
                errors.append(f"{path} 缺失")
                continue
            if path not in evidence:
                provenance = part.provenance
                level = EvidenceLevel.moderate if provenance and (provenance.bbox or provenance.note) and part.confidence >= .7 else EvidenceLevel.weak
                evidence[path] = FieldEvidence(
                    field=path, source_file=filename, view="未细分",
                    evidence_type="derived" if level == EvidenceLevel.moderate else "estimated",
                    level=level, note=(provenance.note if provenance else "模型未提供字段级定位"),
                    confidence=part.confidence, requires_confirmation=level != EvidenceLevel.strong,
                )
        quantity_path = f"parts[{part.part_id}].quantity"
        if quantity_path not in evidence:
            evidence[quantity_path] = FieldEvidence(
                field=quantity_path, source_file=filename, view="明细表/图面",
                evidence_type="derived", level=EvidenceLevel.moderate if part.confidence >= .7 else EvidenceLevel.weak,
                note="由零件解析结果继承", confidence=part.confidence,
                requires_confirmation=part.confidence < .9,
            )

    for path in critical_field_paths(ir):
        item = evidence.get(path)
        if path.endswith(".material.spec"):
            part_id = path.split("[", 1)[1].split("]", 1)[0]
            part = next((candidate for candidate in ir.parts if candidate.part_id == part_id), None)
            if not part or not part.material or not part.material.spec.strip():
                missing_material_parts.append(part_id)
                continue
        if not item or item.level in {EvidenceLevel.weak, EvidenceLevel.contradictory, EvidenceLevel.none}:
            weak_evidence_fields.append(path)
        if item and item.level == EvidenceLevel.contradictory:
            conflicting_evidence_fields.append(path)

    if missing_material_parts:
        warnings.append(
            f"{len(set(missing_material_parts))} 个零件未识别材料；不影响 CAD/2D 生成，"
            "材料、质量或成本分析前可按需补充"
        )
    if weak_evidence_fields:
        warnings.append(
            f"{len(set(weak_evidence_fields))} 个工程字段证据置信度较低；"
            "当前解析值仍可用于生成，正式出图或投产前建议抽样校核"
        )
    if conflicting_evidence_fields:
        warnings.append(
            f"{len(set(conflicting_evidence_fields))} 个工程字段存在证据冲突；"
            "当前值仍可用于预览生成，正式出图或投产前建议重点校核"
        )

    unique_errors = list(dict.fromkeys(errors))
    unique_warnings = list(dict.fromkeys(warnings))
    if unique_errors:
        status = AIResultStatus.blocked
    elif unique_warnings or ir.open_questions:
        status = AIResultStatus.partial
    else:
        status = AIResultStatus.ready
    return ir.model_copy(update={
        "evidence_ledger": list(evidence.values()),
        "ai_status": status,
        "validation": ValidationSummary(errors=unique_errors, warnings=unique_warnings),
        "sop_version": "drawing-1.0",
    })


def generation_gate(ir: DesignIR) -> list[str]:
    """兼容旧调用方：只返回会让确定性几何无法构建的结构问题。

    材料、证据等级和置信度属于工程治理提示，不是 CAD/2D 生成门禁；真正的
    数量、基体类型和尺寸有效性由 geometry.preflight_parts 统一检查。
    """
    if not ir.sop_version.startswith("drawing-"):
        return []
    geometric_patterns = (
        re.compile(r"^零件 .+ 缺少可建模基体特征$"),
        re.compile(r"^parts\[[^\]]+\]\.features\[0\]\.[^.]+ 缺失$"),
    )
    return [
        issue for issue in ir.validation.errors
        if any(pattern.fullmatch(issue) for pattern in geometric_patterns)
    ]


def pipeline_report(ir: DesignIR, manifest: dict) -> dict:
    """把真实执行过的解析阶段保存为可审计流水线，不伪造未运行的 OCR/企业规则。"""
    files = manifest.get("files") or []
    supported = [item for item in files if item.get("extraction") != "none"]
    unsupported = [item.get("name") for item in files if item.get("extraction") == "none"]
    views = sorted({item.view for item in ir.evidence_ledger if item.view})
    critical = critical_field_paths(ir)
    bound = {item.field for item in ir.evidence_ledger if item.field in critical}
    return {
        "version": "drawing-pipeline-1.0",
        "sop_version": ir.sop_version,
        "status": ir.ai_status.value,
        "stages": [
            {"id": "manifest", "status": "completed", "files": len(files),
             "supported": len(supported), "unsupported": unsupported},
            {"id": "local_extraction", "status": "completed",
             "methods": sorted({item.get("extraction") for item in supported})},
            {"id": "view_and_candidate_detection", "status": "completed",
             "views": views, "parts": len(ir.parts), "assemblies": len(ir.assemblies)},
            {"id": "dimension_binding", "status": "completed" if bound else "partial",
             "critical_fields": len(critical), "bound_fields": len(bound)},
            {"id": "ir_assembly", "status": "completed", "standard_parts": len(ir.standard_parts)},
            {"id": "rule_validation", "status": ir.ai_status.value,
             "errors": ir.validation.errors, "warnings": ir.validation.warnings},
        ],
    }


def mark_human_confirmed(ir: DesignIR, actor: str) -> DesignIR:
    """人工保存 IR 即确认当前关键值，为后续工程校核建立可审计证据。"""
    # 用户可能刚补齐上一轮缺失的材料或尺寸，必须先重算本地校验，不能把旧错误
    # 原样带进新版本并继续显示为未解决问题。
    ir = finalize_ir(ir, "人工校核")
    evidence = _evidence_by_field(ir)
    confirmed_fields: set[str] = set()
    for path in critical_field_paths(ir):
        if path.endswith(".material.spec"):
            part_id = path.split("[", 1)[1].split("]", 1)[0]
            part = next((candidate for candidate in ir.parts if candidate.part_id == part_id), None)
            if not part or not part.material or not part.material.spec.strip():
                continue
        evidence[path] = FieldEvidence(
            field=path, source_file="人工校核", view="工作台",
            evidence_type="human_confirmed", level=EvidenceLevel.strong,
            note=f"{actor} 保存并确认当前值", confidence=1.0, requires_confirmation=False,
        )
        confirmed_fields.add(path)
    remaining_questions = [
        question for question in ir.open_questions
        if not any(_question_targets_field(question, field) for field in confirmed_fields)
    ]
    confirmed = ir.model_copy(update={
        "evidence_ledger": list(evidence.values()), "open_questions": remaining_questions,
    })
    # 强证据写入后再重算一次，清除已经解决的“缺失/冲突”本地错误。
    return finalize_ir(confirmed, "人工校核")


def confirm_evidence_field(ir: DesignIR, field: str, actor: str) -> DesignIR:
    """人工接受单个 AI 校核补丁后，只提升该字段的证据，不连带确认其它字段。"""
    evidence = _evidence_by_field(ir)
    previous = evidence.get(field)
    evidence[field] = FieldEvidence(
        field=field, source_file=(previous.source_file if previous else "人工校核"),
        page=previous.page if previous else None, view=previous.view if previous else "工作台",
        bbox=previous.bbox if previous else None, evidence_type="human_confirmed",
        level=EvidenceLevel.strong,
        note=f"{actor} 接受 AI 字段级校核建议" + (f"；原依据：{previous.note}" if previous and previous.note else ""),
        confidence=1.0, requires_confirmation=False,
    )
    remaining_questions = [
        question for question in ir.open_questions if not _question_targets_field(question, field)
    ]
    confirmed = ir.model_copy(update={
        "evidence_ledger": list(evidence.values()), "open_questions": remaining_questions,
    })
    return finalize_ir(confirmed, "人工校核")


def _attachment_text(name: str, data: bytes) -> str:
    """提取可作为图纸解析上下文的附件文字，支持 PDF/DOCX。"""
    lower = (name or "").lower()
    if lower.endswith(_TEXT_EXTS):
        return data.decode("utf-8", errors="replace")
    if lower.endswith(_EXTRACTABLE_DOCUMENT_EXTS):
        return extract_document_text(name, data)
    raise ValueError("不支持提取文本")


def _attachment_blocks(
    attachments: Optional[List[Tuple[str, bytes]]],
) -> List[dict]:
    """把佐证文件转成内容块: 图片→image 块; 文本/PDF/DOCX→text 块。"""
    blocks: List[dict] = []
    attachments = list(attachments or [])
    for index, (name, data) in enumerate(attachments):
        if LLM_MAX_ATTACHMENTS > 0 and index >= LLM_MAX_ATTACHMENTS:
            blocks.append(claude_client.text_block(
                f"【其余 {len(attachments) - index} 个佐证附件因上下文预算未发送给模型】"
            ))
            break
        lower = name.lower()
        if lower.endswith(_IMAGE_EXTS):
            if len(data) > LLM_MAX_ATTACHMENT_IMAGE_BYTES:
                blocks.append(claude_client.text_block(
                    f"【佐证图片 {name} 过大，未发送给模型（上限 {LLM_MAX_ATTACHMENT_IMAGE_BYTES} bytes）】"
                ))
                continue
            blocks.append(claude_client.text_block(f"【佐证图片: {name}】"))
            blocks.append(claude_client.image_block(data, name, detail="low"))
        elif lower.endswith(_TEXT_EXTS) or lower.endswith(_EXTRACTABLE_DOCUMENT_EXTS):
            try:
                text = _attachment_text(name, data)
            except (OSError, ValueError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
                blocks.append(claude_client.text_block(
                    f"【技术文档 {name} 无法提取可读文本，未作为解析依据：{exc}】"
                ))
                continue
            if not text.strip():
                blocks.append(claude_client.text_block(
                    f"【技术文档 {name} 未提取到可读文本，未作为解析依据】"
                ))
                continue
            if len(text) > LLM_MAX_ATTACHMENT_TEXT_CHARS:
                text = text[:LLM_MAX_ATTACHMENT_TEXT_CHARS] + "\n【附件内容已按上下文预算截断】"
            blocks.append(
                claude_client.text_block(f"【技术文档/佐证文件: {name}】\n{text}")
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
    """分阶段解析：清单/提取 -> 视觉候选 -> 本地 IR 组装与规则校验。"""
    manifest = build_input_manifest(filename, image_bytes, attachments)
    profile = sop.industry_profile([
        filename, note, *[name for name, _ in (attachments or [])],
        *[_attachment_text(name, data)[:5000] for name, data in (attachments or [])
          if name.lower().endswith(_TEXT_EXTS)],
    ])
    knowledge, _ = sop.load("drawing", profile=profile)
    content = _base_blocks(image_bytes, filename, note, attachments)
    content.insert(0, claude_client.text_block(
        "【本地输入清单】\n" + json.dumps(manifest, ensure_ascii=False)
        + "\n\n【本次适用 SOP】\n" + knowledge
    ))
    content.append(claude_client.text_block(USER_INSTRUCTION))
    candidate = claude_client.run(SYSTEM_PROMPT, content, DesignIR)
    return finalize_ir(candidate, filename, manifest)


def verify_drawing(
    ir: DesignIR,
    image_bytes: bytes,
    filename: str,
    note: str = "",
    attachments: Optional[List[Tuple[str, bytes]]] = None,
) -> VerificationPatch:
    """自校验第二遍：只返回差异补丁，绝不重新生成完整 IR。"""
    profile = sop.industry_profile([filename, note, ir.device_name, ir.design_intent])
    knowledge, _ = sop.load("drawing_verify", profile=profile)
    content = _base_blocks(image_bytes, filename, note, attachments)
    content.insert(0, claude_client.text_block("【本次适用 SOP】\n" + knowledge))
    content.append(
        claude_client.text_block(
            "【初步解析的 IR(待核对)】\n" + ir.model_dump_json(indent=2)
        )
    )
    content.append(
        claude_client.text_block(
            "请按校验清单逐条核对，仅输出字段级 VerificationPatch。"
        )
    )
    return claude_client.run(VERIFY_SYSTEM_PROMPT, content, VerificationPatch)


_VERIFY_PATH = re.compile(
    r"^parts\[([^\]]+)\]\.(name|model_no|role|quantity|confidence|tolerance_general|"
    r"material\.spec|features\[(\d+)\]\.(length|width|thickness|height|diameter|radius|distance|"
    r"x|y|count_x|count_y|spacing_x|spacing_y|purpose))$"
)


def _json_scalar(value: str) -> Any:
    decoded = json.loads(value)
    if isinstance(decoded, (dict, list)):
        raise ValueError("校核补丁只允许标量字段")
    return decoded


def apply_verification_patch(ir: DesignIR, patch: VerificationPatch, *, auto_only: bool = True) -> tuple[DesignIR, list[dict], list[dict]]:
    """本地白名单应用强证据补丁；其余保留为待人工确认建议。"""
    updated = ir.model_copy(deep=True)
    applied: list[dict] = []
    pending: list[dict] = []
    by_id = {part.part_id: part for part in updated.parts}
    for change in patch.changes:
        payload = change.model_dump()
        matched = _VERIFY_PATH.fullmatch(change.field)
        if not matched:
            payload["rejected_reason"] = "字段路径不在校核白名单"
            pending.append(payload)
            continue
        part = by_id.get(matched.group(1))
        if not part:
            payload["rejected_reason"] = "零件不存在"
            pending.append(payload)
            continue
        try:
            old_value, new_value = _json_scalar(change.old_value), _json_scalar(change.new_value)
        except (ValueError, json.JSONDecodeError) as exc:
            payload["rejected_reason"] = f"值不是合法 JSON 标量：{exc}"
            pending.append(payload)
            continue
        field = matched.group(2)
        feature_index = matched.group(3)
        if field.startswith("features["):
            index = int(feature_index)
            if index >= len(part.features):
                payload["rejected_reason"] = "特征索引不存在"
                pending.append(payload)
                continue
            target, attr = part.features[index], field.rsplit(".", 1)[1]
        elif field == "material.spec":
            if not part.material:
                payload["rejected_reason"] = "当前零件没有材料对象，不能由校核补丁新建"
                pending.append(payload)
                continue
            target, attr = part.material, "spec"
        else:
            target, attr = part, field
        current = getattr(target, attr)
        if current != old_value and str(current) != str(old_value):
            payload["rejected_reason"] = "旧值与当前 IR 不一致"
            pending.append(payload)
            continue
        safe = (
            not change.requires_confirmation and change.confidence >= .9
            and change.evidence.level == EvidenceLevel.strong
        )
        if auto_only and not safe:
            payload["pending_reason"] = "证据或置信度未达到自动应用门槛"
            pending.append(payload)
            continue
        try:
            # 每项补丁在独立副本上试应用。校验失败时丢弃副本，确保“待确认/拒绝”
            # 的坏值绝不会污染后续 IR 或影响同批其它补丁。
            candidate = updated.model_dump()
            candidate_part = next(item for item in candidate["parts"] if item["part_id"] == part.part_id)
            if field.startswith("features["):
                candidate_target = candidate_part["features"][int(feature_index)]
            elif field == "material.spec":
                candidate_target = candidate_part["material"]
            else:
                candidate_target = candidate_part
            candidate_target[attr] = new_value
            updated = DesignIR.model_validate(candidate)
            by_id = {item.part_id: item for item in updated.parts}
        except Exception as exc:
            payload["rejected_reason"] = f"新值未通过 IR 校验：{exc}"
            pending.append(payload)
            continue
        updated.evidence_ledger = [item for item in updated.evidence_ledger if item.field != change.field]
        updated.evidence_ledger.append(change.evidence.model_copy(update={"field": change.field}))
        applied.append(payload)
    updated = finalize_ir(updated, "AI 校核")
    return updated, applied, pending

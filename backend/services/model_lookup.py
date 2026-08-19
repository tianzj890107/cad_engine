"""型号候选 -> Qwen 联网检索 -> 可人工确认的零件识别证据。

联网资料先独立保存；只有用户明确确认的匹配结果才写入新的 DesignIR/BOM 版本。
"""
from __future__ import annotations

import re
from typing import Iterable

from ..models.ir import DesignIR, StandardPart
from ..models.model_lookup import ModelLookupResult
from ..time_utils import now_cst_str
from . import qwen_client
from .requirement_extract import prepare_documents

_MODEL_RE = re.compile(
    r"(?<![A-Z0-9])(?:"
    r"[A-Z]{1,10}[-/]?[A-Z0-9][A-Z0-9._/-]{2,}"  # VQ110-5M
    r"|[A-Z]{1,5}\d{1,6}(?:\s+[A-Za-z][A-Za-z0-9-]{1,16}){0,2}"  # H7 Pro
    r")(?![A-Z0-9])", re.I,
)
_SKIP_TOKENS = re.compile(r"^(?:P-?\d+|A-?\d+|M\d+(?:X\d+)?|ISO[-\s]?\d+|GB/?T|Q\d{3,4}|SUS\d+|AL\d+)$", re.I)
_MAX_CANDIDATES = 12
_MAX_DOCUMENT_CONTEXT = 18000

_SYSTEM_PROMPT = """你是通用工业产品与设备外购件型号核验工程师。根据项目中明确出现的型号候选，
使用联网搜索核验其对应的零件或产品。

严格规则：
1. 你自主决定应搜索哪些型号、产品标识、制造商组合与关键词；candidate_hints 只是线索，不是必须逐条搜索的清单。
   但最终 candidate_model 必须是项目资料中实际出现过的原文，不能补造、修正或猜测型号。
2. 优先制造商官网、官方 PDF/目录；搜索结果不足时 status=ambiguous 或 not_found，不能强行匹配；
   发现只是营销术语、技术名词而非可唯一定位的型号时，使用 not_a_model。
3. 每项 status 只能是 matched、ambiguous、not_found、not_a_model 之一。
4. 所有结论都 requires_confirmation=true；本次结果仅供人工确认，绝不等价于修改 CAD/BOM。
5. specification_summary 只写公开的关键规格摘要，不要复制长篇目录；confidence 必须是 0 到 1 的数字（例如 0.80），且必须保守。
6. 当公开资料主要指向一台整机、一个产品系列或技术架构，而非可唯一定位的外购料号时：
   - 不要因此返回空结果。写 product_summary，并给出 proposed_components 与 process_designs；
   - proposed_components 是“联网推演候选部件”，例如背光模组、控制芯片、光学层、显示面板；只写公开资料直接支持的存在与功能，
     不得编造尺寸、数量、供应商、内部料号或图纸未证实的结构；全部 requires_confirmation=true；
   - process_designs 只概括公开资料提到的技术/工艺路线及待工程确认的控制点，不能伪装成已验证的制造工艺卡；
   - 这两类内容可使用产品名/技术名，不受 candidate_model 的“原文完整出现”限制。
7. 只输出一个合法 JSON 对象，不要 Markdown、解释或代码块。"""


def _lookup_with_search(prompt: dict, *, max_tokens: int):
    """按当前选中的语言模型走对应提供商的联网检索。

    百炼有自己的原生搜索 API（会返回可核验的来源），Anthropic / OpenAI 则用各自的
    hosted web search 工具。之前这里写死走百炼，换成别的模型就会报
    "Qwen 调用失败"。
    """
    from . import llm_client, llm_settings

    provider = llm_settings.provider_of(llm_settings.selected_model(vision=False))
    if provider == "qwen":
        return qwen_client.complete_to_model_with_web_search(
            _SYSTEM_PROMPT, prompt, ModelLookupResult, max_tokens=max_tokens)

    sources: list = []
    result = llm_client.run(
        _SYSTEM_PROMPT,
        [llm_client.text_block(str(prompt)),
         llm_client.text_block(llm_client.web_search_notice(True))],
        ModelLookupResult,
        extra_tools=llm_client.web_search_tools(True),
        max_tokens=max_tokens,
        sources_out=sources,
    )
    return result, {
        "sources": sources,
        "search_count": len(sources),
        "model": llm_client.last_used_model() or "",
    }


def _valid_token(value: str) -> bool:
    token = value.strip(".,;:()[]{}<>\"' ")
    if not 4 <= len(token) <= 80 or not any(char.isascii() and char.isalpha() for char in token):
        return False
    if _SKIP_TOKENS.fullmatch(token):
        return False
    return any(char.isalpha() for char in token)


def _document_identifier_lines(text: str) -> list[str]:
    """补足没有连续料号的产品标识，例如“海信 RGB-MiniLED 电视 UX 2026”。"""
    values: list[str] = []
    for raw in (text or "").splitlines():
        value = raw.strip().lstrip("·•-* ").strip()
        # prepare_documents 写入的资料标题不是产品/型号，不能作为搜索线索。
        if value.startswith("【技术文档："):
            continue
        if not 4 <= len(value) <= 100:
            continue
        # 至少含一个拉丁字母：避免把普通中文句子误作为外购件型号。
        if not any(char.isascii() and char.isalpha() for char in value):
            continue
        if value not in values:
            values.append(value)
    return values[:_MAX_CANDIDATES]


def _tokens(text: str) -> list[str]:
    values: list[str] = []
    for match in _MODEL_RE.finditer(text or ""):
        token = match.group(0).strip(".,;:()[]{}<>\"' ")
        if _valid_token(token) and token not in values:
            values.append(token)
    return values


def _candidate_context(ir: DesignIR, attachments: Iterable[tuple[str, bytes]]) -> tuple[list[dict], str, str]:
    """从已解析 IR 和可读技术资料中收集型号候选及其原始上下文。"""
    rows: list[dict] = []
    seen: set[str] = set()

    def add(token: str, context: str, part_id: str | None = None) -> None:
        key = token.upper()
        if key in seen or len(rows) >= _MAX_CANDIDATES:
            return
        if _valid_token(token):
            seen.add(key)
            rows.append({"candidate_model": token, "source_context": context[:360], "related_part_id": part_id})

    for part in ir.parts:
        if part.model_no:
            add(part.model_no, f"零件 {part.part_id} {part.name} 的图纸识别型号：{part.model_no}", part.part_id)
        context = " · ".join(filter(None, [part.name, part.role or "", part.provenance.note if part.provenance else ""]))
        for token in _tokens(context):
            add(token, f"零件 {part.part_id} 的识别上下文：{context}", part.part_id)
    for standard in ir.standard_parts:
        for token in _tokens(standard.spec):
            add(token, f"标准件/BOM 规格：{standard.spec}")

    prepared = prepare_documents(attachments)
    document_text = prepared.text[:_MAX_DOCUMENT_CONTEXT]
    for segment in document_text.split("\n"):
        for token in _tokens(segment):
            add(token, f"技术文档：{segment.strip()}")
    # 技术文档中很多消费电子/半导体标识并非 VQ110-5M 这类连续料号，
    # 例如 H7 Pro、RGB-MiniLED电视UX 2026。候选不足时把短标题行也交给
    # 联网模型判断是否确为型号，模型可返回 not_a_model，仍不自动写入 BOM。
    for identifier in _document_identifier_lines(document_text):
        add(identifier, f"技术文档产品/技术标识：{identifier}")
    ir_text = "\n".join(
        " · ".join(filter(None, [part.part_id, part.name, part.model_no or "", part.role or ""]))
        for part in ir.parts
    ) + "\n" + "\n".join(standard.spec for standard in ir.standard_parts)
    return rows, document_text, ir_text


def identify_models(ir: DesignIR, attachments: Iterable[tuple[str, bytes]]) -> ModelLookupResult:
    candidates, document_text, ir_text = _candidate_context(ir, attachments)
    if not candidates and not (document_text or ir_text).strip():
        return ModelLookupResult(
            summary="未在已解析零件、BOM 或可读技术文档中发现可联网核验的型号候选。",
            open_questions=["请在补充说明或技术文档中提供制造商和完整型号后再核验。"],
            generated_at=now_cst_str(),
        )
    prompt = {
        "device_name": ir.device_name,
        "candidate_hints": candidates,
        "relevant_parts": [
            {"part_id": part.part_id, "name": part.name, "role": part.role or "", "model_no": part.model_no or ""}
            for part in ir.parts[:80]
        ],
        "technical_document_excerpt": document_text,
        "project_identification_text": ir_text,
        "task": "先自行判断哪些术语值得联网检索，再仅输出有工程意义的型号/产品标识核验结论；不要为了凑数量而输出每个提示词。",
    }
    # 型号核验可能同时返回识别、产品级候选部件与工艺推演，使用完整文本预算，
    # 避免结果在 proposals 中间被截断。
    result, metadata = _lookup_with_search(prompt, max_tokens=12000)
    result.search_sources = metadata["sources"]
    result.search_count = metadata["search_count"]
    result.model = metadata["model"]
    result.generated_at = now_cst_str()
    candidate_parts = {
        str(item.get("candidate_model") or "").strip().upper(): item.get("related_part_id")
        for item in candidates if item.get("related_part_id")
    }
    for item in result.identifications:
        # 模型可能漏回 related_part_id；候选提取阶段已有精确零件来源时由本地补回，
        # 让用户清楚确认后会写入哪个零件，也避免后续按型号回退匹配到错误实体。
        if not item.related_part_id:
            item.related_part_id = candidate_parts.get(item.candidate_model.strip().upper())
    # 防止模型返回项目资料中不存在的型号，保证每项都可追溯到图纸/BOM/技术文档。
    project_corpus = (document_text + "\n" + ir_text).upper()
    # 确认接口以候选型号为稳定键；模型偶尔重复返回同一候选时只保留第一项，
    # 避免一次人工确认同时作用于多个相互矛盾的识别结论。
    filtered = []
    seen_models: set[str] = set()
    for item in result.identifications:
        key = item.candidate_model.strip().upper()
        if not key or key not in project_corpus or key in seen_models:
            continue
        seen_models.add(key)
        filtered.append(item)
        if len(filtered) >= _MAX_CANDIDATES:
            break
    result.identifications = filtered
    if not result.identifications:
        result.open_questions.append("联网服务未对项目中的型号候选返回可用结论，请人工核验。")
    return result


def apply_lookup_results(ir: DesignIR, report: dict) -> tuple[DesignIR, list[dict]]:
    """将可靠的联网匹配同步到 IR/BOM，并返回逐项的前后变更记录。

    只处理 ``matched``：其余状态代表搜索未能形成可靠结论，保留在核验报告里供复核，
    绝不以猜测覆盖原始图纸解析。所有调用方须把结果作为一个新的 IR 版本保存。
    """
    updated = ir.model_copy(deep=True)
    changes: list[dict] = []

    def clean(value: object) -> str:
        return str(value or "").strip()

    for item in report.get("identifications", []):
        if clean(item.get("status")) != "matched":
            continue
        candidate = clean(item.get("candidate_model"))
        if not candidate:
            continue
        part_id = clean(item.get("related_part_id"))
        name = clean(item.get("identified_part_name"))
        category = clean(item.get("category"))
        manufacturer = clean(item.get("manufacturer"))
        specification = clean(item.get("specification_summary"))
        evidence = clean(item.get("evidence_summary"))

        # 优先精确关联零件编号；模型未给编号时仅在原型号明确相同的零件上同步，
        # 不通过名称模糊猜测来错误覆盖加工件。
        target = next((part for part in updated.parts if part.part_id == part_id), None) if part_id else None
        if target is None:
            target = next((part for part in updated.parts if clean(part.model_no).upper() == candidate.upper()), None)
        if target is not None:
            before = {
                "name": target.name, "model_no": target.model_no or "", "manufacturer": target.manufacturer or "",
                "model_specification": target.model_specification or "",
            }
            target.model_no = candidate
            if name:
                target.name = name
            if manufacturer:
                target.manufacturer = manufacturer
            if specification:
                target.model_specification = specification
            if evidence:
                target.model_lookup_evidence = evidence
            after = {
                "name": target.name, "model_no": target.model_no or "", "manufacturer": target.manufacturer or "",
                "model_specification": target.model_specification or "",
            }
            if before != after:
                changes.append({
                    "target": "part", "part_id": target.part_id, "candidate_model": candidate,
                    "before": before, "after": after,
                })
            continue

        # 文档中出现、但图纸尚未拆成对应零件的可靠外购件，进入标准件/BOM，
        # 不伪造几何零件；后续人工可在工作台将其关联到实体零件。
        existing = next((standard for standard in updated.standard_parts if clean(standard.model_no).upper() == candidate.upper()), None)
        if existing is None:
            existing = StandardPart(
                spec=" · ".join(value for value in (candidate, name) if value) or candidate,
                category=category or None,
                quantity=1,
                model_no=candidate,
                manufacturer=manufacturer or None,
                model_specification=specification or None,
                model_lookup_evidence=evidence or None,
            )
            updated.standard_parts.append(existing)
            changes.append({
                "target": "standard_part", "candidate_model": candidate, "action": "added",
                "after": {"spec": existing.spec, "category": existing.category or "", "manufacturer": existing.manufacturer or ""},
            })
        else:
            before = {"spec": existing.spec, "category": existing.category or "", "manufacturer": existing.manufacturer or ""}
            if name and candidate not in existing.spec:
                existing.spec = f"{candidate} · {name}"
            if category:
                existing.category = category
            if manufacturer:
                existing.manufacturer = manufacturer
            if specification:
                existing.model_specification = specification
            if evidence:
                existing.model_lookup_evidence = evidence
            after = {"spec": existing.spec, "category": existing.category or "", "manufacturer": existing.manufacturer or ""}
            if before != after:
                changes.append({
                    "target": "standard_part", "candidate_model": candidate, "action": "updated",
                    "before": before, "after": after,
                })

    # 当资料只能确认“产品架构”而不能确认 CAD 几何零件时，写入 BOM 的联网推演候选区。
    # 它们使用明确前缀，绝不会被误认为原图直接识别出的加工件或已确认标准件。
    for proposal in report.get("proposed_components", []):
        name = clean(proposal.get("name"))
        if not name:
            continue
        model_no = clean(proposal.get("model_no"))
        category = clean(proposal.get("category")) or "联网推演部件"
        manufacturer = clean(proposal.get("manufacturer"))
        role = clean(proposal.get("role"))
        evidence = clean(proposal.get("evidence_summary"))
        spec = f"【联网推演·待图纸确认】{name}"
        existing = next(
            (standard for standard in updated.standard_parts
             if standard.spec == spec or (model_no and clean(standard.model_no).upper() == model_no.upper())),
            None,
        )
        if existing is None:
            existing = StandardPart(
                spec=spec, category=category, quantity=1, model_no=model_no or None,
                manufacturer=manufacturer or None, model_specification=role or None,
                model_lookup_evidence=evidence or None,
            )
            updated.standard_parts.append(existing)
            changes.append({
                "target": "web_component", "candidate_model": model_no or name, "action": "added",
                "after": {"spec": existing.spec, "category": existing.category or "", "manufacturer": existing.manufacturer or ""},
            })
            continue
        before = {"spec": existing.spec, "category": existing.category or "", "manufacturer": existing.manufacturer or "", "model_specification": existing.model_specification or ""}
        if category:
            existing.category = category
        if manufacturer:
            existing.manufacturer = manufacturer
        if role:
            existing.model_specification = role
        if evidence:
            existing.model_lookup_evidence = evidence
        after = {"spec": existing.spec, "category": existing.category or "", "manufacturer": existing.manufacturer or "", "model_specification": existing.model_specification or ""}
        if before != after:
            changes.append({
                "target": "web_component", "candidate_model": model_no or name, "action": "updated",
                "before": before, "after": after,
            })
    return updated, changes

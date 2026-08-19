"""为所有异步 AI 任务生成统一、旁路持久化的治理元数据。"""
from __future__ import annotations

from typing import Any

from ..config import LLM_PROVIDER, active_model, active_text_model
from ..models.ai import AIResultMetadata, AIResultStatus, FieldEvidence, ValidationSummary


AI_TASK_KINDS = {
    "parse", "verify", "model_lookup", "decompose", "process", "cost",
    "material_recommend", "manufacturing_recommend", "cleaning_recommend",
    "assembly_recommend", "production_recommend", "summary_recommend",
    "costest_recommend", "pricing_recommend", "negotiation_recommend",
    "pricenego_recommend", "approval_recommend", "requirement_document_extract",
}

_SOP_VERSION = {
    "parse": "drawing-1.0", "verify": "drawing-verify-1.0",
    "model_lookup": "model-lookup-1.0", "decompose": "dfm-1.0",
    "process": "process-1.0", "requirement_document_extract": "requirement-1.0",
}


def _walk(payload: Any, key: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, list):
            found.extend(value)
        elif value not in (None, "", {}):
            found.append(value)
        for child_key, child in payload.items():
            if child_key != key and isinstance(child, (dict, list)):
                found.extend(_walk(child, key))
    elif isinstance(payload, list):
        for child in payload:
            found.extend(_walk(child, key))
    return found


def _model(kind: str) -> str:
    """留痕用的模型名。以「模型设置」里选中的那个为准。

    以前只在 LLM_PROVIDER=="qwen" 时才去问实际用过的模型，否则回落到 .env 常量 ——
    换成别的提供商后，审计记录里写的就不是真正跑过的模型。
    """
    try:
        from . import llm_client, llm_settings

        return llm_client.last_used_model() or llm_settings.selected_model(
            vision=(kind == "parse"))
    except Exception:                                   # pragma: no cover - 配置异常
        return active_model() if kind == "parse" else active_text_model()


def metadata(kind: str, result: Any) -> dict:
    payload = result if isinstance(result, dict) else {}
    root = payload.get("ir") if isinstance(payload.get("ir"), dict) else payload
    errors = [str(item) for item in _walk(root, "errors")]
    warnings = [str(item) for item in _walk(root, "warnings")]
    questions = _walk(root, "open_questions")
    assumptions = [str(item) for item in _walk(root, "assumptions")]
    evidence: list[FieldEvidence] = []
    for item in _walk(root, "evidence_ledger") + _walk(root, "evidence"):
        if isinstance(item, dict) and item.get("field"):
            try:
                evidence.append(FieldEvidence.model_validate(item))
            except Exception:
                continue
    confidence_values = []
    for value in _walk(root, "confidence"):
        try:
            number = float(value)
            if 0 <= number <= 1:
                confidence_values.append(number)
        except (TypeError, ValueError):
            pass
    explicit = str(root.get("ai_status") or root.get("status") or "").upper() if isinstance(root, dict) else ""
    if explicit in {item.value for item in AIResultStatus}:
        status = AIResultStatus(explicit)
    elif errors:
        status = AIResultStatus.blocked
    elif questions or warnings:
        status = AIResultStatus.partial
    else:
        status = AIResultStatus.ready
    doc = AIResultMetadata(
        status=status, evidence=evidence, assumptions=list(dict.fromkeys(assumptions)),
        open_questions=questions, validation=ValidationSummary(
            errors=list(dict.fromkeys(errors)), warnings=list(dict.fromkeys(warnings)),
        ),
        confidence=(sum(confidence_values) / len(confidence_values)) if confidence_values else None,
        model=_model(kind), provider=LLM_PROVIDER,
        sop_version=str(root.get("sop_version") or _SOP_VERSION.get(kind, f"{kind}-1.0")) if isinstance(root, dict) else _SOP_VERSION.get(kind, f"{kind}-1.0"),
    )
    return doc.model_dump(mode="json")


"""AI 任务统一的证据、状态和校核补丁契约。"""
from __future__ import annotations

from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator

from .coercion import StrList


class AIResultStatus(str, Enum):
    ready = "READY"
    partial = "PARTIAL"
    blocked = "BLOCKED"


class EvidenceLevel(str, Enum):
    strong = "STRONG"
    moderate = "MODERATE"
    weak = "WEAK"
    contradictory = "CONTRADICTORY"
    none = "NONE"


class FieldEvidence(BaseModel):
    """一个结构化字段的来源；bbox 使用原文件页面的归一化坐标。"""
    field: str = Field(..., description="字段路径，如 parts[P-001].features[0].width")
    source_file: str = Field("", description="来源文件名")
    page: Optional[int] = Field(None, description="页码，从 1 开始")
    view: Optional[str] = Field(None, description="主视/俯视/侧视/剖视/明细表/技术要求等")
    bbox: Optional[List[float]] = Field(None, description="归一化 [x,y,w,h]")
    evidence_type: str = Field(
        "direct", description="direct/derived/document/ocr/estimated/general_knowledge"
    )
    level: EvidenceLevel = EvidenceLevel.none
    note: Optional[str] = Field(None, description="简短依据，不复制大段原文")
    confidence: float = 0.5
    requires_confirmation: bool = False

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value):
        try:
            number = float(str(value).rstrip("%"))
            if "%" in str(value) or number > 1:
                number /= 100
            return max(0.0, min(1.0, number))
        except (TypeError, ValueError):
            return 0.5


class ValidationSummary(BaseModel):
    errors: StrList = Field(default_factory=list)
    warnings: StrList = Field(default_factory=list)


class AIResultMetadata(BaseModel):
    status: AIResultStatus = AIResultStatus.partial
    evidence: List[FieldEvidence] = Field(default_factory=list)
    assumptions: StrList = Field(default_factory=list)
    open_questions: List[Any] = Field(default_factory=list)
    validation: ValidationSummary = Field(default_factory=ValidationSummary)
    confidence: Optional[float] = None
    model: str = ""
    provider: str = ""
    sop_version: str = ""


class VerificationChange(BaseModel):
    """AI 校核只能建议一个字段变化，不能重写整份 IR。"""
    field: str
    old_value: str = Field("", description="旧值的 JSON 文本")
    new_value: str = Field("", description="新值的 JSON 文本")
    reason: str = ""
    evidence: FieldEvidence
    confidence: float = 0.5
    requires_confirmation: bool = True

    @field_validator("old_value", "new_value", mode="before")
    @classmethod
    def normalize_json_scalar_text(cls, value):
        """兼容模型直接返回数字/布尔/null，统一为补丁使用的 JSON 文本。"""
        if isinstance(value, str):
            return value
        import json
        return json.dumps(value, ensure_ascii=False)

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value):
        return FieldEvidence.normalize_confidence(value)


class VerificationPatch(BaseModel):
    changes: List[VerificationChange] = Field(default_factory=list)
    assumptions: StrList = Field(default_factory=list)
    open_questions: StrList = Field(default_factory=list)
    summary: str = ""


class VerificationPatchDecision(BaseModel):
    field: str
    decision: str = Field(pattern="^(confirmed|rejected)$")
    note: str = Field("", max_length=600)

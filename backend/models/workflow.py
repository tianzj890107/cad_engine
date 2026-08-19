"""企业工艺评估流程的持久化模型。

该模型不替换既有的图纸 IR、工艺方案或报价数据；它只记录需求受理和
工艺评估报告在创建、确认、审核、发布及分发过程中的业务状态和签名。
"""
from __future__ import annotations

import json
from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator

from .coercion import StrList


class WorkflowReview(BaseModel):
    action: str
    actor: str
    role: str = ""
    comment: str = ""
    at: str


class RequirementDoc(BaseModel):
    project_id: str
    requirement_no: str
    title: str = ""
    status: str = "draft"  # draft -> pending_confirmation -> pending_review -> approved/rejected
    data: dict[str, Any] = Field(default_factory=dict)
    created_by: str = ""
    created_at: Optional[str] = None
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None
    confirmation_note: str = ""
    # 手动触发的 Qwen 确认检查结果。保留在需求单中，刷新确认页后仍可追溯。
    # 默认空，避免保存/浏览需求单时产生任何模型调用。
    ai_check: dict[str, Any] = Field(default_factory=dict)
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    review_note: str = ""
    history: List[WorkflowReview] = Field(default_factory=list)
    updated_at: Optional[str] = None


class RequirementDynamicSpecField(BaseModel):
    """“灵活”行业由文本模型建议的产品规格字段定义。"""
    section: str = "3.1"
    key: str
    label: str
    value: str = ""
    placeholder: str = ""
    input_type: str = "text"
    required: bool = False


class RequirementDocumentExtraction(BaseModel):
    """纯文本模型从技术文档提取出的 1.1 草稿补丁，不直接替代人工确认。"""
    title: str = ""
    fields: dict[str, str] = Field(default_factory=dict)
    # 仅对文档没有明确给出的必填字段保存 AI 默认建议；非必填字段保持空白。
    recommendations: dict[str, str] = Field(default_factory=dict)
    # 每个 AI 推荐值的置信度，范围 0~1；字段明确带入的值不在此列。
    recommendation_confidence: dict[str, float] = Field(default_factory=dict)
    summary: str = ""
    open_questions: StrList = Field(default_factory=list)
    # 自动行业识别；手动选择行业时仅作为建议与留痕，不覆盖人工选择。
    industry: str = "flexible"
    industry_confidence: float = 0.0
    industry_reason: str = ""
    # 仅 industry=flexible 时使用；固定行业不依赖这组字段。
    flexible_spec_fields: List[RequirementDynamicSpecField] = Field(default_factory=list)

    @field_validator("recommendation_confidence", mode="before")
    @classmethod
    def normalize_recommendation_confidence(cls, value):
        if not isinstance(value, dict):
            return {}
        normalized = {}
        for key, raw in value.items():
            try:
                text = str(raw).strip()
                number = float(text[:-1]) / 100 if text.endswith("%") else float(text)
            except (TypeError, ValueError):
                continue
            normalized[str(key)] = max(0.0, min(1.0, number))
        return normalized

    @field_validator("flexible_spec_fields", mode="before")
    @classmethod
    def normalize_flexible_spec_fields(cls, value):
        """容忍文本模型在动态字段数组中夹带空对象、数字或别名字段。

        动态表单属于“建议项”，单个坏项不应让整次技术文档解析报废、更不应触发
        再次发送文档。缺少 key/label 的项没有可安全展示的语义，直接忽略。
        """
        if value is None:
            return []
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                return []
        candidates = value if isinstance(value, list) else [value]
        normalized = []
        for item in candidates:
            if isinstance(item, str):
                try:
                    item = json.loads(item)
                except (TypeError, ValueError):
                    continue
            if not isinstance(item, dict):
                continue
            row = dict(item)
            # 常见同义字段只做确定性映射；绝不虚构字段名或中文标签。
            row["key"] = row.get("key") or row.get("field_key") or row.get("field") or row.get("name")
            row["label"] = row.get("label") or row.get("field_label") or row.get("title") or row.get("display_name")
            if not str(row.get("key") or "").strip() or not str(row.get("label") or "").strip():
                continue
            normalized.append(row)
        return normalized


class ReportRecipient(BaseModel):
    name: str
    organization: str = ""
    contact: str = ""
    channel: str = "平台通知"


class ReportEvaluationItem(BaseModel):
    """3.1 汇总页的工艺可行性结论行。"""
    item: str
    status: str = "可行"
    conclusion: str = ""


class ReportStageResult(BaseModel):
    """3.1 汇总页的 2.1–2.6 阶段结论行。"""
    stage: str
    conclusion: str = ""


class ReportReviewProgress(BaseModel):
    """工艺报告的汇总、审核、发布状态行。"""
    role: str
    status: str = "待审核"
    date: str = "—"


class ReportAttachment(BaseModel):
    """汇总报告中可追溯的附件入口。"""
    name: str
    source: str
    href: str = ""


class ReportReviewItem(BaseModel):
    """审核人针对每一项工艺可行性结论给出的意见。"""
    item: str
    tag: str = "同意。"
    opinion: str = ""


class ProcessReport(BaseModel):
    project_id: str
    report_no: str
    requirement_no: str = ""
    title: str = "工艺评估报告"
    status: str = "draft"  # draft -> in_review -> approved/rejected -> published
    overview: str = ""
    highlights: StrList = Field(default_factory=list)
    risks: StrList = Field(default_factory=list)
    conclusion: str = ""
    # 3.1 汇总结果页的可编辑结构化内容；与底层各工艺步骤数据并存。
    # display_mode=reference 时展示经产品确认的参考稿占位内容；当 2.2–2.6
    # 均有真实保存结果时，前端将自动优先渲染实时汇总结论。
    display_mode: str = "reference"
    basic_info: dict[str, str] = Field(default_factory=dict)
    evaluation_items: List[ReportEvaluationItem] = Field(default_factory=list)
    stage_results: List[ReportStageResult] = Field(default_factory=list)
    review_progress: List[ReportReviewProgress] = Field(default_factory=list)
    attachments: List[ReportAttachment] = Field(default_factory=list)
    review_items: List[ReportReviewItem] = Field(default_factory=list)
    review_conclusion: str = ""
    distribution_scope: str = ""
    distribution_cc: str = ""
    source_snapshot: dict[str, Any] = Field(default_factory=dict)
    prepared_by: str = ""
    prepared_at: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    review_note: str = ""
    published_by: Optional[str] = None
    published_at: Optional[str] = None
    recipients: List[ReportRecipient] = Field(default_factory=list)
    version: int = 1
    history: List[WorkflowReview] = Field(default_factory=list)
    updated_at: Optional[str] = None


class WorkflowAction(BaseModel):
    comment: str = ""
    decision: str = "approve"
    review_items: List[ReportReviewItem] = Field(default_factory=list)
    review_conclusion: str = ""
    distribution_scope: str = ""
    distribution_cc: str = ""


class PublishAction(BaseModel):
    comment: str = ""
    recipients: List[ReportRecipient] = Field(default_factory=list)


class ReportDistributionSettings(BaseModel):
    """3.1/3.2 共同维护的发布范围与抄送对象。"""
    distribution_scope: str = Field(default="", max_length=1200)
    distribution_cc: str = Field(default="", max_length=1200)

"""企业工艺评估流程的持久化模型。

该模型不替换既有的图纸 IR、工艺方案或报价数据；它只记录需求受理和
工艺评估报告在创建、确认、审核、发布及分发过程中的业务状态和签名。
"""
from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


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


class RequirementDocumentExtraction(BaseModel):
    """纯文本模型从技术文档提取出的 1.1 草稿补丁，不直接替代人工确认。"""
    title: str = ""
    fields: dict[str, str] = Field(default_factory=dict)
    summary: str = ""
    open_questions: List[str] = Field(default_factory=list)


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
    highlights: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
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

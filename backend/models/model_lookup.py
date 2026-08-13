"""2.1 图纸解析中的外购件/型号联网核验结果。

该结果先作为独立补充证据保存；经人工确认后才写入零件/BOM，不自动改写几何。
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from .cost import WebSource


def _normalize_confidence(value):
    """兼容模型常用的 high/medium 文字等级，统一供前端显示百分比。"""
    if isinstance(value, str):
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        levels = {
            "very_high": 0.92, "high": 0.80, "medium": 0.60,
            "low": 0.35, "very_low": 0.15,
            "很高": 0.92, "高": 0.80, "中": 0.60, "中等": 0.60,
            "低": 0.35, "很低": 0.15,
        }
        if normalized in levels:
            return levels[normalized]
        if normalized.endswith("%"):
            try:
                return float(normalized[:-1]) / 100
            except ValueError:
                return value
    return value


class ModelIdentification(BaseModel):
    candidate_model: str = Field(..., description="图纸、BOM 或技术文档中出现的料号、型号或产品标识候选")
    related_part_id: Optional[str] = Field(None, description="关联的已解析零件编号")
    source_context: str = Field("", description="型号在项目资料中的原始上下文")
    status: str = Field("ambiguous", description="matched / ambiguous / not_found / not_a_model")
    identified_part_name: str = Field("", description="联网核验后的零件/产品名称")
    category: str = Field("", description="零件分类，例如电磁阀、真空接头")
    manufacturer: str = Field("", description="制造商；无可靠证据时留空")
    specification_summary: str = Field("", description="公开规格摘要；不复制整份目录")
    confidence: float = Field(0.0, description="匹配置信度 0~1")
    evidence_summary: str = Field("", description="为何认为匹配或不匹配")
    requires_confirmation: bool = Field(True, description="所有联网结论均须人工确认")

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value):
        return _normalize_confidence(value)


class ProductComponentProposal(BaseModel):
    """联网公开资料推演出的产品级候选部件，区别于原图直接识别的 CAD 零件。"""
    name: str = Field(..., description="候选部件名称")
    category: str = Field("", description="背光模组、控制芯片、光学层、显示面板等")
    role: str = Field("", description="在产品架构中的功能")
    manufacturer: str = Field("", description="来源明确时的制造商")
    model_no: str = Field("", description="来源明确时的型号；技术宣传名可留空")
    related_part_id: Optional[str] = Field(None, description="若可明确关联图纸零件则填写 P-xxx")
    confidence: float = Field(0.5, description="公开资料对该部件存在性的支持强度 0~1")
    evidence_summary: str = Field("", description="公开资料依据与不确定边界")
    requires_confirmation: bool = Field(True, description="必须由图纸/BOM/工程师确认")

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value):
        return _normalize_confidence(value)


class ProcessDesignProposal(BaseModel):
    name: str = Field(..., description="工艺/技术方案名称")
    related_component: str = Field("", description="关联候选部件")
    design_summary: str = Field("", description="公开资料支持的工艺或设计要点")
    key_controls: List[str] = Field(default_factory=list, description="需工程确认的关键控制点")
    confidence: float = Field(0.5, description="公开资料支持强度 0~1")
    requires_confirmation: bool = Field(True)

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value):
        return _normalize_confidence(value)


class ModelLookupResult(BaseModel):
    summary: str = ""
    identifications: List[ModelIdentification] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)
    search_sources: List[WebSource] = Field(default_factory=list)
    search_count: int = 0
    model: str = ""
    generated_at: str = ""
    confirmations: dict[str, dict] = Field(default_factory=dict)
    applied_changes: List[dict] = Field(default_factory=list, description="人工确认后同步到 IR/BOM 的变更明细")
    auto_sync_attempted_at: str = Field("", description="旧数据兼容字段；当前流程不再自动同步")
    product_summary: str = Field("", description="产品级联网资料综合判断，不等同于图纸直接识别")
    proposed_components: List[ProductComponentProposal] = Field(default_factory=list)
    process_designs: List[ProcessDesignProposal] = Field(default_factory=list)

    @field_validator("proposed_components", mode="before")
    @classmethod
    def normalize_text_component_proposals(cls, value):
        """兼容模型把候选部件写成一句话而非对象的常见输出。"""
        if value is None:
            return []
        if not isinstance(value, list):
            value = [value]
        normalized = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if not text:
                    continue
                name, separator, detail = text.partition("：")
                normalized.append({
                    "name": name.strip() or text[:80],
                    "role": detail.strip() if separator else text,
                    "evidence_summary": "模型以自然语言返回的联网推演摘要，待图纸/BOM确认。",
                    "requires_confirmation": True,
                })
            else:
                normalized.append(item)
        return normalized

    @field_validator("process_designs", mode="before")
    @classmethod
    def normalize_text_process_proposals(cls, value):
        """兼容模型把工艺要点写成一句话而非对象的常见输出。"""
        if value is None:
            return []
        if not isinstance(value, list):
            value = [value]
        normalized = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if not text:
                    continue
                name, separator, detail = text.partition("：")
                normalized.append({
                    "name": name.strip() or text[:80],
                    "design_summary": detail.strip() if separator else text,
                    "key_controls": ["公开资料推演，需由工程图、BOM与工艺工程师确认"],
                    "requires_confirmation": True,
                })
            else:
                normalized.append(item)
        return normalized

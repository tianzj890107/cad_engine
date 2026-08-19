"""
技术工艺总结 IR —— 技术工艺第 8 步(收尾)。

汇总前面各步(材料/制造工艺/清洗/组装检测/产线)的结果,产出可编辑的执行摘要并可导出
为文档。各步的结构化数据仍由各自的 doc 持有(在这里可跳转编辑),本模型只持久化
"执行摘要"这部分可编辑文字。
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from .coercion import StrList
from .ir import OpenQuestion
from .cost import WebSource
from .material import Timing


class SummaryRecommendation(BaseModel):
    """Claude 依据各步汇总产出的执行摘要(纯文字结论,不含表格)。"""
    overview: Optional[str] = Field(None, description="总体概述(器件/材料/工艺路线一句话到一段)")
    highlights: StrList = Field(default_factory=list, description="关键亮点/结论要点")
    risks: StrList = Field(default_factory=list, description="风险与待办/待澄清")
    conclusion: Optional[str] = Field(None, description="总体结论(可否量产/下一步)")
    open_questions: List[OpenQuestion] = Field(default_factory=list, description="需澄清的问题")
    search_sources: List[WebSource] = Field(default_factory=list, description="联网检索来源(可追溯)")


class SummaryDoc(BaseModel):
    project_id: Optional[str] = None
    title: str = Field("技术工艺总结报告", description="文档标题")
    overview: Optional[str] = None
    highlights: StrList = Field(default_factory=list)
    risks: StrList = Field(default_factory=list)
    conclusion: Optional[str] = None
    confirmed: bool = False
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None
    timing: Timing = Field(default_factory=Timing)
    search_sources: List[WebSource] = Field(default_factory=list)
    updated_at: Optional[str] = None

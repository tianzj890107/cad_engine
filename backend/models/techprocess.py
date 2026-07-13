"""
技术工艺 / 商机报价 记录 —— 技术工艺流程「结束」步最终录入到管理列表的一条记录。

biz=tech 录入到《技术工艺管理》列表;biz=quote 录入到《商机报价管理》列表。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class TechProcessRecord(BaseModel):
    id: Optional[str] = Field(None, description="主键(留空由后端生成)")
    code: Optional[str] = Field(None, description="工艺/报价编号(留空由后端生成)")
    name: str = Field("未命名技术工艺", description="名称")
    project_id: Optional[str] = Field(None, description="关联的图纸项目")
    biz: str = Field("tech", description="业务: tech(技术工艺) | quote(报价)")
    status: Optional[str] = Field(None, description="状态,如 '已录入' / '已定稿'")
    owner: Optional[str] = Field(None, description="录入人")
    created_at: Optional[str] = Field(None, description="录入时间")
    note: Optional[str] = Field(None, description="备注")

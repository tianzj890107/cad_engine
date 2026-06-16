"""
工艺拆解 IR (Process Plan) —— 把单个零件拆成结构化的"工艺路线"。

与设计意图 IR 一脉相承的思路: 大模型(Claude)只产出**结构化**的工艺规程(工序步骤 +
参数 + 依赖关系),而不是一段自由文本 —— 从而可校验(工序依赖/工时合计/缺参标注)、
可编辑(人在环改参)、可追溯(每步置信度 + 待澄清)。确定性的部分(排序、合计工时、
依赖校验、缺口提示)由平台计算,见 services/process.py。

注意: 同样为兼容工具调用承载的结构化输出,不使用数值约束。
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from .ir import OpenQuestion


class ProcessType(str, Enum):
    blank = "blank"              # 下料/备料(锯切、剪板、激光、铸/锻毛坯)
    turning = "turning"         # 车
    milling = "milling"         # 铣
    drilling = "drilling"       # 钻/扩/铰
    boring = "boring"           # 镗
    grinding = "grinding"       # 磨
    bench = "bench"             # 钳工(去毛刺、攻丝、修配)
    sheet_metal = "sheet_metal"  # 钣金(折弯、冲压、卷圆)
    welding = "welding"         # 焊接
    heat_treat = "heat_treat"   # 热处理(调质、淬火、退火、渗碳)
    surface = "surface"         # 表面处理(发黑、电镀、阳极、喷涂)
    assembly = "assembly"       # 装配
    inspection = "inspection"   # 检验
    other = "other"


class ProcessStep(BaseModel):
    step_no: int = Field(..., description="工序号，按 10 递增(10/20/30...)")
    name: str = Field(..., description="工序名称，如 '粗铣基准面'")
    type: ProcessType = Field(..., description="工序类型")
    description: str = Field(..., description="工序内容/加工要求描述")
    equipment: Optional[str] = Field(None, description="设备/机床，如 'CNC 加工中心 VMC850'")
    fixture: Optional[str] = Field(None, description="工装夹具")
    tooling: Optional[str] = Field(None, description="刀具/量具，如 'φ12 立铣刀; 游标卡尺'")
    params: Optional[str] = Field(None, description="切削/工艺参数，如 'S3000 F600 ap1.0'")
    quality: Optional[str] = Field(None, description="质量要求/检验项，如 '平面度 0.05; Ra1.6'")
    duration_min: Optional[float] = Field(None, description="单件工时估算(分钟)")
    depends_on: List[int] = Field(
        default_factory=list, description="依赖的前序工序号(必须先完成)"
    )
    note: Optional[str] = Field(None, description="备注")
    confidence: float = Field(0.6, description="该工序的置信度 0~1")


class ProcessPlan(BaseModel):
    part_id: str = Field(..., description="对应零件编号")
    part_name: str = Field(..., description="零件名称")
    material: Optional[str] = Field(None, description="材料牌号")
    blank: Optional[str] = Field(None, description="毛坯类型/下料规格，如 '板料 110×90×14 Q235'")
    summary: str = Field(..., description="工艺方案概述(选材/定位基准/加工思路)")
    steps: List[ProcessStep] = Field(default_factory=list, description="工序步骤(工艺路线)")
    overall_note: Optional[str] = Field(None, description="整体工艺备注/注意事项")
    open_questions: List[OpenQuestion] = Field(
        default_factory=list, description="需工艺工程师澄清的问题(缺尺寸/公差/材料等)"
    )

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
import json
import re
from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

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


# 结构化输出在不同模型、不同提示词版本下会有少量表达差异。这里做的是
# 本地、无模型调用的兼容层：把常见的中文工序名/字段别名统一为 ProcessPlan
# 的稳定数据结构，避免一份可用的工艺方案因为枚举或数值格式不同而被丢弃。
_PROCESS_TYPE_ALIASES = {
    "blank": ("下料", "备料", "毛坯", "锯切", "剪板", "激光切割", "cutting", "sawing", "laser"),
    "turning": ("车削", "车加工", "车床", "turning", "turn"),
    "milling": ("铣削", "铣加工", "加工中心", "cnc", "milling", "mill"),
    "drilling": ("钻孔", "钻削", "扩孔", "铰孔", "攻丝", "tapping", "drilling", "drill"),
    "boring": ("镗孔", "镗削", "boring", "bore"),
    "grinding": ("磨削", "研磨", "抛光", "grinding", "grind"),
    "bench": ("钳工", "去毛刺", "修配", "倒角", "bench", "deburr"),
    "sheet_metal": ("钣金", "冲压", "折弯", "卷圆", "sheet metal", "forming"),
    "welding": ("焊接", "钎焊", "welding", "weld"),
    "heat_treat": ("热处理", "退火", "淬火", "调质", "时效", "渗碳", "heat treat"),
    "surface": ("表面处理", "阳极", "电镀", "喷涂", "发黑", "surface", "anod"),
    "assembly": ("装配", "assembly", "assemble"),
    "inspection": ("检验", "检测", "测试", "质检", "inspection", "test"),
}


def _first_number(value: Any, *, integer: bool = False) -> Optional[float | int]:
    """从“10 分钟”“OP20”“85%”等模型常见输出中取出首个数字。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if integer else float(value)
    matched = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    if not matched:
        return None
    number = float(matched.group())
    return int(number) if integer else number


def _alias_value(data: dict, target: str, *aliases: str) -> None:
    """仅当标准字段为空时采用模型可能使用的别名。"""
    if data.get(target) not in (None, "", [], {}):
        return
    for alias in aliases:
        value = data.get(alias)
        if value not in (None, "", [], {}):
            data[target] = value
            return


def _normalise_process_type(value: Any) -> ProcessType:
    if isinstance(value, ProcessType):
        return value
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in ProcessType._value2member_map_:
        return ProcessType(raw)
    compact = raw.replace("_", "")
    for key, names in _PROCESS_TYPE_ALIASES.items():
        if any(name.lower().replace(" ", "").replace("_", "") in compact for name in names):
            return ProcessType(key)
    return ProcessType.other


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

    @model_validator(mode="before")
    @classmethod
    def normalize_model_output(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        _alias_value(data, "step_no", "seq", "sequence", "no", "step", "工序号")
        _alias_value(data, "name", "process_name", "operation", "process", "工序名称")
        _alias_value(data, "type", "process_type", "operation_type", "工序类型")
        _alias_value(data, "description", "content", "process_content", "operation_content", "requirement", "工序内容")
        _alias_value(data, "equipment", "machine", "machine_tool", "设备", "机床")
        _alias_value(data, "fixture", "jig", "夹具", "工装")
        _alias_value(data, "tooling", "tools", "tool", "刀具", "量具")
        _alias_value(data, "params", "parameters", "process_parameters", "参数", "工艺参数")
        _alias_value(data, "quality", "inspection", "quality_requirement", "quality_check", "质量要求", "检验要求")
        _alias_value(data, "duration_min", "duration", "time_min", "time", "estimated_time", "工时")
        _alias_value(data, "depends_on", "dependencies", "depends", "predecessors", "前置工序")
        _alias_value(data, "note", "remarks", "备注")
        _alias_value(data, "confidence", "confidence_score", "置信度")

        if data.get("step_no") in (None, ""):
            data["step_no"] = 10
        if not str(data.get("name") or "").strip():
            data["name"] = f"工序 {data['step_no']}"
        if not str(data.get("description") or "").strip():
            data["description"] = str(data["name"])
        data["type"] = _normalise_process_type(data.get("type") or data.get("name"))
        return data

    @field_validator("step_no", mode="before")
    @classmethod
    def normalize_step_no(cls, value):
        return _first_number(value, integer=True) or 10

    @field_validator("duration_min", mode="before")
    @classmethod
    def normalize_duration(cls, value):
        return _first_number(value) if value not in (None, "") else None

    @field_validator("depends_on", mode="before")
    @classmethod
    def normalize_dependencies(cls, value):
        if value in (None, "", "无", "none", "None", "-"):
            return []
        values = value if isinstance(value, list) else re.split(r"[,，、;/\s]+", str(value))
        return [number for item in values if (number := _first_number(item, integer=True)) is not None]

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value):
        if value in (None, ""):
            return 0.6
        text = str(value).strip().lower()
        labels = {
            "very_high": 0.95, "very high": 0.95, "极高": 0.95,
            "high": 0.85, "高": 0.85,
            "medium": 0.6, "中": 0.6, "一般": 0.6,
            "low": 0.35, "低": 0.35,
        }
        if text in labels:
            return labels[text]
        number = _first_number(value)
        if number is None:
            return 0.6
        if "%" in text or number > 1:
            number /= 100
        return max(0.0, min(1.0, number))


class ProcessPlan(BaseModel):
    part_id: str = Field("", description="对应零件编号")
    part_name: str = Field("", description="零件名称")
    material: Optional[str] = Field(None, description="材料牌号")
    blank: Optional[str] = Field(None, description="毛坯类型/下料规格，如 '板料 110×90×14 Q235'")
    summary: str = Field("", description="工艺方案概述(选材/定位基准/加工思路)")
    steps: List[ProcessStep] = Field(default_factory=list, description="工序步骤(工艺路线)")
    overall_note: Optional[str] = Field(None, description="整体工艺备注/注意事项")
    open_questions: List[OpenQuestion] = Field(
        default_factory=list, description="需工艺工程师澄清的问题(缺尺寸/公差/材料等)"
    )
    part_class: str = Field("machining", description="确定性零件分类/工艺模板")
    rule_warnings: List[str] = Field(default_factory=list, description="通用工艺规则校验告警")
    sop_version: str = Field("", description="采用的工艺 SOP 版本")

    @model_validator(mode="before")
    @classmethod
    def normalize_model_output(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        _alias_value(data, "part_id", "part_no", "id", "component_id", "零件编号")
        _alias_value(data, "part_name", "name", "component_name", "零件名称")
        _alias_value(data, "material", "material_spec", "材料")
        _alias_value(data, "blank", "raw_material", "stock", "毛坯")
        _alias_value(data, "summary", "process_summary", "overview", "工艺概述", "工艺方案")
        _alias_value(data, "steps", "operations", "process_steps", "route", "工艺路线", "工序")
        _alias_value(data, "overall_note", "note", "remarks", "整体备注")
        _alias_value(data, "open_questions", "questions", "clarifications", "待澄清项")
        if isinstance(data.get("steps"), str):
            try:
                decoded = json.loads(data["steps"])
                data["steps"] = decoded if isinstance(decoded, list) else [decoded]
            except (TypeError, ValueError):
                data["steps"] = [data["steps"]]
        elif not isinstance(data.get("steps"), list):
            data["steps"] = []
        if not isinstance(data.get("open_questions"), list):
            data["open_questions"] = [data["open_questions"]] if data.get("open_questions") else []
        return data

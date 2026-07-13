"""
材料定性与供应链拆解 IR —— 技术工艺第 3 步。

与设计意图 IR / 成本 IR 一脉相承:Claude 只产出**结构化建议**(候选陶瓷主体材料 +
电极/金属化配方 + 粉末纯度/粒径要求,并可**联网检索**材料特性/行情/供应商公开信息作为
依据),平台做**确定性**的供应商达标匹配;"确认"由人来点。可校验、可编辑、可追溯。

注意: 为兼容工具调用承载的结构化输出,不使用数值约束(min/max),在 Python 侧校验。
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from .cost import WebSource
from .ir import OpenQuestion


# --------------------------------------------------------------------------- #
# ① 陶瓷主体材料候选
# --------------------------------------------------------------------------- #
class BodyCandidate(BaseModel):
    material: str = Field(..., description="材料名称,如 '氧化铝(Al2O3 96%)' / '氮化铝(AlN)' / '氮化硅(Si3N4)'")
    score: float = Field(0.6, description="综合推荐度 0~1")
    thermal_conductivity: Optional[str] = Field(None, description="热导率,如 '170-230 W/m·K'")
    dielectric: Optional[str] = Field(None, description="介电性能(介电常数/损耗/耐压)")
    cte: Optional[str] = Field(None, description="热膨胀系数 CTE,如 '4.5 ppm/K'")
    mechanical: Optional[str] = Field(None, description="机械强度/断裂韧性等")
    cost_level: Optional[str] = Field(None, description="成本水平,如 '低/中/高' 或大致单价")
    pros: List[str] = Field(default_factory=list, description="优点")
    cons: List[str] = Field(default_factory=list, description="缺点/风险")
    recommended: bool = Field(False, description="是否为推荐项")
    source: Optional[str] = Field(None, description="主要依据/出处")


# --------------------------------------------------------------------------- #
# ② 电极浆料 & 金属化层
# --------------------------------------------------------------------------- #
class PasteComponent(BaseModel):
    component: str = Field(..., description="成分,如 'Ag' / 'Pd' / '玻璃料' / '有机载体'")
    ratio_pct: Optional[float] = Field(None, description="占比(wt%)")
    role: Optional[str] = Field(None, description="作用,如 '导电相' / '附着/烧结' / '流变载体'")


class MetalLayer(BaseModel):
    layer: str = Field(..., description="层名,如 '金属化底层' / '镀镍' / '镀金'")
    material: Optional[str] = Field(None, description="材料,如 'Mo-Mn' / 'Ni' / 'Au'")
    thickness_um: Optional[float] = Field(None, description="厚度(µm)")
    process: Optional[str] = Field(None, description="工艺,如 '钼锰法+高温烧结' / '化学镀' / '电镀'")


# --------------------------------------------------------------------------- #
# ③ 粉末要求(供应商达标判定的基准)
# --------------------------------------------------------------------------- #
class PowderRequirement(BaseModel):
    material: str = Field(..., description="物料,如 '氧化铝粉' / '氮化铝粉' / 'Ag-Pd 浆料'")
    purity_pct_min: Optional[float] = Field(None, description="最低纯度(%)")
    d50_um_min: Optional[float] = Field(None, description="D50 粒径下限(µm)")
    d50_um_max: Optional[float] = Field(None, description="D50 粒径上限(µm)")
    notes: Optional[str] = Field(None, description="其它要求,如比表面积/杂质上限")


# --------------------------------------------------------------------------- #
# Claude 产出的建议(可联网检索;平台据此填充 MaterialPlan)
# --------------------------------------------------------------------------- #
class MaterialRecommendation(BaseModel):
    body_candidates: List[BodyCandidate] = Field(default_factory=list, description="陶瓷主体材料候选(含氧化铝/氮化铝等对比)")
    body_recommended: Optional[str] = Field(None, description="推荐选用的主体材料名称")
    body_rationale: Optional[str] = Field(None, description="选材理由")
    paste: List[PasteComponent] = Field(default_factory=list, description="电极浆料成分配方")
    layers: List[MetalLayer] = Field(default_factory=list, description="金属化层结构")
    metallization_rationale: Optional[str] = Field(None, description="电极/金属化方案理由")
    requirements: List[PowderRequirement] = Field(default_factory=list, description="推荐的粉末纯度/粒径要求")
    assumptions: List[str] = Field(default_factory=list, description="关键假设")
    open_questions: List[OpenQuestion] = Field(default_factory=list, description="需澄清的问题")
    search_sources: List[WebSource] = Field(default_factory=list, description="联网检索来源(可追溯)")


# --------------------------------------------------------------------------- #
# 平台聚合 / 持久化的完整计划(含人工选定/确认/供应商匹配/时间)
# --------------------------------------------------------------------------- #
class BodySelection(BaseModel):
    candidates: List[BodyCandidate] = Field(default_factory=list)
    selected: Optional[str] = Field(None, description="人工选定的主体材料")
    rationale: Optional[str] = None
    confirmed: bool = False
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None


class Metallization(BaseModel):
    paste: List[PasteComponent] = Field(default_factory=list)
    layers: List[MetalLayer] = Field(default_factory=list)
    rationale: Optional[str] = None
    confirmed: bool = False
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None


class SupplierMatch(BaseModel):
    supplier: str
    material: Optional[str] = None
    offered_purity_pct: Optional[float] = None
    offered_d50_um: Optional[float] = None
    qualified: bool = False
    gap_notes: Optional[str] = None


class SupplyEvaluation(BaseModel):
    requirements: List[PowderRequirement] = Field(default_factory=list)
    matches: List[SupplierMatch] = Field(default_factory=list)
    conclusion: Optional[str] = None
    evaluated_at: Optional[str] = None


class Timing(BaseModel):
    status: str = Field("not_started", description="not_started | in_progress | done")
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    completed: bool = False


class MaterialPlan(BaseModel):
    project_id: Optional[str] = None
    body: BodySelection = Field(default_factory=BodySelection)
    metallization: Metallization = Field(default_factory=Metallization)
    supply: SupplyEvaluation = Field(default_factory=SupplyEvaluation)
    timing: Timing = Field(default_factory=Timing)
    assumptions: List[str] = Field(default_factory=list)
    open_questions: List[OpenQuestion] = Field(default_factory=list)
    search_sources: List[WebSource] = Field(default_factory=list)
    updated_at: Optional[str] = None


# --------------------------------------------------------------------------- #
# 供应商能力目录(全局,种子可维护)
# --------------------------------------------------------------------------- #
class Supplier(BaseModel):
    id: Optional[str] = Field(None, description="主键(留空由后端生成)")
    name: str = Field(..., description="供应商名称")
    material: str = Field(..., description="可供物料,如 '氧化铝粉' / '氮化铝粉' / 'Ag-Pd 浆料'")
    max_purity_pct: Optional[float] = Field(None, description="可达最高纯度(%)")
    d50_min_um: Optional[float] = Field(None, description="可供 D50 粒径下限(µm)")
    d50_max_um: Optional[float] = Field(None, description="可供 D50 粒径上限(µm)")
    moq: Optional[str] = Field(None, description="最小起订量")
    lead_time: Optional[str] = Field(None, description="交期")
    contact: Optional[str] = Field(None, description="联系方式")
    note: Optional[str] = Field(None, description="备注")

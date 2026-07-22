"""
材料定性与供应链拆解服务(技术工艺第 3 步)。

延续平台思路:Claude 只产出**结构化建议**(陶瓷主体材料候选/电极金属化配方/粉末要求),
允许**联网检索**材料特性、行情与供应商公开信息作为依据(回填可追溯来源);
平台做**确定性**的供应商达标匹配(纯度/粒径数值比较),最终"确认"由人来点。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from ..models.cost import WebSource
from ..models.ir import DesignIR
from ..models.material import (
    MaterialRecommendation, PowderRequirement, SupplierMatch, SupplyEvaluation,
)
from ..time_utils import now_cst_str
from . import llm_client as claude_client

SYSTEM_PROMPT = """你是资深电子陶瓷 / 封装基板材料工程师。给定一个器件的结构化设计意图
(零件、材料、特征、尺寸)与可选补充说明,请完成「材料定性与供应链拆解」,输出结构化建议:

1. **陶瓷主体材料定性**:对比候选主体材料(至少覆盖 氧化铝 Al2O3、氮化铝 AlN,并按器件需要
   补充 氮化硅 Si3N4、氧化铍 BeO 等),从**热导率、介电性能、热膨胀系数(CTE)、机械强度、成本**
   维度给出对比(body_candidates),标注推荐项(recommended)与推荐理由(body_rationale)。
   若器件偏大功率/高散热,应倾向氮化铝等高热导材料并说明权衡。
2. **电极浆料与金属化层配方**:给出电极浆料成分配方(paste:导电相/玻璃料/有机载体及大致 wt%)
   与金属化层结构(layers:如 钼锰法底层 + 化学镀镍 + 镀金,含厚度/工艺),并给出方案理由
   (metallization_rationale)。
3. **粉末纯度/粒径要求**:针对选定主体材料给出粉末的纯度(%)与 D50 粒径(µm)区间要求
   (requirements),作为后续供应商达标判定的基准。
4. **联网检索**:用 web_search 查材料特性区间、当前行情与可能的供应商公开信息作为依据,
   不要臆造精确数字;没有把握的写进 open_questions 并说明假设(assumptions)。
全程用中文填写各字段,调用工具输出结构化 MaterialRecommendation。"""


def _context(ir: Optional[DesignIR], note: str, use_web: bool) -> str:
    lines: List[str] = []
    if ir:
        if ir.device_name:
            lines.append(f"器件/设备名称: {ir.device_name}")
        if ir.parts:
            lines.append("零件构成:")
            for p in ir.parts[:20]:
                seg = f"  - {p.part_id} {p.name}"
                if getattr(p, "material", None) and p.material and p.material.spec:
                    seg += f"(现标注材料: {p.material.spec})"
                if getattr(p, "role", None):
                    seg += f" — {p.role}"
                lines.append(seg)
    if not lines:
        lines.append("(暂无结构化 IR,请基于补充说明与通用工程经验给出建议,并在 open_questions 标注缺失输入)")
    if note and note.strip():
        lines.append(f"\n【用户补充说明(请优先采用)】\n{note.strip()}")
    lines.append(
        "\n请先联网检索相关材料特性/行情/供应商公开信息,再输出结构化 MaterialRecommendation。"
        if use_web else "\n请基于上述项目资料与通用工程知识输出结构化 MaterialRecommendation。"
    )
    return "\n".join(lines)


def recommend(
    ir: Optional[DesignIR] = None, note: str = "", web: bool = True,
) -> MaterialRecommendation:
    use_web = web and claude_client.WEB_SEARCH_AVAILABLE
    content = [
        claude_client.text_block(_context(ir, note, use_web)),
        claude_client.text_block(claude_client.web_search_notice(use_web)),
    ]
    extra_tools = claude_client.web_search_tools(use_web)
    sources: list = []
    rec = claude_client.run(
        SYSTEM_PROMPT, content, MaterialRecommendation,
        extra_tools=extra_tools, sources_out=sources,
    )
    # 合并平台收集到的检索来源(去重)
    have = {s.url for s in rec.search_sources}
    for s in sources:
        if s.get("url") and s["url"] not in have:
            rec.search_sources.append(WebSource(title=s.get("title") or s["url"], url=s["url"]))
            have.add(s["url"])
    return rec


# --------------------------------------------------------------------------- #
# ③ 确定性供应商达标匹配:offered_purity ≥ 要求纯度 且 D50 落在要求区间 -> 达标
# --------------------------------------------------------------------------- #
def _match_requirement(supplier_material: str, reqs: List[PowderRequirement]) -> Optional[PowderRequirement]:
    sm = (supplier_material or "").strip()
    for r in reqs:
        rm = (r.material or "").strip()
        if rm and (rm in sm or sm in rm):
            return r
    return reqs[0] if len(reqs) == 1 else None


def evaluate(requirements: List[PowderRequirement], suppliers: List[dict]) -> SupplyEvaluation:
    matches: List[SupplierMatch] = []
    qualified_count = 0
    for s in suppliers:
        req = _match_requirement(s.get("material", ""), requirements)
        purity = s.get("max_purity_pct")
        d50_min = s.get("d50_min_um")
        d50_max = s.get("d50_max_um")
        gaps: List[str] = []
        qualified = True

        if req is None:
            qualified = False
            gaps.append("无匹配的物料要求,无法判定")
        else:
            if req.purity_pct_min is not None:
                if purity is None:
                    qualified = False
                    gaps.append("供应商未提供纯度数据")
                elif purity < req.purity_pct_min:
                    qualified = False
                    gaps.append(f"纯度 {purity}% < 要求 {req.purity_pct_min}%")
            # 粒径:要求一个 D50 区间,供应商可供区间需与之有交集
            if req.d50_um_min is not None or req.d50_um_max is not None:
                lo = req.d50_um_min if req.d50_um_min is not None else float("-inf")
                hi = req.d50_um_max if req.d50_um_max is not None else float("inf")
                if d50_min is None and d50_max is None:
                    qualified = False
                    gaps.append("供应商未提供 D50 粒径数据")
                else:
                    s_lo = d50_min if d50_min is not None else float("-inf")
                    s_hi = d50_max if d50_max is not None else float("inf")
                    if s_hi < lo or s_lo > hi:
                        qualified = False
                        gaps.append(
                            f"D50 可供 [{d50_min},{d50_max}]µm 与要求 [{req.d50_um_min},{req.d50_um_max}]µm 无交集"
                        )

        if qualified:
            qualified_count += 1
        matches.append(SupplierMatch(
            supplier=s.get("name", "?"),
            material=s.get("material"),
            offered_purity_pct=purity,
            offered_d50_um=d50_min if d50_min is not None else d50_max,
            qualified=qualified,
            gap_notes="; ".join(gaps) if gaps else "满足纯度与粒径要求",
        ))

    conclusion = (
        f"共评估 {len(matches)} 家供应商,其中 {qualified_count} 家达标。"
        + ("" if qualified_count else " 暂无完全达标供应商,建议放宽指标或开发新供应商。")
    )
    return SupplyEvaluation(
        requirements=requirements, matches=matches, conclusion=conclusion,
        evaluated_at=now_cst_str(),
    )

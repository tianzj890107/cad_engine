"""工艺推荐前的工艺库检索。

和零部件检索（component_match）同一个思路：先把**企业库里已经有什么**查清楚，
再让模型在这个基础上排工艺。不这么做的话，模型只能凭通用工艺常识编一条路线，
既对不上车间实有设备，也拿不到标准工时 —— 报出来的工期没有依据。

检索走三层，全程不经模型：
  1. 路线模板：优先用匹配到的库内零部件挂的 default_route_code（同一个零件
     以前怎么做的，就还怎么做）；没有匹配件时按 类别/材料类别/批量 召回打分。
  2. 工序：展开路线的工序序列，取标准准备工时、单件工时模型、默认设备类。
  3. 特征补漏：按零件特征再召回一遍工序，落在路线之外的列为**补充工序**候选；
     一个特征连候选工序都召不到，就是**库内空白**，必须走工艺开发。

第 3 步是这个模块存在的主要理由：路线模板是按类别做的，具体零件多出来的特征
（比如底板上多一圈密封槽）不会自动出现在模板里，不补漏就会被整条漏掉。
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from ..storage import da_db, kb_repo, store
from ..time_utils import now_cst_str

ProgressFn = Optional[Callable[[str], None]]

# 特征 → 兜底零件类别。只在库内匹配不到零部件、拿不到 category 时用。
_FEATURE_CATEGORY = (
    ({"bend", "sheet"}, "钣金件"),
    ({"cylinder", "revolve"}, "回转件"),
    ({"plate", "box"}, "结构件"),
)


def _feature_types(part: dict) -> list[str]:
    kinds = []
    for feature in part.get("features") or []:
        kind = str(feature.get("type") or feature.get("feature_type") or "").strip().lower()
        if kind and kind not in kinds:
            kinds.append(kind)
    return kinds


def _material_category(spec: Optional[str]) -> Optional[str]:
    """按牌号反查物料类别；查不到留空，不猜。"""
    text = str(spec or "").strip()
    if not text:
        return None
    for material in kb_repo.list_materials(keyword=text):
        grade = str(material.get("grade") or "").strip()
        if grade and (grade in text or text in grade):
            return material.get("category")
    return None


def build_query(part: dict, *, match: Optional[dict] = None, batch_size: int = 1) -> dict:
    """确定路线召回的三个维度：零件类别、材料类别、批量。"""
    features = _feature_types(part)
    category = None
    default_route = None
    if match and match.get("matched"):
        component = kb_repo.get_component(match.get("component_code") or "")
        if component:
            category = component.get("category")
            default_route = component.get("default_route_code")
    if not category:
        kinds = set(features)
        for markers, guess in _FEATURE_CATEGORY:
            if kinds & markers:
                category = guess
                break
    material = part.get("material")
    spec = material.get("spec") if isinstance(material, dict) else material
    return {
        "category": category,
        "material_spec": spec,
        "material_category": _material_category(spec),
        "features": features,
        "batch_size": max(1, int(batch_size or 1)),
        "default_route_code": default_route,
    }


# --------------------------------------------------------------------------- #
# 检索
# --------------------------------------------------------------------------- #
def _step_brief(step: Optional[dict]) -> dict:
    step = step or {}
    return {
        "step_code": step.get("step_code"),
        "name": step.get("name"),
        "process_type": step.get("process_type"),
        "category": step.get("category"),
        "setup_min": step.get("setup_min"),
        "unit_min_formula": step.get("unit_min_formula"),
        "yield_rate": step.get("yield_rate"),
        "equipment_class": step.get("default_equipment_class"),
        "is_critical": bool(step.get("is_critical")),
    }


def _equipment_for(class_code: Optional[str]) -> list[dict]:
    if not class_code:
        return []
    return [
        {"equipment_id": item.get("equipment_id"), "name": item.get("name"),
         "model": item.get("model"), "workshop": item.get("workshop")}
        for item in kb_repo.list_equipment(equipment_class=class_code)
    ][:3]


def _material_filter(query: dict, route_row: Optional[dict]) -> Optional[set[str]]:
    """确定补充工序召回的材料口径。取不到就返回 None（宁可不召回，也不乱召回）。"""
    category = query.get("material_category")
    if category:
        return {str(category).lower()}
    # 图纸没写牌号时退用路线模板自己的适用材料 —— 路线是按类别选的，
    # 它的材料范围至少把行业圈对了。
    materials = {str(m).lower() for m in (route_row or {}).get("applicable_material") or []}
    return materials or None


def _applies_to_material(step: dict, materials: set[str]) -> bool:
    # list_process_steps 返回的是原始行，applicable_material 还是 JSON 文本。
    raw = step.get("applicable_material")
    values = raw if isinstance(raw, list) else da_db.decode_json(raw, [])
    applicable = {str(item).lower() for item in values}
    # 没标适用材料的工序视为通用（检测、钳工这类），不因为筛材料被误杀。
    return not applicable or bool(applicable & materials)


def _pick_route(query: dict) -> tuple[Optional[dict], str, float]:
    """选路线。返回 (展开后的路线, 来源说明, 打分)。"""
    if query.get("default_route_code"):
        route = kb_repo.get_route(query["default_route_code"])
        if route:
            return route, "库内同类零部件的默认路线", 1.0
    candidates = kb_repo.recommend_routes(
        category=query.get("category"),
        material_category=query.get("material_category"),
        batch_size=query.get("batch_size"),
    )
    if not candidates:
        return None, "", 0.0
    best = candidates[0]
    return kb_repo.get_route(best["route_code"]), "按类别/材料/批量召回", float(best["score"])


def lookup_part(part: dict, *, match: Optional[dict] = None, batch_size: int = 1,
                progress: ProgressFn = None) -> dict:
    """检索单个零件的工艺库。返回路线 + 补充工序 + 库内空白，不写库。"""
    query = build_query(part, match=match, batch_size=batch_size)
    label = f"{part.get('part_id') or '?'} {part.get('name') or ''}".strip()
    _report(progress, f"检索工艺库：{label}"
                      f"（类别 {query['category'] or '未定'}／材料 {query['material_category'] or '未定'}）")

    route_row, source, score = _pick_route(query)
    steps: list[dict] = []
    if route_row:
        for item in route_row.get("steps") or []:
            brief = _step_brief(item.get("step"))
            brief.update({
                "seq": item.get("seq"),
                "is_optional": bool(item.get("is_optional")),
                "condition_expr": item.get("condition_expr"),
                "equipment": _equipment_for(brief["equipment_class"]),
            })
            steps.append(brief)
        _report(progress, f"  ↳ 命中路线 {route_row['route_code']} {route_row.get('name') or ''}"
                          f"（{source}，{len(steps)} 道工序）")
    else:
        _report(progress, "  ↳ 库内无适用路线模板，按工序逐道召回")

    # 特征补漏：路线模板按类别做，具体零件多出来的特征不会自动进模板。
    in_route = {item["step_code"] for item in steps}
    materials = _material_filter(query, route_row)
    extra: list[dict] = []
    covered: list[str] = []
    notes: list[str] = []
    if materials is None:
        # applicable_feature 是很粗的几何标签 —— "plate" 同时挂在铣削、折弯、光刻、
        # 极片涂布上。没有材料类别就没法把行业分开，硬召回只会给出一堆荒唐候选，
        # 不如明说"先确认牌号"。
        notes.append("材料类别未知，已跳过补充工序召回；请先确认材料牌号")
    else:
        for kind in query["features"]:
            hits = [step for step in kb_repo.steps_for_features([kind])
                    if _applies_to_material(step, materials)]
            if not hits:
                continue
            covered.append(kind)
            for step in hits:
                if step["step_code"] in in_route or any(e["step_code"] == step["step_code"] for e in extra):
                    continue
                brief = _step_brief(step)
                brief["reason"] = f"特征 {kind} 需要，但路线模板未覆盖"
                brief["equipment"] = _equipment_for(brief["equipment_class"])
                extra.append(brief)
    gaps = [kind for kind in query["features"] if kind not in covered] if materials is not None else []

    if extra:
        _report(progress, f"  ↳ 补充工序候选 {len(extra)} 道："
                          + "、".join(item["name"] or item["step_code"] for item in extra[:4]))
    if gaps:
        _report(progress, f"  ↳ 库内空白：特征 {'、'.join(gaps)} 没有对应工序，需工艺开发")
    for note in notes:
        _report(progress, f"  ↳ {note}")

    return {
        "generated_at": now_cst_str(),
        "part_id": part.get("part_id"),
        "part_name": part.get("name") or "",
        "query": query,
        "route": {
            "route_code": route_row["route_code"],
            "name": route_row.get("name"),
            "summary": route_row.get("summary"),
            "source": source,
            "score": round(score, 3),
            "steps": steps,
        } if route_row else None,
        "extra_steps": extra,
        "feature_gaps": gaps,
        "notes": notes,
        "summary": {
            "route_steps": len(steps),
            "extra_steps": len(extra),
            "covered_features": len(covered),
            "uncovered_features": len(gaps),
            "library_steps": len(kb_repo.list_process_steps()),
        },
    }


def _report(progress: ProgressFn, message: str) -> None:
    if progress:
        try:
            progress(message)
        except Exception:      # 进度上报失败不能影响检索本身
            pass


# --------------------------------------------------------------------------- #
# 给模型看的摘要
# --------------------------------------------------------------------------- #
def as_prompt(report: dict) -> str:
    """把检索结果压成一段提示词。

    只给编号、名称、标准工时、设备类这些**可核对的事实**。模型该做的是在这些
    既有资源上排序取舍，而不是自己发明工序编号。
    """
    lines: list[str] = []
    route = report.get("route")
    if route:
        lines.append(f"库内工艺路线模板 {route['route_code']}（{route.get('name') or ''}，"
                     f"{route.get('source') or ''}）：")
        for step in route.get("steps") or []:
            lines.append(
                f"  {step.get('seq')} {step.get('step_code')} {step.get('name')}"
                f" · 类型={step.get('process_type')}"
                f" · 准备={step.get('setup_min')}min"
                f" · 单件工时模型={step.get('unit_min_formula') or '未定义'}"
                f" · 设备类={step.get('equipment_class') or '未指定'}"
                + ("（可选）" if step.get("is_optional") else "")
            )
    else:
        lines.append("库内没有适用的工艺路线模板。")
    if report.get("extra_steps"):
        lines.append("特征要求、但路线模板未覆盖的库内工序（按需插入）：")
        for step in report["extra_steps"]:
            lines.append(f"  {step.get('step_code')} {step.get('name')} · {step.get('reason')}")
    if report.get("feature_gaps"):
        lines.append("库内空白（没有对应工序，需按新工艺开发处理，并在风险里说明）："
                     + "、".join(report["feature_gaps"]))
    for note in report.get("notes") or []:
        lines.append(f"检索说明：{note}")
    lines.append("要求：优先复用上述工序编号与标准工时；确需库外工序时必须显式标注"
                 "「库外新增」并说明理由，不得编造库内不存在的工序编号或设备编号。")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 持久化
# --------------------------------------------------------------------------- #
def save_report(project_id: str, part_id: str, report: dict) -> None:
    store.save_process_lookup(project_id, part_id, report)

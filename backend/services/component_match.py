"""图纸拆解后的零部件库检索。

拆解出零件只是第一步。工艺评估真正关心的是：**这些零件里，哪些库里已经有、
可以直接复用或改制，哪些是全新的、必须走完整工艺开发**。这个判断直接决定
工期与成本，因此在拆解流程里就做掉，而不是留给人事后一个个去翻库。

检索走知识库的三级漏斗（storage/kb_repo.recommend_components）：
包络粗筛 → 参数精筛 → 特征相似。**打分全程不经模型**，同一份 IR 反复跑
结果一致 —— 否则同一张图两次评估会给出不同的复用建议。

产出分三档：
  reuse   可直接复用：关键参数全部落在库内允差里
  modify  可改制：结构同类但有尺寸/特征差异，附差异说明
  new     未匹配：库里没有同类件，按新制评估
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

from ..storage import kb_repo, store
from ..time_utils import now_cst_str

# 可直接复用的门槛。低于它但仍被漏斗召回的，算"可改制"候选。
REUSE_SCORE = 0.90
MODIFY_SCORE = 0.55
CANDIDATE_LIMIT = 3

ProgressFn = Optional[Callable[[str], None]]


# --------------------------------------------------------------------------- #
# 从 IR 零件构造检索输入
# --------------------------------------------------------------------------- #
def build_query(part: dict) -> dict:
    """把 IR 里的零件转成漏斗需要的 {features, params}。

    params 由特征推导：库内零部件的关键参数用的就是这套键（length/width/
    thickness/diameter/hole_diameter），两边对齐才能做参数精筛。
    """
    features = list(part.get("features") or [])
    params: dict[str, Any] = {}
    for feature in features:
        kind = str(feature.get("type") or feature.get("feature_type") or "").lower()
        if kind == "plate":
            _put(params, "length", feature.get("length"))
            _put(params, "width", feature.get("width"))
            _put(params, "thickness", feature.get("thickness"))
        elif kind == "box":
            _put(params, "length", feature.get("length"))
            _put(params, "width", feature.get("width"))
            _put(params, "height", feature.get("height"))
        elif kind == "cylinder":
            _put(params, "diameter", feature.get("diameter"))
            _put(params, "height", feature.get("height"))
        elif kind in ("hole", "hole_pattern"):
            _put(params, "hole_diameter", feature.get("diameter"))
    material = part.get("material")
    spec = material.get("spec") if isinstance(material, dict) else material
    return {
        "name": part.get("name") or "",
        "features": features,
        "params": params,
        "material_spec": spec,
        "material_code": _material_code_for(spec),
    }


def _put(target: dict, key: str, value: Any) -> None:
    """同名参数取第一个非空值：一个零件可能有多个同类特征。"""
    if key not in target and isinstance(value, (int, float)) and value:
        target[key] = float(value)


def _material_code_for(spec: Optional[str]) -> Optional[str]:
    """按牌号在物料库里反查编码；查不到就留空，不猜。"""
    text = str(spec or "").strip()
    if not text:
        return None
    for material in kb_repo.list_materials(keyword=text):
        grade = str(material.get("grade") or "").strip()
        if grade and (grade in text or text in grade):
            return material["material_code"]
    return None


# --------------------------------------------------------------------------- #
# 检索
# --------------------------------------------------------------------------- #
def match_part(part: dict) -> dict:
    """检索单个零件。返回结论 + 候选，不写库。"""
    query = build_query(part)
    candidates = kb_repo.recommend_components(query, limit=CANDIDATE_LIMIT)
    top = candidates[0] if candidates else None
    score = float(top["score"]) if top else 0.0

    if top and (score >= REUSE_SCORE or top["match_type"] == "exact"):
        decision, label = "reuse", "可直接复用"
    elif top and score >= MODIFY_SCORE:
        decision, label = "modify", "可改制"
    else:
        decision, label = "new", "未匹配（按新制评估）"
        # 漏斗的召回门槛比"可改制"低，弱候选照样会被带回来。判为未匹配后就不能
        # 再挂着这个编码 —— 否则界面上会出现一条并不成立的命中记录。候选仍保留
        # 在 candidates 里，供人工翻查。
        top = None

    return {
        "part_id": part.get("part_id"),
        "part_name": part.get("name") or "",
        "material_spec": query["material_spec"],
        "query_params": query["params"],
        "decision": decision,
        "decision_label": label,
        "matched": decision != "new",
        "component_id": top["component_id"] if top else None,
        "component_code": top["component_code"] if top else None,
        "component_name": top["name"] if top else None,
        "score": round(score, 4),
        "match_type": top["match_type"] if top else "none",
        "gap_notes": top.get("gap_notes") if top else "",
        "candidates": candidates,
    }


def match_project(project_id: str, ir: dict, *, progress: ProgressFn = None) -> dict:
    """对整份 IR 逐件检索零部件库，产出可复用/可改制/未匹配三档报告。"""
    parts = list(ir.get("parts") or [])
    library_size = len(kb_repo.list_components(limit=1000))
    _report(progress, f"零部件库检索开始：{len(parts)} 个零件 × 库内 {library_size} 条记录")

    items: list[dict] = []
    for index, part in enumerate(parts, start=1):
        label = f"{part.get('part_id') or '?'} {part.get('name') or ''}".strip()
        _report(progress, f"检索零部件库（{index}/{len(parts)}）：{label}")
        # 把查询条件本身也播出去：用户要能看出"是拿哪几个尺寸去比的"，
        # 只报结论的话，匹配不上时根本无从判断是库里没有还是条件提错了。
        query = build_query(part)
        criteria = "、".join(f"{key}={value:g}" for key, value in query["params"].items())
        _report(progress, f"  · 查询条件：{criteria or '无可用尺寸参数'}"
                          + (f"，材料 {query['material_spec']}" if query["material_spec"] else ""))
        result = match_part(part)
        items.append(result)
        if result["matched"]:
            _report(progress, f"  ↳ 命中 {result['component_code']} {result['component_name'] or ''}"
                              f"（{result['decision_label']}，匹配度 {result['score']:.0%}）")
            if result.get("gap_notes"):
                _report(progress, f"    差异：{result['gap_notes']}")
        else:
            _report(progress, "  ↳ 库内无同类件，按新制评估")

    reuse = [item for item in items if item["decision"] == "reuse"]
    modify = [item for item in items if item["decision"] == "modify"]
    new = [item for item in items if item["decision"] == "new"]
    report = {
        "generated_at": now_cst_str(),
        "library_size": library_size,
        "thresholds": {"reuse": REUSE_SCORE, "modify": MODIFY_SCORE},
        "items": items,
        "summary": {
            "total": len(items),
            "reuse": len(reuse),
            "modify": len(modify),
            "new": len(new),
            "matched": len(reuse) + len(modify),
        },
    }
    _report(progress, f"零部件库检索完成：可复用 {len(reuse)}、可改制 {len(modify)}、"
                      f"未匹配 {len(new)}")
    return report


def _report(progress: ProgressFn, message: str) -> None:
    if progress:
        try:
            progress(message)
        except Exception:      # 进度上报失败不能影响检索本身
            pass


# --------------------------------------------------------------------------- #
# 持久化
# --------------------------------------------------------------------------- #
def save_report(project_id: str, report: dict) -> None:
    store.save_component_match(project_id, report)
    _persist_to_da(project_id, report)


def _persist_to_da(project_id: str, report: dict) -> None:
    """把匹配结果写进 DA 的 wip_component_match（尽力而为）。

    DA 库当前与主流程并行，尚未接管业务读写；写失败不能让拆解任务失败，
    因此这里吞掉异常，只在审计里留一条。
    """
    try:
        from ..storage import da_repo

        ir = store.load_ir(project_id) or {}
        if not ir.get("parts"):
            return
        ir_id = da_repo.save_design_ir(project_id, ir, model_name="platform-parse")
        for item in report.get("items") or []:
            da_repo.save_component_matches(ir_id, item["part_id"], item.get("candidates") or [])
    except Exception as exc:                                    # pragma: no cover - 依赖 DA 库状态
        store.audit(project_id, "component_match_da_skip", {"reason": str(exc)[:200]})

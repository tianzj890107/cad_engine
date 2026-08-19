"""知识库(kb_*)的读写与检索。

包含四类数据源的访问入口,以及两处**确定性计算**:
  - recommend_components(): 零部件推荐的三级漏斗(包络粗筛 -> 参数精筛 -> 特征相似),
    分数完全由本模块算出,不经模型 —— 否则同一份图纸两次评估会得到不同的推荐排序。
  - current_price() / effective_rate() / effective_factor(): 按测算时点取价与费率,
    成本明细回填这些主键后,报告里的每个数字都能复现。
"""
from __future__ import annotations

import uuid
from typing import Any, Iterable, Optional, Sequence

from . import da_db as db
from . import kb_library as lib

# 三级漏斗权重。包络只做粗筛(权重最低),特征相似最能反映"能不能照着做出来"。
WEIGHT_ENVELOPE = 0.25
WEIGHT_PARAM = 0.35
WEIGHT_FEATURE = 0.40
# 粗筛放行的包络偏差;超出即认为不是同一类零件。
ENVELOPE_TOLERANCE = 0.20
MATCH_THRESHOLD = 0.35


def _uid(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


# ========================================================================== #
# 零部件
# ========================================================================== #
def save_component(
    component: dict,
    *,
    params: Optional[Sequence[dict]] = None,
    features: Optional[Sequence[dict]] = None,
    create_dirs: bool = True,
) -> str:
    """新增/更新一个可制造零部件(含参数与特征)。返回 component_id。"""
    code = str(component["component_code"]).strip()
    existing = db.query_one(
        "SELECT component_id FROM kb_component WHERE component_code = ?", (code,)
    )
    component_id = component.get("component_id") or (
        existing["component_id"] if existing else _uid("CMP")
    )
    row = dict(component)
    row["component_id"] = component_id
    row["component_code"] = code
    row.setdefault("created_at", db.now())
    row["updated_at"] = db.now()
    db.upsert("kb_component", row, keys=("component_id",))

    if params is not None:
        db.execute("DELETE FROM kb_component_param WHERE component_id = ?", (component_id,))
        for p in params:
            db.insert("kb_component_param", {**p, "component_id": component_id})
    if features is not None:
        db.execute("DELETE FROM kb_component_feature WHERE component_id = ?", (component_id,))
        for seq, f in enumerate(features, start=1):
            db.insert("kb_component_feature", {"seq": seq, **f, "component_id": component_id})
    if create_dirs:
        lib.component_dir(code, create=True)
    return component_id


def get_component(ref: str) -> Optional[dict]:
    """按 component_id 或 component_code 取零部件全貌(参数/特征/图纸)。"""
    row = db.query_one(
        "SELECT * FROM kb_component WHERE component_id = ? OR component_code = ?", (ref, ref)
    )
    if not row:
        return None
    cid = row["component_id"]
    row["tags"] = db.decode_json(row.get("tags"), [])
    row["params"] = db.query(
        "SELECT * FROM kb_component_param WHERE component_id = ? ORDER BY is_key DESC, param_key", (cid,)
    )
    row["features"] = db.query(
        "SELECT * FROM kb_component_feature WHERE component_id = ? ORDER BY seq", (cid,)
    )
    row["drawings"] = db.query(
        "SELECT * FROM kb_component_drawing WHERE component_id = ? ORDER BY drawing_kind, rev", (cid,)
    )
    return row


def list_components(
    *,
    category: Optional[str] = None,
    lifecycle: str = "active",
    keyword: str = "",
    limit: int = 200,
) -> list[dict]:
    sql = "SELECT * FROM kb_component WHERE 1=1"
    args: list[Any] = []
    if lifecycle:
        sql += " AND lifecycle = ?"
        args.append(lifecycle)
    if category:
        sql += " AND category = ?"
        args.append(category)
    if keyword:
        sql += " AND (name LIKE ? OR component_code LIKE ? OR spec_summary LIKE ?)"
        args += [f"%{keyword}%"] * 3
    sql += " ORDER BY reuse_count DESC, component_code LIMIT ?"
    args.append(limit)
    return db.query(sql, args)


# -------------------------------------------------------------------------- #
# 图库:文件夹 -> 索引
# -------------------------------------------------------------------------- #
def sync_component_drawings(component_code: str) -> dict:
    """扫描该零部件的图库文件夹,把文件登记/更新到 kb_component_drawing。

    文件夹是权威来源:目录里已删除的文件会从索引中移除,新放入的自动登记,
    每类图纸的最新版本目录标记为 is_current。
    """
    comp = db.query_one(
        "SELECT component_id FROM kb_component WHERE component_code = ?", (component_code,)
    )
    if not comp:
        raise LookupError(f"零部件未登记: {component_code}")
    cid = comp["component_id"]

    scanned = lib.scan_component_files(component_code)
    seen: set[str] = set()
    added = updated = 0
    for item in scanned:
        seen.add(item["file_path"])
        kind, rev = item["drawing_kind"], item["rev"]
        is_current = 1 if rev == (lib.latest_rev(component_code, kind) or rev) else 0
        row = {**item, "component_id": cid, "is_current": is_current, "uploaded_at": db.now()}
        exists = db.query_one(
            "SELECT drawing_id FROM kb_component_drawing "
            "WHERE component_id = ? AND file_path = ?", (cid, item["file_path"])
        )
        if exists:
            db.upsert(
                "kb_component_drawing",
                {**row, "drawing_id": exists["drawing_id"]},
                keys=("drawing_id",),
            )
            updated += 1
        else:
            db.insert("kb_component_drawing", row)
            added += 1

    stale = [
        r["drawing_id"]
        for r in db.query("SELECT drawing_id, file_path FROM kb_component_drawing WHERE component_id = ?", (cid,))
        if r["file_path"] not in seen
    ]
    for drawing_id in stale:
        db.execute("DELETE FROM kb_component_drawing WHERE drawing_id = ?", (drawing_id,))
    return {"component_code": component_code, "added": added, "updated": updated, "removed": len(stale)}


def sync_all_drawings() -> list[dict]:
    """全量扫描图库目录。未在库中登记的目录会被跳过并报告。"""
    results: list[dict] = []
    for code in lib.list_component_codes():
        try:
            results.append(sync_component_drawings(code))
        except LookupError as exc:
            results.append({"component_code": code, "error": str(exc)})
    return results


# ========================================================================== #
# 零部件推荐:三级漏斗(确定性)
# ========================================================================== #
def recommend_components(part: dict, *, limit: int = 5, category: Optional[str] = None) -> list[dict]:
    """给一个拆解出的零件推荐可复用的库内零部件。

    part 形如 models/ir.py::Part 的 dict:{name, features: [...], material: {...}},
    另可给 params: {param_key: value} 覆盖/补充参数比对。
    """
    features = list(part.get("features") or [])
    envelope = envelope_of(features)
    part_params = dict(part.get("params") or {})
    material_code = part.get("material_code")

    candidates = list_components(category=category, limit=500)
    scored: list[dict] = []
    for comp in candidates:
        env = _envelope_score(envelope, comp)
        if env == 0.0 and envelope:
            continue  # 粗筛淘汰:包络差距过大
        param_score, gaps = _param_score(comp["component_id"], part_params)
        feature_score = _feature_score(comp["component_id"], features)
        score = (
            WEIGHT_ENVELOPE * env
            + WEIGHT_PARAM * param_score
            + WEIGHT_FEATURE * feature_score
        )
        if material_code and comp.get("default_material_code") == material_code:
            score = min(1.0, score + 0.05)   # 同材料的小幅加成,不改变量级
        if score < MATCH_THRESHOLD:
            continue
        scored.append({
            "component_id": comp["component_id"],
            "component_code": comp["component_code"],
            "name": comp["name"],
            "score": round(score, 4),
            "envelope_score": round(env, 4),
            "param_score": round(param_score, 4),
            "feature_score": round(feature_score, 4),
            "match_type": _match_type(score, param_score, feature_score),
            "gap_notes": "; ".join(gaps) if gaps else "",
        })
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:limit]


def envelope_of(features: Iterable[dict]) -> tuple[float, float, float]:
    """由特征估算零件外形包络(mm,降序三元组)。无可用尺寸时返回 (0,0,0)。"""
    dims: list[float] = []
    for f in features or []:
        ftype = str(f.get("type") or f.get("feature_type") or "").lower()
        if ftype == "plate":
            dims = _max_dims(dims, [f.get("length"), f.get("width"), f.get("thickness")])
        elif ftype == "box":
            dims = _max_dims(dims, [f.get("length"), f.get("width"), f.get("height")])
        elif ftype == "cylinder":
            d = f.get("diameter")
            dims = _max_dims(dims, [d, d, f.get("height")])
    if not dims:
        return (0.0, 0.0, 0.0)
    ordered = sorted((float(d) for d in dims if d), reverse=True)[:3]
    while len(ordered) < 3:
        ordered.append(0.0)
    return tuple(ordered)  # type: ignore[return-value]


def _max_dims(current: list[float], candidate: Sequence[Any]) -> list[float]:
    values = [float(v) for v in candidate if isinstance(v, (int, float)) and v]
    if not values:
        return current
    # 取体积最大的那组特征作为整体包络的代表。
    if not current or _volume(values) > _volume(current):
        return values
    return current


def _volume(dims: Sequence[float]) -> float:
    v = 1.0
    for d in dims:
        v *= max(float(d), 1e-6)
    return v


def _envelope_score(envelope: tuple[float, float, float], comp: dict) -> float:
    """包络粗筛。**两个及以上**维度超差才淘汰,只差一个维度的仍放行(该维度计 0 分)。

    为什么不是一超差就淘汰:钣金件的库内包络记的是**成形后**尺寸(如折弯后高 45mm),
    而图纸拆解给出的 plate 特征是**展开料厚**(2mm)。这两者在第三个维度上必然对不上,
    但前两维完全一致时,它恰恰就是"要不要折弯"的改制判断,而不是"不是同一个零件"。
    """
    if not any(envelope):
        return 0.5  # 零件没给尺寸:不加分也不淘汰
    comp_dims = sorted(
        [float(comp.get(k) or 0) for k in ("envelope_l", "envelope_w", "envelope_h")], reverse=True
    )
    if not any(comp_dims):
        return 0.5
    scores: list[float] = []
    over_tolerance = 0
    for a, b in zip(envelope, comp_dims):
        if a <= 0 and b <= 0:
            continue
        if a <= 0 or b <= 0:
            over_tolerance += 1
            scores.append(0.0)
            continue
        deviation = abs(a - b) / max(a, b)
        if deviation > ENVELOPE_TOLERANCE:
            over_tolerance += 1
            scores.append(0.0)
            continue
        scores.append(1.0 - deviation / ENVELOPE_TOLERANCE)
    if over_tolerance >= 2:
        return 0.0
    return sum(scores) / len(scores) if scores else 0.5


def _param_score(component_id: str, part_params: dict) -> tuple[float, list[str]]:
    """按库内关键参数逐项比对:落在允差内计满分,超出按偏差衰减。"""
    rows = db.query(
        "SELECT param_key, param_name, value_num, value_text, unit, tol_lower, tol_upper, is_key "
        "FROM kb_component_param WHERE component_id = ?", (component_id,)
    )
    if not rows or not part_params:
        return (0.5, [])
    total_weight = 0.0
    earned = 0.0
    gaps: list[str] = []
    for row in rows:
        actual = part_params.get(row["param_key"])
        if actual is None:
            continue
        weight = 2.0 if row["is_key"] else 1.0
        total_weight += weight
        expected = row["value_num"]
        if expected is None:
            hit = str(actual).strip() == str(row["value_text"] or "").strip()
            earned += weight if hit else 0.0
            if not hit:
                gaps.append(f"{row['param_key']}: 库内 {row['value_text']} / 图纸 {actual}")
            continue
        try:
            actual_num = float(actual)
        except (TypeError, ValueError):
            gaps.append(f"{row['param_key']}: 图纸值非数值 {actual}")
            continue
        lower = expected - (row["tol_lower"] if row["tol_lower"] is not None else 0.0)
        upper = expected + (row["tol_upper"] if row["tol_upper"] is not None else 0.0)
        if lower <= actual_num <= upper:
            earned += weight
            continue
        span = max(abs(expected), 1e-6)
        deviation = min(abs(actual_num - expected) / span, 1.0)
        earned += weight * (1.0 - deviation)
        gaps.append(f"{row['param_key']}: 库内 {expected}{row['unit'] or ''} / 图纸 {actual_num}")
    if total_weight == 0:
        return (0.5, gaps)
    return (earned / total_weight, gaps)


def _feature_score(component_id: str, features: Sequence[dict]) -> float:
    """特征相似 = 类型覆盖度 × 0.5 + 同类特征尺寸接近度 × 0.5。

    用"覆盖度"而非对称的 Jaccard:判断能否复用时,库内零件**多出**的特征
    (如额外的倒角)远不如**缺失**图纸要求的特征致命,故多余特征只轻度扣分。
    """
    rows = db.query(
        "SELECT * FROM kb_component_feature WHERE component_id = ? ORDER BY seq", (component_id,)
    )
    if not rows or not features:
        return 0.0
    kb_types = {r["feature_type"] for r in rows}
    part_types = {
        str(f.get("type") or f.get("feature_type") or "").lower() for f in features
    } - {""}
    if not part_types or not kb_types:
        return 0.0
    shared = kb_types & part_types
    coverage = len(shared) / len(part_types)
    extra = len(kb_types - part_types) / len(kb_types)
    type_score = coverage * (1.0 - 0.25 * extra)

    dim_scores: list[float] = []
    for ftype in shared:
        kb_first = next(r for r in rows if r["feature_type"] == ftype)
        part_first = next(
            f for f in features
            if str(f.get("type") or f.get("feature_type") or "").lower() == ftype
        )
        dim_scores.append(_dim_closeness(kb_first, part_first))
    dim = sum(dim_scores) / len(dim_scores) if dim_scores else 0.0
    return 0.5 * type_score + 0.5 * dim


_DIM_KEYS = ("length", "width", "thickness", "height", "diameter", "radius", "distance")


def _dim_closeness(kb_feature: dict, part_feature: dict) -> float:
    scores: list[float] = []
    for key in _DIM_KEYS:
        a, b = kb_feature.get(key), part_feature.get(key)
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or not a or not b:
            continue
        scores.append(max(0.0, 1.0 - abs(a - b) / max(abs(a), abs(b))))
    return sum(scores) / len(scores) if scores else 0.5


def _match_type(score: float, param_score: float, feature_score: float) -> str:
    if score >= 0.95 and param_score >= 0.98:
        return "exact"
    if param_score >= 0.85:
        return "param_near"
    if feature_score >= 0.6:
        return "feature_similar"
    return "none"


# ========================================================================== #
# 物料 & 价格
# ========================================================================== #
def save_material(material: dict, *, properties: Optional[Sequence[dict]] = None) -> str:
    row = dict(material)
    row.setdefault("created_at", db.now())
    row["updated_at"] = db.now()
    db.upsert("kb_material", row, keys=("material_code",))
    code = row["material_code"]
    if properties is not None:
        db.execute("DELETE FROM kb_material_property WHERE material_code = ?", (code,))
        for p in properties:
            db.insert("kb_material_property", {**p, "material_code": code})
    return code


def get_material(material_code: str) -> Optional[dict]:
    row = db.query_one("SELECT * FROM kb_material WHERE material_code = ?", (material_code,))
    if not row:
        return None
    row["properties"] = db.query(
        "SELECT * FROM kb_material_property WHERE material_code = ? ORDER BY prop_key", (material_code,)
    )
    return row


def list_materials(*, category: Optional[str] = None, keyword: str = "") -> list[dict]:
    sql = "SELECT * FROM kb_material WHERE status = 'active'"
    args: list[Any] = []
    if category:
        sql += " AND category = ?"
        args.append(category)
    if keyword:
        sql += " AND (name LIKE ? OR grade LIKE ? OR material_code LIKE ?)"
        args += [f"%{keyword}%"] * 3
    return db.query(sql + " ORDER BY material_code", args)


def add_material_price(price: dict) -> int:
    row = dict(price)
    row.setdefault("valid_from", db.now())
    row.setdefault("created_at", db.now())
    return db.insert("kb_material_price", row)


def current_price(material_code: str, *, at: Optional[str] = None,
                  price_type: Optional[str] = None) -> Optional[dict]:
    """取指定时点有效的价格。成本测算必须带上返回的 price_id 以便复现。

    排序口径:**最新的价格优先**,同一天的多条再按可信度取。
    不能让可信度压过时效 —— 否则一条 confidence=1.0 的年初合同价会永远盖住
    半年后的最新行情,材料涨跌完全反映不到测算里。
    要锁定某一类价格(如只认合同价),显式传 price_type。
    """
    moment = at or db.now()
    sql = (
        "SELECT * FROM kb_material_price WHERE material_code = ? "
        "AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)"
    )
    args: list[Any] = [material_code, moment, moment]
    if price_type:
        sql += " AND price_type = ?"
        args.append(price_type)
    sql += " ORDER BY valid_from DESC, confidence DESC LIMIT 1"
    return db.query_one(sql, args)


# ========================================================================== #
# 费率 & 系数
# ========================================================================== #
def effective_rate(rate_type: str, *, scope_type: str = "global", scope_ref: Optional[str] = None,
                   at: Optional[str] = None) -> Optional[dict]:
    """按作用域取费率;指定作用域没有时回退到 global。"""
    moment = at or db.now()
    row = db.query_one(
        "SELECT * FROM kb_cost_rate WHERE rate_type = ? AND scope_type = ? "
        "AND (scope_ref = ? OR ? IS NULL) "
        "AND effective_from <= ? AND (effective_to IS NULL OR effective_to > ?) "
        "ORDER BY effective_from DESC LIMIT 1",
        (rate_type, scope_type, scope_ref, scope_ref, moment, moment),
    )
    if row or scope_type == "global":
        return row
    return effective_rate(rate_type, scope_type="global", at=moment)


def effective_factor(factor_type: str, *, at: Optional[str] = None,
                     scope: Optional[str] = None) -> Optional[dict]:
    moment = at or db.now()
    sql = (
        "SELECT * FROM kb_cost_factor WHERE factor_type = ? "
        "AND effective_from <= ? AND (effective_to IS NULL OR effective_to > ?)"
    )
    args: list[Any] = [factor_type, moment, moment]
    if scope:
        sql += " AND (applicable_scope = ? OR applicable_scope IS NULL)"
        args.append(scope)
    return db.query_one(sql + " ORDER BY effective_from DESC LIMIT 1", args)


def save_cost_rate(rate: dict) -> str:
    row = dict(rate)
    row.setdefault("effective_from", db.now())
    db.upsert("kb_cost_rate", row, keys=("rate_code",))
    return row["rate_code"]


def save_cost_factor(factor: dict) -> str:
    row = dict(factor)
    row.setdefault("effective_from", db.now())
    db.upsert("kb_cost_factor", row, keys=("factor_code",))
    return row["factor_code"]


# ========================================================================== #
# 工艺步骤 / 路线 / 设备
# ========================================================================== #
def save_process_step(step: dict, *, params: Optional[Sequence[dict]] = None) -> str:
    row = dict(step)
    row.setdefault("created_at", db.now())
    row.setdefault("effective_from", db.now())
    row["updated_at"] = db.now()
    db.upsert("kb_process_step", row, keys=("step_code",))
    code = row["step_code"]
    if params is not None:
        db.execute("DELETE FROM kb_process_param_template WHERE step_code = ?", (code,))
        for p in params:
            db.insert("kb_process_param_template", {**p, "step_code": code})
    return code


def get_process_step(step_code: str) -> Optional[dict]:
    row = db.query_one("SELECT * FROM kb_process_step WHERE step_code = ?", (step_code,))
    if not row:
        return None
    row["applicable_material"] = db.decode_json(row.get("applicable_material"), [])
    row["applicable_feature"] = db.decode_json(row.get("applicable_feature"), [])
    row["quality_items"] = db.decode_json(row.get("quality_items"), [])
    row["param_templates"] = db.query(
        "SELECT * FROM kb_process_param_template WHERE step_code = ? ORDER BY param_key", (step_code,)
    )
    return row


def list_process_steps(*, process_type: Optional[str] = None,
                       category: Optional[str] = None) -> list[dict]:
    sql = "SELECT * FROM kb_process_step WHERE status = 'active'"
    args: list[Any] = []
    if process_type:
        sql += " AND process_type = ?"
        args.append(process_type)
    if category:
        sql += " AND category = ?"
        args.append(category)
    return db.query(sql + " ORDER BY step_code", args)


def steps_for_features(feature_types: Iterable[str]) -> list[dict]:
    """按特征类型召回候选工序(applicable_feature 命中即可)。"""
    wanted = {str(t).lower() for t in feature_types if t}
    if not wanted:
        return []
    hits = []
    for step in list_process_steps():
        applicable = {
            str(x).lower() for x in db.decode_json(step.get("applicable_feature"), [])
        }
        if applicable & wanted:
            hits.append(step)
    return hits


def save_route(route: dict, steps: Optional[Sequence[dict]] = None) -> str:
    row = dict(route)
    row.setdefault("created_at", db.now())
    row["updated_at"] = db.now()
    db.upsert("kb_process_route", row, keys=("route_code",))
    code = row["route_code"]
    if steps is not None:
        db.execute("DELETE FROM kb_process_route_step WHERE route_code = ?", (code,))
        for item in steps:
            db.insert("kb_process_route_step", {**item, "route_code": code})
    return code


def get_route(route_code: str, *, expand: bool = True) -> Optional[dict]:
    row = db.query_one("SELECT * FROM kb_process_route WHERE route_code = ?", (route_code,))
    if not row:
        return None
    row["applicable_material"] = db.decode_json(row.get("applicable_material"), [])
    steps = db.query(
        "SELECT * FROM kb_process_route_step WHERE route_code = ? ORDER BY seq", (route_code,)
    )
    for item in steps:
        item["depends_on"] = db.decode_json(item.get("depends_on"), [])
        item["param_override"] = db.decode_json(item.get("param_override"), {})
        if expand:
            item["step"] = get_process_step(item["step_code"])
    row["steps"] = steps
    return row


def recommend_routes(*, category: Optional[str] = None, material_category: Optional[str] = None,
                     batch_size: Optional[int] = None) -> list[dict]:
    """按零件类别/材料类别/批量召回工艺路线模板,最匹配的排前面。"""
    routes = db.query("SELECT * FROM kb_process_route WHERE status = 'active' ORDER BY route_code")
    scored: list[dict] = []
    for route in routes:
        score = 0.0
        if category and route.get("applicable_category") == category:
            score += 0.5
        materials = {str(m).lower() for m in db.decode_json(route.get("applicable_material"), [])}
        if material_category and material_category.lower() in materials:
            score += 0.4
        if batch_size is not None:
            low = route.get("batch_min")
            high = route.get("batch_max")
            if (low is None or batch_size >= low) and (high is None or batch_size <= high):
                score += 0.1
        if score > 0:
            scored.append({**route, "score": round(score, 3)})
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored


def save_equipment(equipment: dict) -> str:
    row = dict(equipment)
    row.setdefault("equipment_id", _uid("EQ"))
    row["updated_at"] = db.now()
    db.upsert("kb_equipment", row, keys=("equipment_id",))
    return row["equipment_id"]


def list_equipment(*, equipment_class: Optional[str] = None) -> list[dict]:
    sql = "SELECT * FROM kb_equipment WHERE status = 'active'"
    args: list[Any] = []
    if equipment_class:
        sql += " AND equipment_class = ?"
        args.append(equipment_class)
    return db.query(sql + " ORDER BY name", args)


# ========================================================================== #
# 供应商
# ========================================================================== #
def save_supplier(supplier: dict, *, capabilities: Optional[Sequence[dict]] = None) -> str:
    row = dict(supplier)
    row.setdefault("supplier_id", _uid("SUP"))
    row["updated_at"] = db.now()
    db.upsert("kb_supplier", row, keys=("supplier_id",))
    sid = row["supplier_id"]
    if capabilities is not None:
        db.execute("DELETE FROM kb_supplier_capability WHERE supplier_id = ?", (sid,))
        for cap in capabilities:
            db.insert("kb_supplier_capability", {**cap, "supplier_id": sid})
    return sid


def match_suppliers(requirement: dict) -> list[dict]:
    """按粉末要求做确定性达标判定(对齐 models/material.py::SupplierMatch)。"""
    material_code = requirement.get("material_code")
    material_name = requirement.get("material") or requirement.get("material_name")
    sql = (
        "SELECT c.*, s.name AS supplier FROM kb_supplier_capability c "
        "JOIN kb_supplier s ON s.supplier_id = c.supplier_id WHERE s.status = 'active'"
    )
    args: list[Any] = []
    if material_code:
        sql += " AND c.material_code = ?"
        args.append(material_code)
    elif material_name:
        sql += " AND (c.material_name LIKE ?)"
        args.append(f"%{material_name}%")
    rows = db.query(sql, args)

    purity_min = requirement.get("purity_pct_min")
    d50_min = requirement.get("d50_um_min")
    d50_max = requirement.get("d50_um_max")
    out: list[dict] = []
    for row in rows:
        gaps: list[str] = []
        # 来料认证未通过的供应商,规格再达标也不算合格供方。
        if not row.get("qualified"):
            gaps.append(f"供方未通过来料认证{('：' + row['note']) if row.get('note') else ''}")
        if purity_min is not None:
            offered = row.get("max_purity_pct")
            if offered is None or offered < purity_min:
                gaps.append(f"纯度 {offered} < 要求 {purity_min}%")
        if d50_min is not None and row.get("d50_max_um") is not None and row["d50_max_um"] < d50_min:
            gaps.append(f"D50 上限 {row['d50_max_um']} < 要求下限 {d50_min}µm")
        if d50_max is not None and row.get("d50_min_um") is not None and row["d50_min_um"] > d50_max:
            gaps.append(f"D50 下限 {row['d50_min_um']} > 要求上限 {d50_max}µm")
        out.append({
            "supplier": row["supplier"],
            "supplier_id": row["supplier_id"],
            "material": row.get("material_code") or row.get("material_name"),
            "offered_purity_pct": row.get("max_purity_pct"),
            "offered_d50_um": row.get("d50_min_um"),
            "qualified": not gaps,
            "gap_notes": "; ".join(gaps),
            "moq": row.get("moq"),
            "lead_time": row.get("lead_time"),
        })
    out.sort(key=lambda r: (not r["qualified"], r["supplier"]))
    return out


# ========================================================================== #
# 标准件
# ========================================================================== #
def save_standard_part(part: dict) -> str:
    row = dict(part)
    row.setdefault("std_id", _uid("STD"))
    db.upsert("kb_standard_part", row, keys=("std_id",))
    return row["std_id"]


def find_standard_part(spec: str) -> Optional[dict]:
    """按图纸上的规格串(如 'GB/T 5783 M8x25')反查标准件。"""
    text = (spec or "").strip()
    if not text:
        return None
    exact = db.query_one(
        "SELECT * FROM kb_standard_part WHERE standard_no || ' ' || designation = ? "
        "AND status = 'active'", (text,)
    )
    if exact:
        return exact
    return db.query_one(
        "SELECT * FROM kb_standard_part WHERE ? LIKE '%' || designation || '%' "
        "AND status = 'active' ORDER BY LENGTH(designation) DESC LIMIT 1", (text,)
    )

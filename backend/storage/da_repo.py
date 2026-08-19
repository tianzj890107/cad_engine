"""项目侧(src_ / wip_ / out_ / ops_)的读写。

把现有 Pydantic 模型(DesignIR / ProcessPlan / CostEstimate / RequirementDoc /
ProcessReport)落进 DA 表。函数接受 **dict**(model_dump() 的结果),不反向依赖
models 包,避免存储层与模型层循环引用。

约定:
  - 数据源(src_*)只在受理阶段写一次;
  - 中间产物(wip_*)每次重算整体替换,旧版由 ops_version + 文件快照保留;
  - 产出(out_*)发布时冻结 out_report_snapshot,之后不再随 wip_* 变化。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Iterable, Optional, Sequence

from . import da_db as db

_FEATURE_COLUMNS = (
    "length", "width", "thickness", "height", "diameter", "radius", "distance",
    "x", "y", "count_x", "count_y", "spacing_x", "spacing_y", "purpose",
)


def _uid(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


# ========================================================================== #
# L1 · 项目输入
# ========================================================================== #
def ensure_project(project_id: str, meta: Optional[dict] = None) -> str:
    meta = meta or {}
    row = {
        "project_id": project_id,
        "project_no": meta.get("project_no"),
        "name": meta.get("name") or meta.get("device_name") or project_id,
        "customer": meta.get("customer"),
        "owner": meta.get("owner"),
        "status": meta.get("status") or "active",
        "stage": meta.get("stage"),
        "archived": 1 if meta.get("archived") else 0,
        "created_at": meta.get("created_at") or db.now(),
        "updated_at": db.now(),
    }
    db.upsert("src_project", row, keys=("project_id",))
    return project_id


# 行业模板是需求单的结构性属性(决定 Section C 有哪些字段),单独成列而不是混在
# 字段行里;后续阶段据此到知识库的对应行业数据中检索。
# flexible 已从页面下线,仅兼容早期草稿。
INDUSTRY_KEYS = ("semiconductor", "battery", "appliance", "flexible")

# RequirementDoc.data 里这几个键描述的是「表单怎么渲染」而非业务内容,
# 不进 src_requirement_field,否则字段表里会混进 UI 状态。
_STRUCTURAL_DATA_KEYS = frozenset({
    "industry", "industry_selection", "industry_assessment", "template_spec_manager",
})


def _normalized_industry(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in INDUSTRY_KEYS else "semiconductor"


def save_requirement(doc: dict) -> str:
    """写入需求单(对齐 models/workflow.py::RequirementDoc)。data 拆成字段行。"""
    project_id = doc["project_id"]
    ensure_project(project_id)
    requirement_no = doc["requirement_no"]
    fields = doc.get("data") or {}
    industry = _normalized_industry(doc.get("industry") or fields.get("industry"))
    row = {
        "requirement_no": requirement_no,
        "project_id": project_id,
        "title": doc.get("title"),
        "industry": industry,
        "status": doc.get("status") or "draft",
        "created_by": doc.get("created_by"),
        "created_at": doc.get("created_at") or db.now(),
        "confirmed_by": doc.get("confirmed_by"),
        "confirmed_at": doc.get("confirmed_at"),
        "confirmation_note": doc.get("confirmation_note"),
        "reviewed_by": doc.get("reviewed_by"),
        "reviewed_at": doc.get("reviewed_at"),
        "review_note": doc.get("review_note"),
        "ai_check": doc.get("ai_check") or {},
        "updated_at": db.now(),
    }
    db.upsert("src_requirement", row, keys=("requirement_no",))

    # 需求单每次保存都带全量字段，因此不在本次数据里的行是历史残留（改过模板、
    # 换过行业、删过字段），必须清掉；否则字段表会同时留着新旧两套键。
    incoming = {key for key in fields if key not in _STRUCTURAL_DATA_KEYS}
    for row in db.query(
        "SELECT field_key FROM src_requirement_field WHERE requirement_no = ?", (requirement_no,)
    ):
        if row["field_key"] not in incoming:
            db.execute(
                "DELETE FROM src_requirement_field WHERE requirement_no = ? AND field_key = ?",
                (requirement_no, row["field_key"]),
            )

    for key, value in fields.items():
        if key in _STRUCTURAL_DATA_KEYS:
            continue   # 已单独成列或属于页面状态，不当作业务字段落行
        db.upsert(
            "src_requirement_field",
            {
                "requirement_no": requirement_no,
                "field_key": key,
                "field_value": value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                "source": "human",
                "updated_at": db.now(),
            },
            keys=("requirement_no", "field_key"),
        )
    return requirement_no


def project_industry(project_id: str) -> str:
    """项目所属行业(取自需求单)。后续阶段据此限定知识库检索范围。"""
    row = db.query_one(
        "SELECT industry FROM src_requirement WHERE project_id = ? ORDER BY created_at LIMIT 1",
        (project_id,),
    )
    return _normalized_industry(row["industry"] if row else None)


def register_input_file(project_id: str, *, file_kind: str, filename: str, file_path: str,
                        sha256: str = "", size: int = 0, mime: str = "",
                        uploaded_by: str = "") -> str:
    ensure_project(project_id)
    file_id = _uid("F")
    db.insert("src_input_file", {
        "file_id": file_id, "project_id": project_id, "file_kind": file_kind,
        "filename": filename, "file_path": file_path, "sha256": sha256,
        "file_size": size, "mime": mime, "uploaded_by": uploaded_by,
        "uploaded_at": db.now(),
    })
    return file_id


def record_evidence(project_id: str, stage: str, sources: Iterable[dict]) -> int:
    """联网检索来源入库(对齐 models/cost.py::WebSource)。"""
    count = 0
    for item in sources or []:
        db.insert("src_external_evidence", {
            "project_id": project_id, "stage": stage,
            "query": item.get("query"), "url": item.get("url"),
            "title": item.get("title"), "snippet": item.get("snippet") or item.get("summary"),
            "model_name": item.get("model"), "retrieved_at": item.get("retrieved_at") or db.now(),
        })
        count += 1
    return count


# ========================================================================== #
# L2 · 图纸拆解结果
# ========================================================================== #
def save_design_ir(project_id: str, ir: dict, *, model_name: str = "",
                   version: Optional[int] = None) -> str:
    """整份 IR 落表。同一 version 重复保存视为重算,先清后写。"""
    ensure_project(project_id)
    if version is None:
        row = db.query_one(
            "SELECT COALESCE(MAX(version), 0) AS v FROM wip_design_ir WHERE project_id = ?",
            (project_id,),
        )
        version = int(row["v"]) + 1 if row else 1

    existing = db.query_one(
        "SELECT ir_id FROM wip_design_ir WHERE project_id = ? AND version = ?",
        (project_id, version),
    )
    ir_id = existing["ir_id"] if existing else _uid("IR")
    if existing:
        # 级联删除会带走 assembly/part/feature/provenance/match,保证重算不残留旧行。
        db.execute("DELETE FROM wip_design_ir WHERE ir_id = ?", (ir_id,))

    db.insert("wip_design_ir", {
        "ir_id": ir_id, "project_id": project_id, "version": version,
        "device_name": ir.get("device_name"), "design_intent": ir.get("design_intent"),
        "overall_dims": ir.get("overall_dims"), "assembly_notes": ir.get("assembly_notes"),
        "model_name": model_name, "generated_at": db.now(),
    })

    for asm in ir.get("assemblies") or []:
        db.insert("wip_assembly", {
            "ir_id": ir_id, "assembly_id": asm["assembly_id"], "name": asm.get("name") or "",
            "parent_id": asm.get("parent_id"), "role": asm.get("role"),
            "quantity": asm.get("quantity") or 1,
        })

    for part in ir.get("parts") or []:
        material = part.get("material") or {}
        db.insert("wip_part", {
            "ir_id": ir_id, "part_id": part["part_id"], "name": part.get("name") or "",
            "parent_id": part.get("parent_id"), "role": part.get("role"),
            "material_spec": material.get("spec") if isinstance(material, dict) else material,
            "tolerance_general": part.get("tolerance_general"),
            "quantity": part.get("quantity") or 1,
            "confidence": part.get("confidence", 0.5),
            "recommendation": part.get("recommendation"),
            "model_no": part.get("model_no"), "manufacturer": part.get("manufacturer"),
            "model_specification": part.get("model_specification"),
            "model_lookup_evidence": part.get("model_lookup_evidence"),
        })
        for seq, feature in enumerate(part.get("features") or [], start=1):
            row = {
                "ir_id": ir_id, "part_id": part["part_id"], "seq": seq,
                "feature_type": str(feature.get("type") or feature.get("feature_type")),
            }
            row.update({k: feature.get(k) for k in _FEATURE_COLUMNS if feature.get(k) is not None})
            db.insert("wip_part_feature", row)
        prov = part.get("provenance") or {}
        if prov:
            bbox = prov.get("bbox") or []
            db.insert("wip_part_provenance", {
                "ir_id": ir_id, "part_id": part["part_id"],
                "bbox_x": bbox[0] if len(bbox) > 0 else None,
                "bbox_y": bbox[1] if len(bbox) > 1 else None,
                "bbox_w": bbox[2] if len(bbox) > 2 else None,
                "bbox_h": bbox[3] if len(bbox) > 3 else None,
                "note": prov.get("note"),
            })

    for std in ir.get("standard_parts") or []:
        db.insert("wip_standard_part", {
            "ir_id": ir_id, "spec": std.get("spec") or "待确认规格",
            "category": std.get("category"), "quantity": std.get("quantity") or 1,
            "model_no": std.get("model_no"), "manufacturer": std.get("manufacturer"),
        })

    save_open_questions(project_id, "2.1", ir.get("open_questions") or [])
    return ir_id


def load_design_ir(project_id: str, version: Optional[int] = None) -> Optional[dict]:
    """读回 IR(结构与 models/ir.py::DesignIR 对齐,可直接喂给前端/模型)。"""
    sql = "SELECT * FROM wip_design_ir WHERE project_id = ?"
    args: list[Any] = [project_id]
    if version is not None:
        sql += " AND version = ?"
        args.append(version)
    head = db.query_one(sql + " ORDER BY version DESC LIMIT 1", args)
    if not head:
        return None
    ir_id = head["ir_id"]

    parts = db.query("SELECT * FROM wip_part WHERE ir_id = ? ORDER BY part_id", (ir_id,))
    features = db.query("SELECT * FROM wip_part_feature WHERE ir_id = ? ORDER BY part_id, seq", (ir_id,))
    provenance = {
        r["part_id"]: r for r in db.query("SELECT * FROM wip_part_provenance WHERE ir_id = ?", (ir_id,))
    }
    by_part: dict[str, list[dict]] = {}
    for f in features:
        item = {"type": f["feature_type"]}
        item.update({k: f[k] for k in _FEATURE_COLUMNS if f[k] is not None})
        by_part.setdefault(f["part_id"], []).append(item)

    for part in parts:
        part["features"] = by_part.get(part["part_id"], [])
        if part.get("material_spec"):
            part["material"] = {"spec": part["material_spec"]}
        prov = provenance.get(part["part_id"])
        if prov:
            bbox = [prov[k] for k in ("bbox_x", "bbox_y", "bbox_w", "bbox_h")]
            part["provenance"] = {
                "bbox": bbox if all(v is not None for v in bbox) else None,
                "note": prov.get("note"),
            }

    return {
        "ir_id": ir_id,
        "version": head["version"],
        "device_name": head["device_name"],
        "design_intent": head["design_intent"],
        "overall_dims": head["overall_dims"],
        "assembly_notes": head["assembly_notes"],
        "assemblies": db.query("SELECT * FROM wip_assembly WHERE ir_id = ? ORDER BY assembly_id", (ir_id,)),
        "parts": parts,
        "standard_parts": db.query("SELECT * FROM wip_standard_part WHERE ir_id = ?", (ir_id,)),
    }


def save_component_matches(ir_id: str, part_id: str, matches: Sequence[dict]) -> int:
    """写入零部件推荐结果(先清后写,保证一次推荐一份榜单)。"""
    db.execute(
        "DELETE FROM wip_component_match WHERE ir_id = ? AND part_id = ?", (ir_id, part_id)
    )
    for m in matches:
        db.insert("wip_component_match", {
            "ir_id": ir_id, "part_id": part_id,
            "component_id": m.get("component_id"),
            "match_type": m.get("match_type") or "none",
            "score": m.get("score") or 0,
            "envelope_score": m.get("envelope_score"),
            "param_score": m.get("param_score"),
            "feature_score": m.get("feature_score"),
            "gap_notes": m.get("gap_notes"),
            "created_at": db.now(),
        })
    return len(matches)


def decide_component_match(match_id: int, decision: str, actor: str) -> None:
    """人工定夺复用/改制/新制。复用时给库内零件的复用计数 +1(推荐排序权重)。"""
    if decision not in ("reuse", "modify", "new"):
        raise ValueError(f"未知决策: {decision}")
    db.execute(
        "UPDATE wip_component_match SET decision = ?, decided_by = ?, decided_at = ? "
        "WHERE match_id = ?", (decision, actor, db.now(), match_id),
    )
    if decision == "reuse":
        db.execute(
            "UPDATE kb_component SET reuse_count = reuse_count + 1 WHERE component_id = "
            "(SELECT component_id FROM wip_component_match WHERE match_id = ?)", (match_id,)
        )


def register_geometry(ir_id: str, part_id: str, artifact_kind: str, file_path: str,
                      *, sha256: str = "", size: int = 0) -> int:
    return db.insert("wip_part_geometry", {
        "ir_id": ir_id, "part_id": part_id, "artifact_kind": artifact_kind,
        "file_path": file_path, "sha256": sha256, "file_size": size,
        "generated_at": db.now(),
    })


# ========================================================================== #
# L2 · 工艺 / 材料 / BOM
# ========================================================================== #
def save_process_plan(project_id: str, plan: dict, *, route_code: Optional[str] = None) -> str:
    """写入单个零件的工艺路线(对齐 models/process.py::ProcessPlan)。"""
    ensure_project(project_id)
    part_id = plan["part_id"]
    existing = db.query_one(
        "SELECT plan_id FROM wip_process_plan WHERE project_id = ? AND part_id = ?",
        (project_id, part_id),
    )
    plan_id = existing["plan_id"] if existing else _uid("PP")
    steps = plan.get("steps") or []
    total = sum(float(s.get("duration_min") or 0) for s in steps)
    db.upsert("wip_process_plan", {
        "plan_id": plan_id, "project_id": project_id, "part_id": part_id,
        "part_name": plan.get("part_name"), "material": plan.get("material"),
        "blank": plan.get("blank"), "summary": plan.get("summary"),
        "route_code": route_code, "overall_note": plan.get("overall_note"),
        "total_minutes": round(total, 2), "updated_at": db.now(),
    }, keys=("plan_id",))

    db.execute("DELETE FROM wip_process_step WHERE plan_id = ?", (plan_id,))
    for step in steps:
        db.insert("wip_process_step", {
            "plan_id": plan_id, "step_no": step.get("step_no") or 0,
            "step_code": step.get("step_code"),
            "name": step.get("name") or "", "process_type": str(step.get("type") or step.get("process_type") or "other"),
            "description": step.get("description"), "equipment": step.get("equipment"),
            "equipment_id": step.get("equipment_id"),
            "fixture": step.get("fixture"), "tooling": step.get("tooling"),
            "params": step.get("params"), "quality": step.get("quality"),
            "duration_min": step.get("duration_min"),
            "depends_on": step.get("depends_on") or [],
            "confidence": step.get("confidence", 0.6), "note": step.get("note"),
        })
    save_open_questions(project_id, "2.4", plan.get("open_questions") or [])
    return plan_id


def load_process_plan(project_id: str, part_id: str) -> Optional[dict]:
    plan = db.query_one(
        "SELECT * FROM wip_process_plan WHERE project_id = ? AND part_id = ?",
        (project_id, part_id),
    )
    if not plan:
        return None
    steps = db.query(
        "SELECT * FROM wip_process_step WHERE plan_id = ? ORDER BY step_no", (plan["plan_id"],)
    )
    for s in steps:
        s["depends_on"] = db.decode_json(s.get("depends_on"), [])
    plan["steps"] = steps
    return plan


def save_bom(project_id: str, items: Sequence[dict]) -> int:
    ensure_project(project_id)
    db.execute("DELETE FROM wip_bom_item WHERE project_id = ?", (project_id,))
    for item in items:
        db.insert("wip_bom_item", {
            "project_id": project_id, "ref": item.get("ref"), "item": item.get("item") or "",
            "category": item.get("category"), "material_code": item.get("material_code"),
            "spec": item.get("spec"), "quantity": item.get("quantity"),
            "unit": item.get("unit"), "from_step": item.get("from_step"),
            "note": item.get("note"),
        })
    return len(items)


def save_route_steps(project_id: str, steps: Sequence[dict]) -> int:
    ensure_project(project_id)
    db.execute("DELETE FROM wip_route_step WHERE project_id = ?", (project_id,))
    for step in steps:
        db.insert("wip_route_step", {
            "project_id": project_id, "seq": step.get("seq") or 0,
            "name": step.get("name") or "", "category": step.get("category"),
            "step_code": step.get("step_code"), "equipment": step.get("equipment"),
            "params": step.get("params"), "purpose": step.get("purpose"),
            "quality": step.get("quality"), "critical": 1 if step.get("critical") else 0,
        })
    return len(steps)


def save_material_plan(project_id: str, plan: dict) -> None:
    """写入材料定性结果(对齐 models/material.py::MaterialPlan)。"""
    ensure_project(project_id)
    body = plan.get("body") or {}
    metal = plan.get("metallization") or {}
    supply = plan.get("supply") or {}
    timing = plan.get("timing") or {}
    db.upsert("wip_material_plan", {
        "project_id": project_id,
        "body_selected": body.get("selected"),
        "body_material_code": plan.get("body_material_code"),
        "body_rationale": body.get("rationale"),
        "paste": metal.get("paste") or [],
        "layers": metal.get("layers") or [],
        "metallization_rationale": metal.get("rationale"),
        "supply_conclusion": supply.get("conclusion"),
        "timing_status": timing.get("status") or "not_started",
        "started_at": timing.get("started_at"), "finished_at": timing.get("finished_at"),
        "confirmed_by": body.get("confirmed_by"), "confirmed_at": body.get("confirmed_at"),
        "updated_at": db.now(),
    }, keys=("project_id",))

    db.execute("DELETE FROM wip_material_candidate WHERE project_id = ?", (project_id,))
    for cand in body.get("candidates") or []:
        db.insert("wip_material_candidate", {
            "project_id": project_id, "material_code": cand.get("material_code"),
            "material_name": cand.get("material") or "", "score": cand.get("score", 0.6),
            "pros": cand.get("pros") or [], "cons": cand.get("cons") or [],
            "recommended": 1 if cand.get("recommended") else 0, "source": cand.get("source"),
        })
    save_open_questions(project_id, "2.3", plan.get("open_questions") or [])


# ========================================================================== #
# L2 · 成本测算(确定性重算)
# ========================================================================== #
def save_cost_estimate(project_id: str, estimate: dict, *, items: Optional[Sequence[dict]] = None) -> str:
    """写入成本测算。金额与合计由平台重算,模型给的 amount 只作参考。"""
    ensure_project(project_id)
    existing = db.query_one(
        "SELECT estimate_id FROM wip_cost_estimate WHERE project_id = ? ORDER BY updated_at DESC LIMIT 1",
        (project_id,),
    )
    estimate_id = estimate.get("estimate_id") or (
        existing["estimate_id"] if existing else _uid("CE")
    )
    db.upsert("wip_cost_estimate", {
        "estimate_id": estimate_id, "project_id": project_id,
        "currency": estimate.get("currency") or "CNY",
        "batch_size": estimate.get("batch_size") or 1,
        "market_notes": estimate.get("market_notes"), "summary": estimate.get("summary"),
        "assumptions": estimate.get("assumptions") or [],
        "priced_at": estimate.get("priced_at") or db.now(),
        "confirmed_by": estimate.get("confirmed_by"), "confirmed_at": estimate.get("confirmed_at"),
        "updated_at": db.now(),
    }, keys=("estimate_id",))

    rows = list(items) if items is not None else _flatten_cost_items(estimate)
    db.execute("DELETE FROM wip_cost_item WHERE estimate_id = ?", (estimate_id,))
    for seq, item in enumerate(rows, start=1):
        row = {**item, "estimate_id": estimate_id, "seq": item.get("seq") or seq}
        row["amount"] = _item_amount(row)
        db.insert("wip_cost_item", row)

    recompute_totals(estimate_id)
    save_open_questions(project_id, "2.5", estimate.get("open_questions") or [])
    return estimate_id


def _flatten_cost_items(estimate: dict) -> list[dict]:
    """把 CostEstimate 的四个 List 摊平成统一明细行。"""
    out: list[dict] = []
    for item in estimate.get("material_costs") or []:
        out.append({
            "cost_type": "material", "name": item.get("item") or "", "spec": item.get("spec"),
            "unit_usage": item.get("unit_usage"), "unit": item.get("unit"),
            "unit_price": item.get("unit_price"), "amount": item.get("amount"),
            "basis": item.get("market_price_source"),
            "supply_stability": item.get("supply_stability"), "note": item.get("note"),
            "material_code": item.get("material_code"), "price_id": item.get("price_id"),
            "source": item.get("source") or "ai",
        })
    for item in estimate.get("manufacturing_costs") or []:
        out.append({
            "cost_type": "manufacturing", "name": item.get("process") or "",
            "labor_cost": item.get("labor_cost"),
            "equipment_depreciation": item.get("equipment_depreciation"),
            "energy_cost": item.get("energy_cost"), "other_cost": item.get("other"),
            "amount": item.get("subtotal"), "basis": item.get("basis"), "note": item.get("note"),
            "step_code": item.get("step_code"), "rate_code": item.get("rate_code"),
            "source": item.get("source") or "ai",
        })
    for item in estimate.get("technical_costs") or []:
        out.append({
            "cost_type": "technical", "name": item.get("item") or "",
            "amount": item.get("amount"), "basis": item.get("basis"), "note": item.get("note"),
            "factor_code": item.get("factor_code"), "source": item.get("source") or "ai",
        })
    for item in estimate.get("logistics_costs") or []:
        out.append({
            "cost_type": "logistics", "name": item.get("item") or "",
            "amount": item.get("amount"), "basis": item.get("basis"), "note": item.get("note"),
            "rate_code": item.get("rate_code"), "source": item.get("source") or "ai",
        })
    return out


def _item_amount(row: dict) -> float:
    """单件金额的确定性口径:能算的一律重算,只有算不出来时才采信模型给的值。"""
    if row.get("cost_type") == "material":
        usage, price = row.get("unit_usage"), row.get("unit_price")
        if usage is not None and price is not None:
            return round(float(usage) * float(price), 6)
    if row.get("cost_type") == "manufacturing":
        parts = [row.get(k) for k in ("labor_cost", "equipment_depreciation", "energy_cost", "other_cost")]
        values = [float(p) for p in parts if p is not None]
        if values:
            return round(sum(values), 6)
    return round(float(row.get("amount") or 0), 6)


def recompute_totals(estimate_id: str) -> dict:
    """按明细重算四类小计与总计。这是报告里金额的唯一来源。"""
    rows = db.query(
        "SELECT cost_type, SUM(amount) AS total FROM wip_cost_item "
        "WHERE estimate_id = ? GROUP BY cost_type", (estimate_id,)
    )
    totals = {r["cost_type"]: round(float(r["total"] or 0), 6) for r in rows}
    material = totals.get("material", 0.0)
    manufacturing = totals.get("manufacturing", 0.0)
    technical = totals.get("technical", 0.0)
    logistics = totals.get("logistics", 0.0)
    grand = round(material + manufacturing + technical + logistics, 6)
    db.execute(
        "UPDATE wip_cost_estimate SET material_total = ?, manufacturing_total = ?, "
        "technical_total = ?, logistics_total = ?, grand_total = ?, updated_at = ? "
        "WHERE estimate_id = ?",
        (material, manufacturing, technical, logistics, grand, db.now(), estimate_id),
    )
    return {
        "material_total": material, "manufacturing_total": manufacturing,
        "technical_total": technical, "logistics_total": logistics, "grand_total": grand,
    }


def load_cost_estimate(project_id: str) -> Optional[dict]:
    head = db.query_one(
        "SELECT * FROM wip_cost_estimate WHERE project_id = ? ORDER BY updated_at DESC LIMIT 1",
        (project_id,),
    )
    if not head:
        return None
    head["assumptions"] = db.decode_json(head.get("assumptions"), [])
    head["items"] = db.query(
        "SELECT * FROM wip_cost_item WHERE estimate_id = ? ORDER BY cost_type, seq",
        (head["estimate_id"],),
    )
    return head


# ========================================================================== #
# L2 · 澄清池 / 阶段状态
# ========================================================================== #
def save_open_questions(project_id: str, stage: str, questions: Iterable[dict]) -> int:
    """重算某阶段时替换该阶段的未决问题;已解决的保留,不被覆盖。"""
    db.execute(
        "DELETE FROM wip_open_question WHERE project_id = ? AND stage = ? AND status = 'open'",
        (project_id, stage),
    )
    count = 0
    for q in questions or []:
        db.insert("wip_open_question", {
            "project_id": project_id, "stage": stage,
            "field": q.get("field") or "待确认项",
            "reason": q.get("reason") or "需人工确认",
            "guess": q.get("guess"), "created_at": db.now(),
        })
        count += 1
    return count


def resolve_question(q_id: int, answer: str, actor: str) -> None:
    db.execute(
        "UPDATE wip_open_question SET status = 'resolved', answer = ?, resolved_by = ?, "
        "resolved_at = ? WHERE q_id = ?", (answer, actor, db.now(), q_id),
    )


def set_stage_state(project_id: str, stage: str, status: str, *, actor: str = "") -> None:
    ensure_project(project_id)
    now = db.now()
    row = {
        "project_id": project_id, "stage": stage, "status": status, "updated_at": now,
    }
    if status == "in_progress":
        row["started_at"] = now
    if status == "done":
        row["finished_at"] = now
        if actor:
            row["confirmed_by"] = actor
            row["confirmed_at"] = now
    db.upsert("wip_stage_state", row, keys=("project_id", "stage"))


def stage_states(project_id: str) -> list[dict]:
    return db.query(
        "SELECT * FROM wip_stage_state WHERE project_id = ? ORDER BY stage", (project_id,)
    )


# ========================================================================== #
# L3 · 评估结果
# ========================================================================== #
def save_report(doc: dict) -> str:
    """写入工艺评估报告(对齐 models/workflow.py::ProcessReport)。"""
    project_id = doc["project_id"]
    ensure_project(project_id)
    report_no = doc["report_no"]
    db.upsert("out_process_report", {
        "report_no": report_no, "project_id": project_id,
        "requirement_no": doc.get("requirement_no") or None,
        "title": doc.get("title") or "工艺评估报告",
        "version": doc.get("version") or 1,
        "status": doc.get("status") or "draft",
        "overview": doc.get("overview"), "conclusion": doc.get("conclusion"),
        "highlights": doc.get("highlights") or [], "risks": doc.get("risks") or [],
        "basic_info": doc.get("basic_info") or {},
        "review_conclusion": doc.get("review_conclusion"),
        "distribution_scope": doc.get("distribution_scope"),
        "distribution_cc": doc.get("distribution_cc"),
        "prepared_by": doc.get("prepared_by"), "prepared_at": doc.get("prepared_at"),
        "reviewed_by": doc.get("reviewed_by"), "reviewed_at": doc.get("reviewed_at"),
        "review_note": doc.get("review_note"),
        "published_by": doc.get("published_by"), "published_at": doc.get("published_at"),
        "updated_at": db.now(),
    }, keys=("report_no",))

    db.execute("DELETE FROM out_report_evaluation_item WHERE report_no = ?", (report_no,))
    for seq, item in enumerate(doc.get("evaluation_items") or [], start=1):
        db.insert("out_report_evaluation_item", {
            "report_no": report_no, "seq": seq, "item": item.get("item") or "",
            "status": item.get("status") or "可行", "conclusion": item.get("conclusion"),
        })

    db.execute("DELETE FROM out_report_stage_result WHERE report_no = ?", (report_no,))
    for seq, item in enumerate(doc.get("stage_results") or [], start=1):
        db.insert("out_report_stage_result", {
            "report_no": report_no, "seq": seq, "stage": item.get("stage") or "",
            "conclusion": item.get("conclusion"),
        })

    db.execute("DELETE FROM out_report_attachment WHERE report_no = ?", (report_no,))
    for item in doc.get("attachments") or []:
        db.insert("out_report_attachment", {
            "report_no": report_no, "name": item.get("name") or "",
            "source_kind": item.get("source"), "file_path": item.get("href"),
        })

    for seq, item in enumerate(doc.get("review_items") or [], start=1):
        db.insert("out_report_review", {
            "report_no": report_no, "seq": seq, "item": item.get("item"),
            "tag": item.get("tag"), "opinion": item.get("opinion"),
            "reviewer": doc.get("reviewed_by"), "reviewed_at": doc.get("reviewed_at"),
        })

    for r in doc.get("recipients") or []:
        db.insert("out_report_distribution", {
            "report_no": report_no, "recipient_name": r.get("name") or "",
            "organization": r.get("organization"), "contact": r.get("contact"),
            "channel": r.get("channel") or "平台通知", "sent_at": doc.get("published_at"),
        })
    return report_no


def freeze_report(report_no: str, *, actor: str = "system") -> dict:
    """发布前冻结快照:把全部 wip_* 与引用到的 kb 主键存成不可变 JSON。

    没有这一步,知识库改一次费率就会让历史报告的数字对不上。
    """
    head = db.query_one("SELECT * FROM out_process_report WHERE report_no = ?", (report_no,))
    if not head:
        raise LookupError(f"报告不存在: {report_no}")
    project_id = head["project_id"]

    snapshot = {
        "report": head,
        "requirement": db.query_one(
            "SELECT * FROM src_requirement WHERE project_id = ?", (project_id,)
        ),
        "design_ir": load_design_ir(project_id),
        "component_matches": db.query(
            "SELECT m.* FROM wip_component_match m JOIN wip_design_ir i ON i.ir_id = m.ir_id "
            "WHERE i.project_id = ?", (project_id,)
        ),
        "process_plans": db.query(
            "SELECT * FROM wip_process_plan WHERE project_id = ?", (project_id,)
        ),
        "route_steps": db.query("SELECT * FROM wip_route_step WHERE project_id = ? ORDER BY seq", (project_id,)),
        "bom": db.query("SELECT * FROM wip_bom_item WHERE project_id = ?", (project_id,)),
        "material_plan": db.query_one(
            "SELECT * FROM wip_material_plan WHERE project_id = ?", (project_id,)
        ),
        "cost_estimate": load_cost_estimate(project_id),
        "open_questions": db.query(
            "SELECT * FROM wip_open_question WHERE project_id = ?", (project_id,)
        ),
        "stage_states": stage_states(project_id),
    }
    kb_refs = _collect_kb_refs(project_id, snapshot)
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    version = int(head["version"] or 1)
    db.upsert("out_report_snapshot", {
        "report_no": report_no, "version": version,
        "snapshot": payload, "kb_refs": kb_refs, "snapshot_sha256": digest,
        "frozen_by": actor, "frozen_at": db.now(),
    }, keys=("report_no", "version"))

    estimate = snapshot.get("cost_estimate")
    if estimate:
        batch = int(estimate.get("batch_size") or 1)
        unit_cost = float(estimate.get("grand_total") or 0)
        db.upsert("out_cost_result", {
            "report_no": report_no, "version": version, "batch_size": batch,
            "unit_cost": unit_cost, "total_cost": round(unit_cost * batch, 6),
            "currency": estimate.get("currency") or "CNY",
            "estimate_id": estimate.get("estimate_id"), "frozen_at": db.now(),
        }, keys=("report_no", "version"))
    return {"report_no": report_no, "version": version, "sha256": digest}


def _collect_kb_refs(project_id: str, snapshot: dict) -> dict:
    """记录本次报告引用到的知识库主键与版本,供事后核对。"""
    refs: dict[str, list] = {"kb_component": [], "kb_process_step": [],
                             "kb_material_price": [], "kb_cost_rate": [], "kb_cost_factor": []}
    for m in snapshot.get("component_matches") or []:
        if m.get("component_id"):
            refs["kb_component"].append(m["component_id"])
    for row in db.query(
        "SELECT DISTINCT s.step_code FROM wip_process_step s JOIN wip_process_plan p "
        "ON p.plan_id = s.plan_id WHERE p.project_id = ? AND s.step_code IS NOT NULL",
        (project_id,),
    ):
        refs["kb_process_step"].append(row["step_code"])
    estimate = snapshot.get("cost_estimate") or {}
    for item in estimate.get("items") or []:
        for key in ("price_id", "rate_code", "factor_code"):
            table = {"price_id": "kb_material_price", "rate_code": "kb_cost_rate",
                     "factor_code": "kb_cost_factor"}[key]
            if item.get(key):
                refs[table].append(item[key])
    return {k: sorted(set(v), key=str) for k, v in refs.items() if v}


def register_deliverable(project_id: str, kind: str, file_path: str, *,
                         report_no: Optional[str] = None, sha256: str = "", size: int = 0) -> int:
    return db.insert("out_deliverable_file", {
        "project_id": project_id, "report_no": report_no, "kind": kind,
        "file_path": file_path, "sha256": sha256, "file_size": size,
        "generated_at": db.now(),
    })


# ========================================================================== #
# L4 · 治理
# ========================================================================== #
def audit(project_id: Optional[str], action: str, *, actor: str = "system",
          target: str = "", detail: Any = None) -> None:
    db.insert("ops_audit", {
        "project_id": project_id, "actor": actor, "action": action,
        "target": target, "detail": detail if detail is None else json.dumps(detail, ensure_ascii=False, default=str),
        "at": db.now(),
    })


def record_llm_call(project_id: Optional[str], *, stage: str, provider: str, model: str,
                    prompt: str = "", input_tokens: int = 0, output_tokens: int = 0,
                    latency_ms: int = 0, ok: bool = True, error: str = "",
                    cost: Optional[float] = None) -> str:
    call_id = _uid("LLM")
    db.insert("ops_llm_call", {
        "call_id": call_id, "project_id": project_id, "stage": stage,
        "provider": provider, "model": model,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else None,
        "input_tokens": input_tokens, "output_tokens": output_tokens, "cost": cost,
        "latency_ms": latency_ms, "ok": 1 if ok else 0, "error": error or None,
        "at": db.now(),
    })
    return call_id


def propose_promotion(project_id: Optional[str], *, source_table: str, source_id: str,
                      target_kb_table: str, payload: Any) -> int:
    """项目产出申请入知识库。项目侧不得直接写 kb_*,必须走这条评审通道。"""
    return db.insert("ops_kb_promotion", {
        "project_id": project_id, "source_table": source_table, "source_id": str(source_id),
        "target_kb_table": target_kb_table,
        "payload": json.dumps(payload, ensure_ascii=False, default=str),
        "status": "pending", "created_at": db.now(),
    })


def list_promotions(status: str = "pending") -> list[dict]:
    return db.query(
        "SELECT * FROM ops_kb_promotion WHERE status = ? ORDER BY created_at DESC", (status,)
    )


def decide_promotion(promo_id: int, decision: str, reviewer: str, note: str = "") -> None:
    if decision not in ("approved", "rejected"):
        raise ValueError(f"未知决策: {decision}")
    db.execute(
        "UPDATE ops_kb_promotion SET status = ?, reviewer = ?, review_note = ?, decided_at = ? "
        "WHERE promo_id = ?", (decision, reviewer, note, db.now(), promo_id),
    )

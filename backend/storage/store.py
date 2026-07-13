"""
项目存储 + 可追溯(证据链)。

结构化元数据(meta / IR / 几何结果 / 图纸结果 / 审计)经可插拔的 MetaBackend
存储(默认 JSON 文件,可切 SQL —— 见 meta_backend.py 与 config.STORAGE_BACKEND)。
二进制大文件(原图 / 附件 / STEP / STL / SVG / DXF)始终落在 DATA_DIR/<id>/ 下:
  source<ext>      上传的设备需求原图
  attachments/     佐证文件
  geometry/        STEP/STL 与 2D 工程图(SVG/DXF)

这套结构即"证据链": 原图 -> IR -> 几何/图纸 -> 校验，逐级留痕、可回查、可审计。
本模块的公开函数签名保持稳定,上层(main.py / services)无需改动。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import List, Optional

from ..config import DATA_DIR
from .blob_backend import get_blob_backend
from .meta_backend import get_backend

# 任务记录是多线程(worker)读改写同一文档,需串行化避免丢更新
_task_lock = threading.Lock()


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _meta():
    return get_backend()


def _blob():
    return get_blob_backend()


# --------------------------------------------------------------------------- #
# 路径助手(二进制: 走可插拔 blob 后端; 本地目录同时是 CAD 工作目录/远端缓存)
# --------------------------------------------------------------------------- #
def project_dir(project_id: str) -> Path:
    return DATA_DIR / project_id


def attachments_dir(project_id: str) -> Path:
    return _blob().ensure_local_dir(f"{project_id}/attachments")


def geometry_dir(project_id: str) -> Path:
    """CAD 内核写几何文件的本地工作目录(S3 后端下亦为上传前的缓存)。"""
    return _blob().ensure_local_dir(f"{project_id}/geometry")


def sync_geometry(project_id: str) -> None:
    """几何/2D 文件生成后,把本地工作目录同步到对象存储(Local 后端为空操作)。"""
    _blob().sync_dir(f"{project_id}/geometry")


def source_path(project_id: str) -> Optional[Path]:
    """对外服务/读取原图的本地路径(S3 后端下必要时回源拉取)。"""
    meta = load_meta(project_id)
    if not meta:
        return None
    return _blob().local_path(f"{project_id}/{meta['source_path']}")


def geometry_file(project_id: str, filename: str) -> Optional[Path]:
    """对外服务某个几何/2D 文件的本地路径(必要时从对象存储拉取)。"""
    return _blob().local_path(f"{project_id}/geometry/{Path(filename).name}")


# --------------------------------------------------------------------------- #
# 审计(可追溯)
# --------------------------------------------------------------------------- #
def audit(project_id: str, action: str, detail=None) -> None:
    _meta().append_audit(project_id, {"ts": _now(), "action": action, "detail": detail})


def list_audit(project_id: str) -> List[dict]:
    return _meta().list_audit(project_id)


# --------------------------------------------------------------------------- #
# 项目 / 资料
# --------------------------------------------------------------------------- #
def create_project(
    source_filename: str, source_bytes: bytes, note: str = "", owner: str = "system"
) -> str:
    project_id = uuid.uuid4().hex[:12]

    ext = Path(source_filename).suffix or ".png"
    src_name = f"source{ext}"
    _blob().put_bytes(f"{project_id}/{src_name}", source_bytes)

    meta = {
        "project_id": project_id,
        "source_filename": source_filename,
        "source_path": src_name,
        "note": note or "",
        "owner": owner or "system",
        "attachments": [],
        "created_at": _now(),
        "stages": {"uploaded": _now()},
    }
    _meta().put_meta(project_id, meta)
    audit(project_id, "create_project", {"source": source_filename, "owner": owner})
    return project_id


# --------------------------------------------------------------------------- #
# 用户(鉴权)
# --------------------------------------------------------------------------- #
def get_user(username: str) -> Optional[dict]:
    return _meta().get_user(username)


def save_user(username: str, data: dict) -> None:
    _meta().put_user(username, data)


def list_users() -> List[dict]:
    return _meta().list_users()


# --------------------------------------------------------------------------- #
# 异步任务记录(状态/进度/结果),存于 doc kind "tasks" 的 {"items":[...]}
# --------------------------------------------------------------------------- #
def save_task(project_id: str, rec: dict) -> None:
    with _task_lock:
        data = _meta().get_doc(project_id, "tasks") or {"items": []}
        items = data["items"]
        for i, t in enumerate(items):
            if t.get("task_id") == rec.get("task_id"):
                items[i] = rec
                break
        else:
            items.append(rec)
        _meta().put_doc(project_id, "tasks", data)


def get_task(project_id: str, task_id: str) -> Optional[dict]:
    with _task_lock:
        data = _meta().get_doc(project_id, "tasks") or {"items": []}
    for t in data["items"]:
        if t.get("task_id") == task_id:
            return t
    return None


def list_tasks(project_id: str) -> List[dict]:
    with _task_lock:
        data = _meta().get_doc(project_id, "tasks") or {"items": []}
    return data["items"]


def add_attachment(project_id: str, filename: str, data: bytes) -> None:
    """保存一个佐证文件，并登记到 meta(可追溯)。"""
    safe = Path(filename).name
    _blob().put_bytes(f"{project_id}/attachments/{safe}", data)
    meta = load_meta(project_id) or {}
    atts = meta.setdefault("attachments", [])
    if safe not in atts:
        atts.append(safe)
    _meta().put_meta(project_id, meta)
    audit(project_id, "add_attachment", {"file": safe})


def load_attachments(project_id: str) -> list[tuple[str, bytes]]:
    """读取该项目所有佐证文件，返回 [(filename, bytes), ...]。"""
    meta = load_meta(project_id) or {}
    out: list[tuple[str, bytes]] = []
    for name in meta.get("attachments", []):
        data = _blob().get_bytes(f"{project_id}/attachments/{name}")
        if data is not None:
            out.append((name, data))
    return out


def get_note(project_id: str) -> str:
    meta = load_meta(project_id) or {}
    return meta.get("note", "") or ""


def load_meta(project_id: str) -> Optional[dict]:
    return _meta().get_meta(project_id)


def list_projects() -> List[dict]:
    out = []
    for meta in _meta().list_metas():
        pid = meta.get("project_id")
        ir = _meta().get_doc(pid, "ir") if pid else None
        meta["has_ir"] = ir is not None
        meta["device_name"] = (ir or {}).get("device_name")
        out.append(meta)
    return out


# --------------------------------------------------------------------------- #
# IR / 几何 / 2D 图纸 结果
# --------------------------------------------------------------------------- #
def save_ir(project_id: str, ir_dict: dict, stage: str = "parsed", author: str = "system") -> None:
    _meta().put_doc(project_id, "ir", ir_dict)
    _touch_stage(project_id, stage)
    audit(project_id, f"save_ir:{stage}", {"parts": len(ir_dict.get("parts", []))})
    # 每次保存 IR 自动留一个版本快照(可追溯/可回溯/可审签)
    record_version(project_id, ir_dict, stage, author=author)


def load_ir(project_id: str) -> Optional[dict]:
    return _meta().get_doc(project_id, "ir")


def save_geometry_result(project_id: str, result: dict) -> None:
    _meta().put_doc(project_id, "geometry", result)
    _touch_stage(project_id, "geometry")
    audit(project_id, "save_geometry", {"parts": len(result.get("parts", []))})


def load_geometry_result(project_id: str) -> Optional[dict]:
    return _meta().get_doc(project_id, "geometry")


def save_drawings_result(project_id: str, result: dict) -> None:
    _meta().put_doc(project_id, "drawings", result)
    _touch_stage(project_id, "drawings")
    audit(project_id, "save_drawings", {"parts": len(result.get("parts", []))})


def load_drawings_result(project_id: str) -> Optional[dict]:
    return _meta().get_doc(project_id, "drawings")


# --------------------------------------------------------------------------- #
# 工艺拆解(按零件存): doc kind "process" = {"plans": {part_id: plan_dict}}
# --------------------------------------------------------------------------- #
def save_process(project_id: str, part_id: str, plan: dict, author: str = "system") -> None:
    doc = _meta().get_doc(project_id, "process") or {"plans": {}}
    doc.setdefault("plans", {})[part_id] = plan
    _meta().put_doc(project_id, "process", doc)
    _touch_stage(project_id, "process")
    audit(project_id, "save_process", {"part_id": part_id, "by": author,
                                       "steps": len(plan.get("steps", []))})


def load_process(project_id: str, part_id: str) -> Optional[dict]:
    doc = _meta().get_doc(project_id, "process") or {}
    return (doc.get("plans") or {}).get(part_id)


def load_all_process(project_id: str) -> dict:
    doc = _meta().get_doc(project_id, "process") or {}
    return doc.get("plans") or {}


# --------------------------------------------------------------------------- #
# 成本分析(按零件存): doc kind "cost" = {"analyses": {part_id: analysis_dict}}
# --------------------------------------------------------------------------- #
def save_cost(project_id: str, part_id: str, analysis: dict, author: str = "system") -> None:
    doc = _meta().get_doc(project_id, "cost") or {"analyses": {}}
    doc.setdefault("analyses", {})[part_id] = analysis
    _meta().put_doc(project_id, "cost", doc)
    _touch_stage(project_id, "cost")
    audit(project_id, "save_cost", {"part_id": part_id, "by": author,
                                    "items": len(analysis.get("items", []))})


def load_cost(project_id: str, part_id: str) -> Optional[dict]:
    doc = _meta().get_doc(project_id, "cost") or {}
    return (doc.get("analyses") or {}).get(part_id)


# --------------------------------------------------------------------------- #
# 材料定性与供应链拆解(项目级,单份计划): doc kind "material"
# --------------------------------------------------------------------------- #
def save_material(project_id: str, plan: dict, author: str = "system") -> None:
    _meta().put_doc(project_id, "material", plan)
    _touch_stage(project_id, "material")
    audit(project_id, "save_material", {"by": author})


def load_material(project_id: str) -> Optional[dict]:
    return _meta().get_doc(project_id, "material")


# --------------------------------------------------------------------------- #
# 制造工艺路径规划和 BOM 编制(项目级,单份计划): doc kind "manufacturing"
# --------------------------------------------------------------------------- #
def save_manufacturing(project_id: str, plan: dict, author: str = "system") -> None:
    _meta().put_doc(project_id, "manufacturing", plan)
    _touch_stage(project_id, "manufacturing")
    audit(project_id, "save_manufacturing", {"by": author})


def load_manufacturing(project_id: str) -> Optional[dict]:
    return _meta().get_doc(project_id, "manufacturing")


# --------------------------------------------------------------------------- #
# 清洗与洁净度管控方案制定(项目级,单份计划): doc kind "cleaning"
# --------------------------------------------------------------------------- #
def save_cleaning(project_id: str, plan: dict, author: str = "system") -> None:
    _meta().put_doc(project_id, "cleaning", plan)
    _touch_stage(project_id, "cleaning")
    audit(project_id, "save_cleaning", {"by": author})


def load_cleaning(project_id: str) -> Optional[dict]:
    return _meta().get_doc(project_id, "cleaning")


# --------------------------------------------------------------------------- #
# 组装与检测方案制定(项目级,单份计划): doc kind "assembly"
# --------------------------------------------------------------------------- #
def save_assembly(project_id: str, plan: dict, author: str = "system") -> None:
    _meta().put_doc(project_id, "assembly", plan)
    _touch_stage(project_id, "assembly")
    audit(project_id, "save_assembly", {"by": author})


def load_assembly(project_id: str) -> Optional[dict]:
    return _meta().get_doc(project_id, "assembly")


# --------------------------------------------------------------------------- #
# 产线匹配与产能评估(项目级,单份计划): doc kind "production"
# --------------------------------------------------------------------------- #
def save_production(project_id: str, plan: dict, author: str = "system") -> None:
    _meta().put_doc(project_id, "production", plan)
    _touch_stage(project_id, "production")
    audit(project_id, "save_production", {"by": author})


def load_production(project_id: str) -> Optional[dict]:
    return _meta().get_doc(project_id, "production")


# --------------------------------------------------------------------------- #
# 技术工艺总结(项目级,单份执行摘要): doc kind "summary"
# --------------------------------------------------------------------------- #
def save_summary(project_id: str, doc: dict, author: str = "system") -> None:
    _meta().put_doc(project_id, "summary", doc)
    _touch_stage(project_id, "summary")
    audit(project_id, "save_summary", {"by": author})


def load_summary(project_id: str) -> Optional[dict]:
    return _meta().get_doc(project_id, "summary")


# --------------------------------------------------------------------------- #
# 成本测算(报价流程第 1 步,项目级,单份): doc kind "costest"
# --------------------------------------------------------------------------- #
def save_costest(project_id: str, doc: dict, author: str = "system") -> None:
    _meta().put_doc(project_id, "costest", doc)
    _touch_stage(project_id, "costest")
    audit(project_id, "save_costest", {"by": author})


def load_costest(project_id: str) -> Optional[dict]:
    return _meta().get_doc(project_id, "costest")


# --------------------------------------------------------------------------- #
# 定价方案(报价流程第 2 步,项目级,单份): doc kind "pricing"
# --------------------------------------------------------------------------- #
def save_pricing(project_id: str, doc: dict, author: str = "system") -> None:
    _meta().put_doc(project_id, "pricing", doc)
    _touch_stage(project_id, "pricing")
    audit(project_id, "save_pricing", {"by": author})


def load_pricing(project_id: str) -> Optional[dict]:
    return _meta().get_doc(project_id, "pricing")


# --------------------------------------------------------------------------- #
# 商务及谈判策略(报价流程第 3 步,项目级,单份): doc kind "negotiation"
# --------------------------------------------------------------------------- #
def save_negotiation(project_id: str, doc: dict, author: str = "system") -> None:
    _meta().put_doc(project_id, "negotiation", doc)
    _touch_stage(project_id, "negotiation")
    audit(project_id, "save_negotiation", {"by": author})


def load_negotiation(project_id: str) -> Optional[dict]:
    return _meta().get_doc(project_id, "negotiation")


# --------------------------------------------------------------------------- #
# 价格协商及谈判(报价流程第 4 步,项目级,单份): doc kind "pricenego"
# --------------------------------------------------------------------------- #
def save_pricenego(project_id: str, doc: dict, author: str = "system") -> None:
    _meta().put_doc(project_id, "pricenego", doc)
    _touch_stage(project_id, "pricenego")
    audit(project_id, "save_pricenego", {"by": author})


def load_pricenego(project_id: str) -> Optional[dict]:
    return _meta().get_doc(project_id, "pricenego")


# --------------------------------------------------------------------------- #
# 报价审批与决策(报价流程第 6 步,项目级,单份): doc kind "approval"
# --------------------------------------------------------------------------- #
def save_approval(project_id: str, doc: dict, author: str = "system") -> None:
    _meta().put_doc(project_id, "approval", doc)
    _touch_stage(project_id, "approval")
    audit(project_id, "save_approval", {"by": author})


def load_approval(project_id: str) -> Optional[dict]:
    return _meta().get_doc(project_id, "approval")


# --------------------------------------------------------------------------- #
# 技术工艺 / 报价 记录(全局列表,JSON 文件): 「结束」步录入到管理列表
# --------------------------------------------------------------------------- #
_record_lock = threading.Lock()


def _records_file() -> Path:
    return DATA_DIR / "techprocess_records.json"


def _read_records() -> List[dict]:
    f = _records_file()
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []


def list_records() -> List[dict]:
    with _record_lock:
        return _read_records()


def save_record(rec: dict) -> dict:
    with _record_lock:
        items = _read_records()
        if not rec.get("id"):
            rec["id"] = "rec_" + uuid.uuid4().hex[:8]
        for i, it in enumerate(items):
            if it.get("id") == rec["id"]:
                items[i] = rec
                break
        else:
            items.append(rec)
        _records_file().write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        return rec


def delete_record(rid: str) -> None:
    with _record_lock:
        items = [it for it in _read_records() if it.get("id") != rid]
        _records_file().write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# 设备资源台账(全局,JSON 文件,带种子;自有产线 + 外协厂商)
# --------------------------------------------------------------------------- #
_equipment_lock = threading.Lock()

_EQUIPMENT_SEED: List[dict] = [
    {"id": "eq_kiln01", "name": "高温烧结炉 GSL-1800X", "type": "烧结炉", "owner": "自有",
     "capability": "最高 1800℃,可控气氛(N2/真空)", "capacity": "600 件/月", "note": "适合氧化铝/氮化铝共烧"},
    {"id": "eq_pol01", "name": "双面研磨抛光机 9B-AC", "type": "环抛机", "owner": "自有",
     "capability": "面型精度 ≤1µm,Φ300 盘", "capacity": "1000 件/月", "note": "精密研磨/抛光"},
    {"id": "eq_laser01", "name": "紫外激光打孔机 UV-355", "type": "激光打孔机", "owner": "自有",
     "capability": "最小孔径 50µm", "capacity": "—", "note": ""},
    {"id": "eq_out_kiln", "name": "外协-超高温气氛炉", "type": "烧结炉", "owner": "外协",
     "vendor": "示例-外协陶瓷厂E", "capability": "最高 2000℃,氢气气氛", "cost": "8 元/件",
     "lead_time": "1-2 周", "note": "自有炉温不足时外协"},
    {"id": "eq_out_pol", "name": "外协-超精密环抛", "type": "环抛机", "owner": "外协",
     "vendor": "示例-外协精密加工厂F", "capability": "面型 ≤0.3µm", "cost": "15 元/件",
     "lead_time": "1 周", "note": "高面型要求外协"},
]


def _equipment_file() -> Path:
    return DATA_DIR / "equipment.json"


def _read_equipment() -> List[dict]:
    f = _equipment_file()
    if not f.exists():
        f.write_text(json.dumps(_EQUIPMENT_SEED, ensure_ascii=False, indent=2), encoding="utf-8")
        return [dict(x) for x in _EQUIPMENT_SEED]
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []


def list_equipment() -> List[dict]:
    with _equipment_lock:
        return _read_equipment()


def save_equipment(rec: dict) -> dict:
    with _equipment_lock:
        items = _read_equipment()
        if not rec.get("id"):
            rec["id"] = "eq_" + uuid.uuid4().hex[:8]
        for i, it in enumerate(items):
            if it.get("id") == rec["id"]:
                items[i] = rec
                break
        else:
            items.append(rec)
        _equipment_file().write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        return rec


def delete_equipment(eid: str) -> None:
    with _equipment_lock:
        items = [it for it in _read_equipment() if it.get("id") != eid]
        _equipment_file().write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# 供应商能力目录(全局,JSON 文件,带种子)
# --------------------------------------------------------------------------- #
_supplier_lock = threading.Lock()

_SUPPLIER_SEED: List[dict] = [
    {"id": "sup_al01", "name": "示例-高纯氧化铝粉厂A", "material": "氧化铝粉",
     "max_purity_pct": 99.99, "d50_min_um": 0.3, "d50_max_um": 1.0,
     "moq": "100kg", "lead_time": "2-3 周", "contact": "—", "note": "亚微米高纯,适合流延"},
    {"id": "sup_al02", "name": "示例-氧化铝粉厂B", "material": "氧化铝粉",
     "max_purity_pct": 99.5, "d50_min_um": 1.0, "d50_max_um": 3.0,
     "moq": "50kg", "lead_time": "1-2 周", "contact": "—", "note": "性价比型"},
    {"id": "sup_aln01", "name": "示例-氮化铝粉厂C", "material": "氮化铝粉",
     "max_purity_pct": 99.9, "d50_min_um": 0.8, "d50_max_um": 2.0,
     "moq": "20kg", "lead_time": "4-6 周", "contact": "—", "note": "高热导,含氧量低"},
    {"id": "sup_pst01", "name": "示例-电子浆料厂D", "material": "Ag-Pd 浆料",
     "max_purity_pct": 99.9, "d50_min_um": 0.5, "d50_max_um": 2.5,
     "moq": "5kg", "lead_time": "1 周", "contact": "—", "note": "厚膜导体浆料"},
]


def _suppliers_file() -> Path:
    return DATA_DIR / "suppliers.json"


def _read_suppliers() -> List[dict]:
    f = _suppliers_file()
    if not f.exists():
        f.write_text(json.dumps(_SUPPLIER_SEED, ensure_ascii=False, indent=2), encoding="utf-8")
        return [dict(x) for x in _SUPPLIER_SEED]
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []


def list_suppliers() -> List[dict]:
    with _supplier_lock:
        return _read_suppliers()


def save_supplier(rec: dict) -> dict:
    with _supplier_lock:
        items = _read_suppliers()
        if not rec.get("id"):
            rec["id"] = "sup_" + uuid.uuid4().hex[:8]
        for i, it in enumerate(items):
            if it.get("id") == rec["id"]:
                items[i] = rec
                break
        else:
            items.append(rec)
        _suppliers_file().write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        return rec


def delete_supplier(sid: str) -> None:
    with _supplier_lock:
        items = [it for it in _read_suppliers() if it.get("id") != sid]
        _suppliers_file().write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# 版本快照 + 校核审签(PRD 6.5)
#   状态机: draft(草稿) -> in_review(送审) -> approved(通过) / rejected(驳回)
#   完整 IR 快照存于 doc kind "versions" 的 {"items": [...]} 中,逐版可回溯。
# --------------------------------------------------------------------------- #
def _versions(project_id: str) -> dict:
    return _meta().get_doc(project_id, "versions") or {"items": []}


def record_version(
    project_id: str, ir_dict: dict, stage: str, author: str = "system", note: str = ""
) -> int:
    data = _versions(project_id)
    items = data["items"]
    version = len(items) + 1
    items.append({
        "version": version,
        "ts": _now(),
        "stage": stage,
        "author": author,
        "note": note,
        "status": "draft",
        "review": [],
        "ir": ir_dict,
    })
    _meta().put_doc(project_id, "versions", data)
    return version


def list_versions(project_id: str) -> List[dict]:
    """版本元信息列表(不含完整 IR),供前端列表/审签面板。"""
    from ..services import versioning
    out: List[dict] = []
    for r in _versions(project_id)["items"]:
        row = {k: r.get(k) for k in ("version", "ts", "stage", "author", "note", "status")}
        row["review"] = r.get("review", [])
        row.update(versioning.summarize(r.get("ir")))
        out.append(row)
    return out


def get_version(project_id: str, version: int) -> Optional[dict]:
    for r in _versions(project_id)["items"]:
        if r.get("version") == version:
            return r
    return None


def set_version_status(
    project_id: str, version: int, status: str, actor: str = "", comment: str = ""
) -> Optional[dict]:
    data = _versions(project_id)
    for r in data["items"]:
        if r.get("version") == version:
            r["status"] = status
            r.setdefault("review", []).append(
                {"ts": _now(), "actor": actor or "anonymous", "status": status, "comment": comment}
            )
            _meta().put_doc(project_id, "versions", data)
            audit(project_id, f"review:{status}",
                  {"version": version, "actor": actor, "comment": comment})
            return r
    return None


# --------------------------------------------------------------------------- #
def _touch_stage(project_id: str, stage: str) -> None:
    meta = load_meta(project_id) or {}
    meta.setdefault("stages", {})[stage] = _now()
    _meta().put_meta(project_id, meta)

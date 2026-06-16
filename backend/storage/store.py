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

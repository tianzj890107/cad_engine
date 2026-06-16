"""
FastAPI 应用: 图纸解析与生成平台后端。

流程: 上传原图 -> Claude 解析为 IR -> Claude 拆解推荐增强 -> CAD 内核生成几何
       -> 前端展示(原图/拆解树/3D 查看器/校验告警/推荐)。

所有阶段结果都落盘(见 storage.store)，形成可追溯证据链。
"""
from __future__ import annotations

from typing import List

from fastapi import (
    Body, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import AUTH_ENABLED, CLAUDE_MODEL, ROOT_DIR
from .models.cost import CostAnalysis
from .models.ir import DesignIR
from .models.process import ProcessPlan
from .services import (
    auth, bom, cost, decompose, drawing2d, geometry, process, step_import, tasks,
    tree, versioning, vision,
)
from .storage import store


class ReviewAction(BaseModel):
    comment: str = ""


class LoginBody(BaseModel):
    username: str
    password: str


class NewUser(BaseModel):
    username: str
    password: str
    role: str = "engineer"
    display_name: str = ""


# --------------------------------------------------------------------------- #
# 鉴权: app 级依赖在 /api 层校验令牌(放行 health/login 与静态前端);
# 关闭鉴权时注入隐式 system/admin,保持旧行为。
# --------------------------------------------------------------------------- #
_PUBLIC_PATHS = {"/api/health", "/api/login"}


async def auth_guard(request: Request):
    if not AUTH_ENABLED:
        request.state.user = auth.SYSTEM_USER
        return
    path = request.url.path
    if path in _PUBLIC_PATHS or not path.startswith("/api/"):
        return
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not token:  # <img>/STL 等无法带请求头,允许 ?token= 透传
        token = request.query_params.get("token", "")
    payload = auth.parse_token(token)
    if not payload:
        raise HTTPException(401, "未登录或令牌已失效")
    u = store.get_user(payload.get("sub", ""))
    if not u:
        raise HTTPException(401, "用户不存在")
    request.state.user = auth.public_user(u)


def current_user(request: Request) -> dict:
    return getattr(request.state, "user", auth.SYSTEM_USER)


def _require(user: dict, allowed: set, msg: str) -> None:
    if (user or {}).get("role") not in allowed:
        raise HTTPException(403, msg)


app = FastAPI(title="图纸解析与生成平台", version="0.1.0", dependencies=[Depends(auth_guard)])

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _seed_admin():
    if AUTH_ENABLED:
        auth.ensure_default_admin(store)


@app.post("/api/login")
def login(body: LoginBody):
    u = store.get_user(body.username)
    if not u or not auth.verify_password(body.password, u.get("password_hash", "")):
        raise HTTPException(401, "用户名或密码错误")
    return {"token": auth.make_token(u["username"], u["role"]), "user": auth.public_user(u)}


@app.get("/api/me")
def whoami(user: dict = Depends(current_user)):
    return {"user": user, "auth_enabled": AUTH_ENABLED}


@app.get("/api/users")
def list_users_ep(user: dict = Depends(current_user)):
    _require(user, auth.ADMIN_ROLES, "需要管理员权限")
    return {"users": [auth.public_user(u) for u in store.list_users()]}


@app.post("/api/users")
def create_user_ep(body: NewUser, user: dict = Depends(current_user)):
    _require(user, auth.ADMIN_ROLES, "需要管理员权限")
    if body.role not in auth.ROLES:
        raise HTTPException(400, f"非法角色,可选: {', '.join(auth.ROLES)}")
    if store.get_user(body.username):
        raise HTTPException(409, "用户名已存在")
    rec = auth.make_user(body.username, body.password, body.role, body.display_name)
    store.save_user(rec["username"], rec)
    return auth.public_user(rec)


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "model": CLAUDE_MODEL,
        "cadquery_available": geometry.CADQUERY_AVAILABLE,
        "auth_enabled": AUTH_ENABLED,
    }


@app.get("/api/projects")
def projects():
    return store.list_projects()


@app.post("/api/projects")
async def upload_project(
    file: UploadFile = File(...),
    note: str = Form(""),
    attachments: List[UploadFile] = File(default=[]),
    user: dict = Depends(current_user),
):
    """上传设备需求原图(可附文字说明与佐证文件)，创建项目。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    content = await file.read()
    if not content:
        raise HTTPException(400, "空文件")
    project_id = store.create_project(
        file.filename or "source.png", content, note=note, owner=user.get("username", "system")
    )
    for att in attachments or []:
        data = await att.read()
        if data:
            store.add_attachment(project_id, att.filename or "attachment", data)
    return {"project_id": project_id}


def _geometry_payload(project_id: str, results) -> dict:
    base = f"/api/projects/{project_id}/geometry"
    return {
        "parts": [
            {
                "part_id": r.part_id, "name": r.name, "ok": r.ok,
                "volume_mm3": r.volume_mm3, "mass_g": r.mass_g, "bbox": r.bbox,
                "warnings": r.warnings, "error": r.error,
                "stl_url": f"{base}/{r.part_id}.stl" if r.ok else None,
                "step_url": f"{base}/{r.part_id}.step" if r.ok else None,
            }
            for r in results
        ]
    }


def _upsert_part(payload: dict, part_entry: dict) -> None:
    parts = payload.setdefault("parts", [])
    for i, p in enumerate(parts):
        if p.get("part_id") == part_entry.get("part_id"):
            parts[i] = part_entry
            return
    parts.append(part_entry)


def _drawings_payload(project_id: str, results) -> dict:
    base = f"/api/projects/{project_id}/geometry"
    return {
        "parts": [
            {
                "part_id": r.part_id, "name": r.name, "ok": r.ok,
                "views": {v: f"{base}/{fn}" for v, fn in r.views.items()},
                "dxf_url": f"{base}/{r.dxf}" if r.dxf else None,
                "warnings": r.warnings, "error": r.error,
            }
            for r in results
        ]
    }


@app.post("/api/projects/3d")
async def upload_3d(
    file: UploadFile = File(...), note: str = Form(""),
    user: dict = Depends(current_user),
):
    """上传 3D 模型(STEP/STP),用 OCCT 反解出零件/结构树/几何属性,
    并直接据原始实体生成 3D(STEP/STL) 与 2D 工程图。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not step_import.AVAILABLE:
        raise HTTPException(503, "CadQuery 未安装，STEP 导入不可用。")
    content = await file.read()
    if not content:
        raise HTTPException(400, "空文件")
    fname = file.filename or "model.step"
    project_id = store.create_project(
        fname, content, note=note, owner=user.get("username", "system")
    )

    def job():
        ir, solid_map = step_import.import_step(content, fname)
        store.save_ir(project_id, ir.model_dump(), stage="parsed_3d",
                      author=user.get("username", "system"))
        out_dir = store.geometry_dir(project_id)
        name_by_id = {p.part_id: p.name for p in ir.parts}
        g_results = [
            geometry.result_from_solid(pid, name_by_id.get(pid, pid), solid, out_dir)
            for pid, solid in solid_map
        ]
        store.save_geometry_result(project_id, _geometry_payload(project_id, g_results))
        d_results = [
            drawing2d.generate_from_solid(pid, name_by_id.get(pid, pid), solid, out_dir)
            for pid, solid in solid_map
        ]
        store.save_drawings_result(project_id, _drawings_payload(project_id, d_results))
        store.sync_geometry(project_id)  # 同步到对象存储(Local 后端空操作)
        store.audit(project_id, "import_step", {"parts": len(solid_map)})
        return {"parts": len(solid_map)}

    task_id = tasks.submit(project_id, "import_3d", job, cad=True)
    return {"project_id": project_id, "task_id": task_id}


@app.post("/api/projects/{project_id}/parse")
def parse(project_id: str, user: dict = Depends(current_user)):
    """调用 Claude 视觉解析原图(结合补充说明/佐证文件) -> IR(异步任务)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    src = store.source_path(project_id)
    if not src or not src.exists():
        raise HTTPException(404, "项目或原图不存在")
    data, name = src.read_bytes(), src.name
    note, atts = store.get_note(project_id), store.load_attachments(project_id)
    author = user.get("username", "system")

    def job():
        ir = vision.parse_drawing(data, name, note=note, attachments=atts)
        store.save_ir(project_id, ir.model_dump(), stage="parsed", author=author)
        return ir.model_dump()

    return {"task_id": tasks.submit(project_id, "parse", job)}


@app.post("/api/projects/{project_id}/verify")
def verify(project_id: str, user: dict = Depends(current_user)):
    """自校验第二遍: 对照原图核对初步 IR 的尺寸/特征并重估置信度(异步任务)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    src = store.source_path(project_id)
    if not src or not src.exists():
        raise HTTPException(404, "项目或原图不存在")
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(404, "请先解析(parse)得到 IR")
    data, name = src.read_bytes(), src.name
    note, atts = store.get_note(project_id), store.load_attachments(project_id)
    author = user.get("username", "system")

    def job():
        verified = vision.verify_drawing(DesignIR(**ir_dict), data, name, note=note, attachments=atts)
        store.save_ir(project_id, verified.model_dump(), stage="verified", author=author)
        return verified.model_dump()

    return {"task_id": tasks.submit(project_id, "verify", job)}


@app.post("/api/projects/{project_id}/decompose")
def decompose_recommend(project_id: str, user: dict = Depends(current_user)):
    """对已解析 IR 做拆解推荐增强(异步任务)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(404, "请先解析(parse)得到 IR")
    author = user.get("username", "system")

    def job():
        enriched = decompose.enrich_with_recommendations(DesignIR(**ir_dict))
        store.save_ir(project_id, enriched.model_dump(), stage="decomposed", author=author)
        return enriched.model_dump()

    return {"task_id": tasks.submit(project_id, "decompose", job)}


@app.post("/api/projects/{project_id}/generate")
def generate(project_id: str, user: dict = Depends(current_user)):
    """据 IR 用 CAD 内核生成各零件几何(STEP/STL) + 校验(异步任务)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(404, "请先解析(parse)得到 IR")
    if not geometry.CADQUERY_AVAILABLE:
        raise HTTPException(
            503, "CadQuery 未安装，几何生成不可用。请 `pip install cadquery` 后重试。"
        )

    def job():
        ir = DesignIR(**ir_dict)
        results = geometry.generate_all(ir.parts, store.geometry_dir(project_id))
        payload = _geometry_payload(project_id, results)
        store.save_geometry_result(project_id, payload)
        store.sync_geometry(project_id)  # 同步到对象存储(Local 后端空操作)
        return payload

    return {"task_id": tasks.submit(project_id, "generate", job, cad=True)}


@app.post("/api/projects/{project_id}/drawings")
def drawings(project_id: str, user: dict = Depends(current_user)):
    """据 IR 用 CAD 内核生成各零件 2D 工程图(三视图 SVG + 下料 DXF,异步任务)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(404, "请先解析(parse)得到 IR")
    if not drawing2d.AVAILABLE:
        raise HTTPException(503, "CadQuery 未安装，2D 工程图生成不可用。")

    def job():
        ir = DesignIR(**ir_dict)
        results = drawing2d.generate_all(ir.parts, store.geometry_dir(project_id))
        payload = _drawings_payload(project_id, results)
        store.save_drawings_result(project_id, payload)
        store.sync_geometry(project_id)  # 同步到对象存储(Local 后端空操作)
        return payload

    return {"task_id": tasks.submit(project_id, "drawings", job, cad=True)}


@app.get("/api/projects/{project_id}/bom.csv")
def bom_csv(project_id: str):
    """导出 BOM 为 CSV(UTF-8 BOM，Excel 友好)。"""
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(404, "请先解析(parse)得到 IR")
    data = bom.to_csv(DesignIR(**ir_dict))
    store.audit(project_id, "export_bom_csv")
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="BOM_{project_id}.csv"'},
    )


@app.get("/api/projects/{project_id}/bom")
def bom_json(project_id: str):
    """BOM 行(JSON)供前端表格展示。"""
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(404, "请先解析(parse)得到 IR")
    return {"rows": bom.build_bom(DesignIR(**ir_dict))}


@app.put("/api/projects/{project_id}/ir")
def update_ir(project_id: str, ir: DesignIR, user: dict = Depends(current_user)):
    """保存人工校核/编辑后的 IR(交互式工作台改参后回存)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    store.save_ir(project_id, ir.model_dump(), stage="edited", author=user.get("username", "system"))
    return ir.model_dump()


@app.post("/api/projects/{project_id}/parts/{part_id}/regenerate")
def regenerate_part(project_id: str, part_id: str, user: dict = Depends(current_user)):
    """改参后单零件重生几何 + 2D 工程图(行内编辑闭环)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(404, "请先解析(parse)得到 IR")
    if not geometry.CADQUERY_AVAILABLE:
        raise HTTPException(503, "CadQuery 未安装。")
    ir = DesignIR(**ir_dict)
    part = next((p for p in ir.parts if p.part_id == part_id), None)
    if not part:
        raise HTTPException(404, f"零件 {part_id} 不存在")

    out_dir = store.geometry_dir(project_id)
    g = geometry.generate_part(part, out_dir)
    gp = store.load_geometry_result(project_id) or {"parts": []}
    g_entry = _geometry_payload(project_id, [g])["parts"][0]
    _upsert_part(gp, g_entry)
    store.save_geometry_result(project_id, gp)

    d = drawing2d.generate_drawings(part, out_dir)
    dp = store.load_drawings_result(project_id) or {"parts": []}
    d_entry = _drawings_payload(project_id, [d])["parts"][0]
    _upsert_part(dp, d_entry)
    store.save_drawings_result(project_id, dp)

    store.sync_geometry(project_id)  # 同步到对象存储(Local 后端空操作)
    store.audit(project_id, "regenerate_part", {"part_id": part_id})
    return {"geometry": g_entry, "drawings": d_entry}


# --------------------------------------------------------------------------- #
# 工艺拆解(把单个零件拆成结构化工艺路线,CAPP)
# --------------------------------------------------------------------------- #
def _geom_for_part(project_id: str, part_id: str):
    gp = store.load_geometry_result(project_id) or {}
    for p in gp.get("parts", []):
        if p.get("part_id") == part_id:
            return {"bbox": p.get("bbox"), "volume_mm3": p.get("volume_mm3"), "mass_g": p.get("mass_g")}
    return None


async def _read_attachments(attachments: List[UploadFile]):
    out = []
    for att in attachments or []:
        data = await att.read()
        if data:
            out.append((att.filename or "attachment", data))
    return out


@app.post("/api/projects/{project_id}/parts/{part_id}/process")
async def generate_process(
    project_id: str, part_id: str,
    note: str = Form(""),
    attachments: List[UploadFile] = File(default=[]),
    user: dict = Depends(current_user),
):
    """把某零件拆解成结构化工艺路线(调用 Claude,异步任务)。可附加说明/文件辅助。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(404, "请先解析(parse)得到 IR")
    ir = DesignIR(**ir_dict)
    part = next((p for p in ir.parts if p.part_id == part_id), None)
    if not part:
        raise HTTPException(404, f"零件 {part_id} 不存在")
    geom = _geom_for_part(project_id, part_id)
    author = user.get("username", "system")
    atts = await _read_attachments(attachments)

    def job():
        plan = process.decompose_process(part, overall=ir, geom=geom, note=note, attachments=atts)
        plan_dict = plan.model_dump()
        store.save_process(project_id, part_id, plan_dict, author=author)
        return {"plan": plan_dict, "validation": process.compute(plan_dict)}

    return {"task_id": tasks.submit(project_id, "process", job)}


@app.get("/api/projects/{project_id}/parts/{part_id}/process")
def get_process(project_id: str, part_id: str):
    """读取某零件已保存的工艺路线 + 确定性派生量(工时合计/依赖校验)。"""
    plan = store.load_process(project_id, part_id)
    return {"plan": plan, "validation": process.compute(plan) if plan else None}


@app.put("/api/projects/{project_id}/parts/{part_id}/process")
def update_process(project_id: str, part_id: str, plan: ProcessPlan,
                   user: dict = Depends(current_user)):
    """保存人工编辑后的工艺路线。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    plan.part_id = part_id
    plan.steps.sort(key=lambda s: s.step_no)
    plan_dict = plan.model_dump()
    store.save_process(project_id, part_id, plan_dict, author=user.get("username", "system"))
    return {"plan": plan_dict, "validation": process.compute(plan_dict)}


# --------------------------------------------------------------------------- #
# 成本分析(对单个零件做成本拆解,允许联网检索行情)
# --------------------------------------------------------------------------- #
@app.post("/api/projects/{project_id}/parts/{part_id}/cost")
async def generate_cost(
    project_id: str, part_id: str, quantity: int = 1,
    note: str = Form(""),
    attachments: List[UploadFile] = File(default=[]),
    user: dict = Depends(current_user),
):
    """对某零件做专业成本分析(Claude 联网检索行情,异步任务)。quantity 为核算批量;可附加说明/文件。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(404, "请先解析(parse)得到 IR")
    ir = DesignIR(**ir_dict)
    part = next((p for p in ir.parts if p.part_id == part_id), None)
    if not part:
        raise HTTPException(404, f"零件 {part_id} 不存在")
    geom = _geom_for_part(project_id, part_id)
    author = user.get("username", "system")
    qty = max(1, int(quantity or 1))
    atts = await _read_attachments(attachments)

    def job():
        analysis = cost.analyze_cost(part, overall=ir, geom=geom, quantity=qty,
                                     note=note, attachments=atts)
        a_dict = analysis.model_dump()
        store.save_cost(project_id, part_id, a_dict, author=author)
        return {"analysis": a_dict, "summary": cost.compute(a_dict)}

    return {"task_id": tasks.submit(project_id, "cost", job)}


@app.get("/api/projects/{project_id}/parts/{part_id}/cost")
def get_cost(project_id: str, part_id: str):
    """读取某零件已保存的成本分析 + 确定性重算(金额/合计/分类汇总)。"""
    analysis = store.load_cost(project_id, part_id)
    return {"analysis": analysis, "summary": cost.compute(analysis) if analysis else None}


@app.put("/api/projects/{project_id}/parts/{part_id}/cost")
def update_cost(project_id: str, part_id: str, analysis: CostAnalysis,
                user: dict = Depends(current_user)):
    """保存人工编辑后的成本分析。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    analysis.part_id = part_id
    a_dict = analysis.model_dump()
    store.save_cost(project_id, part_id, a_dict, author=user.get("username", "system"))
    return {"analysis": a_dict, "summary": cost.compute(a_dict)}


# --------------------------------------------------------------------------- #
# 版本管理 + 校核审签(PRD 6.5)
# --------------------------------------------------------------------------- #
@app.get("/api/projects/{project_id}/versions")
def list_versions(project_id: str):
    """版本快照列表(每次解析/校验/拆解/编辑/恢复自动留版),含审签状态。"""
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"versions": store.list_versions(project_id)}


@app.get("/api/projects/{project_id}/versions/{version}")
def get_version(project_id: str, version: int):
    """某一版本的完整快照(含 IR)。"""
    rec = store.get_version(project_id, version)
    if not rec:
        raise HTTPException(404, "版本不存在")
    return rec


@app.get("/api/projects/{project_id}/versions/{v_from}/diff/{v_to}")
def diff_versions(project_id: str, v_from: int, v_to: int):
    """两个版本的结构化差异(谁把哪个参数从多少改成多少)。"""
    a = store.get_version(project_id, v_from)
    b = store.get_version(project_id, v_to)
    if not a or not b:
        raise HTTPException(404, "版本不存在")
    return {
        "from": v_from, "to": v_to,
        "diff": versioning.diff_ir(a.get("ir"), b.get("ir")),
    }


@app.post("/api/projects/{project_id}/versions/{version}/submit")
def submit_version(project_id: str, version: int,
                   body: ReviewAction = Body(default=ReviewAction()),
                   user: dict = Depends(current_user)):
    """送审: draft -> in_review(提交人=当前登录用户)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    rec = store.set_version_status(project_id, version, "in_review",
                                   user.get("username", "system"), body.comment)
    if not rec:
        raise HTTPException(404, "版本不存在")
    return rec


@app.post("/api/projects/{project_id}/versions/{version}/approve")
def approve_version(project_id: str, version: int,
                    body: ReviewAction = Body(default=ReviewAction()),
                    user: dict = Depends(current_user)):
    """审签通过: -> approved(审签人=当前登录用户,实名留痕)。"""
    _require(user, auth.REVIEW_ROLES, "需要校核/审签或管理员权限")
    rec = store.set_version_status(project_id, version, "approved",
                                   user.get("username", "system"), body.comment)
    if not rec:
        raise HTTPException(404, "版本不存在")
    return rec


@app.post("/api/projects/{project_id}/versions/{version}/reject")
def reject_version(project_id: str, version: int,
                   body: ReviewAction = Body(default=ReviewAction()),
                   user: dict = Depends(current_user)):
    """审签驳回: -> rejected(审签人=当前登录用户,实名留痕)。"""
    _require(user, auth.REVIEW_ROLES, "需要校核/审签或管理员权限")
    rec = store.set_version_status(project_id, version, "rejected",
                                   user.get("username", "system"), body.comment)
    if not rec:
        raise HTTPException(404, "版本不存在")
    return rec


@app.post("/api/projects/{project_id}/versions/{version}/restore")
def restore_version(project_id: str, version: int, user: dict = Depends(current_user)):
    """把某历史版本的 IR 恢复为当前 IR(并自动留一个 restored 新版本)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    rec = store.get_version(project_id, version)
    if not rec or not rec.get("ir"):
        raise HTTPException(404, "版本不存在")
    store.save_ir(project_id, rec["ir"], stage=f"restored_from_v{version}",
                  author=user.get("username", "system"))
    store.audit(project_id, "restore_version", {"from": version, "by": user.get("username")})
    return rec["ir"]


# --------------------------------------------------------------------------- #
# 异步任务(轮询状态/进度/结果)
# --------------------------------------------------------------------------- #
@app.get("/api/projects/{project_id}/tasks/{task_id}")
def get_task(project_id: str, task_id: str):
    rec = store.get_task(project_id, task_id)
    if not rec:
        raise HTTPException(404, "任务不存在")
    return rec


@app.get("/api/projects/{project_id}/tasks")
def list_tasks(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"tasks": store.list_tasks(project_id)}


@app.get("/api/projects/{project_id}/tree")
def structure_tree(project_id: str):
    """层级结构树: 设备-总成-零件。"""
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(404, "请先解析(parse)得到 IR")
    return tree.build_tree(DesignIR(**ir_dict))


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    """项目全量状态(元数据 + IR + 几何 + 2D 图纸结果)。"""
    meta = store.load_meta(project_id)
    if not meta:
        raise HTTPException(404, "项目不存在")
    return {
        "meta": meta,
        "ir": store.load_ir(project_id),
        "geometry": store.load_geometry_result(project_id),
        "drawings": store.load_drawings_result(project_id),
    }


@app.get("/api/projects/{project_id}/audit")
def get_audit(project_id: str):
    """项目审计轨迹(可追溯): 每次解析/校验/生成/导出等动作的留痕。"""
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"audit": store.list_audit(project_id)}


@app.get("/api/projects/{project_id}/source")
def get_source(project_id: str):
    src = store.source_path(project_id)
    if not src or not src.exists():
        raise HTTPException(404, "原图不存在")
    return FileResponse(str(src))


@app.get("/api/projects/{project_id}/geometry/{filename}")
def get_geometry_file(project_id: str, filename: str):
    # store.geometry_file 内部按文件名取 .name(防目录穿越),并按需从对象存储回源
    path = store.geometry_file(project_id, filename)
    if not path or not path.exists():
        raise HTTPException(404, "几何文件不存在")
    return FileResponse(str(path))


# --------------------------------------------------------------------------- #
# 静态前端(挂在最后，避免覆盖 /api)
# --------------------------------------------------------------------------- #
FRONTEND_DIR = ROOT_DIR / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


@app.exception_handler(RuntimeError)
def runtime_error_handler(request, exc):  # pragma: no cover
    return JSONResponse(status_code=500, content={"detail": str(exc)})

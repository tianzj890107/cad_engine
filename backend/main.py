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
from .models.assembly import AssemblyPlan
from .models.approval import QuoteApproval
from .models.cleaning import CleaningPlan
from .models.cost import CostAnalysis
from .models.costest import CostEstimate
from .models.negotiation import NegotiationPlan
from .models.pricenego import PriceNegotiation
from .models.pricing import PricingPlan
from .models.ir import DesignIR
from .models.manufacturing import ManufacturingPlan
from .models.material import MaterialPlan, Supplier
from .models.process import ProcessPlan
from .models.production import EquipmentResource, ProductionPlan
from .models.summary import SummaryDoc
from .models.techprocess import TechProcessRecord
from .services import (
    approval as approval_svc, assembly, auth, bom, cleaning, cost, costest, decompose,
    drawing2d, geometry, manufacturing, material, negotiation, pricenego, pricing,
    process, production, step_import, summary as summary_svc, tasks, tree, versioning,
    vision,
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
# 材料定性与供应链拆解(技术工艺第 3 步)
#   - recommend: Claude 联网产出候选材料/配方/粉末要求(异步)
#   - PUT:       保存人工编辑后的计划
#   - confirm:   人工确认主体材料 / 金属化方案
#   - evaluate:  确定性供应商达标匹配
#   - timing:    记录起止时间与是否完成
# --------------------------------------------------------------------------- #
def _now_str() -> str:
    import time as _t
    return _t.strftime("%Y-%m-%d %H:%M:%S")


def _load_material_plan(project_id: str) -> MaterialPlan:
    saved = store.load_material(project_id)
    plan = MaterialPlan(**saved) if saved else MaterialPlan(project_id=project_id)
    plan.project_id = project_id
    return plan


@app.get("/api/projects/{project_id}/material")
def get_material(project_id: str):
    """读取该项目的材料定性与供应链拆解计划。"""
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"material": store.load_material(project_id)}


@app.post("/api/projects/{project_id}/material/recommend")
def recommend_material(project_id: str, note: str = Form(""),
                       user: dict = Depends(current_user)):
    """Claude 联网产出候选陶瓷主体材料/电极金属化配方/粉末要求(异步任务)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    author = user.get("username", "system")

    def job():
        rec = material.recommend(ir=ir, note=note, web=True)
        plan = _load_material_plan(project_id)
        # 把建议合并进计划(确定性):候选/默认选定/配方/粉末要求/来源
        plan.body.candidates = rec.body_candidates
        if rec.body_recommended and not plan.body.selected:
            plan.body.selected = rec.body_recommended
        if rec.body_rationale:
            plan.body.rationale = rec.body_rationale
        plan.metallization.paste = rec.paste
        plan.metallization.layers = rec.layers
        if rec.metallization_rationale:
            plan.metallization.rationale = rec.metallization_rationale
        if rec.requirements:
            plan.supply.requirements = rec.requirements
        plan.assumptions = rec.assumptions
        plan.open_questions = rec.open_questions
        plan.search_sources = rec.search_sources
        plan.updated_at = _now_str()
        d = plan.model_dump()
        store.save_material(project_id, d, author=author)
        return {"material": d}

    return {"task_id": tasks.submit(project_id, "material_recommend", job)}


@app.put("/api/projects/{project_id}/material")
def update_material(project_id: str, plan: MaterialPlan,
                    user: dict = Depends(current_user)):
    """保存人工编辑后的材料计划(选定材料/配方/粉末要求等)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    plan.project_id = project_id
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_material(project_id, d, author=user.get("username", "system"))
    return {"material": d}


@app.post("/api/projects/{project_id}/material/confirm")
def confirm_material(project_id: str, section: str,
                     user: dict = Depends(current_user)):
    """人工确认某一节(section=body|metallization),记录确认人与时间。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if section not in ("body", "metallization"):
        raise HTTPException(400, "section 必须为 body 或 metallization")
    plan = _load_material_plan(project_id)
    who = user.get("username", "system")
    if section == "body":
        if not plan.body.selected:
            raise HTTPException(400, "请先选定主体材料再确认")
        plan.body.confirmed = True
        plan.body.confirmed_by = who
        plan.body.confirmed_at = _now_str()
    else:
        plan.metallization.confirmed = True
        plan.metallization.confirmed_by = who
        plan.metallization.confirmed_at = _now_str()
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_material(project_id, d, author=who)
    return {"material": d}


@app.post("/api/projects/{project_id}/material/evaluate")
def evaluate_material(project_id: str, user: dict = Depends(current_user)):
    """确定性供应商达标匹配:用计划中的粉末要求 vs 供应商能力目录。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    plan = _load_material_plan(project_id)
    if not plan.supply.requirements:
        raise HTTPException(400, "尚无粉末纯度/粒径要求,请先做 AI 推荐或手动填写要求")
    result = material.evaluate(plan.supply.requirements, store.list_suppliers())
    plan.supply = result
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_material(project_id, d, author=user.get("username", "system"))
    return {"material": d}


@app.post("/api/projects/{project_id}/material/timing")
def material_timing(project_id: str, action: str, user: dict = Depends(current_user)):
    """记录该步骤起止时间与是否完成(action=start|finish)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if action not in ("start", "finish"):
        raise HTTPException(400, "action 必须为 start 或 finish")
    plan = _load_material_plan(project_id)
    if action == "start":
        plan.timing.status = "in_progress"
        plan.timing.started_at = _now_str()
        plan.timing.finished_at = None
        plan.timing.completed = False
    else:
        if not plan.timing.started_at:
            plan.timing.started_at = _now_str()
        plan.timing.status = "done"
        plan.timing.finished_at = _now_str()
        plan.timing.completed = True
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_material(project_id, d, author=user.get("username", "system"))
    return {"material": d}


# --------------------------------------------------------------------------- #
# 供应商能力目录(全局,种子可维护;供③达标匹配使用)
# --------------------------------------------------------------------------- #
@app.get("/api/suppliers")
def list_suppliers_ep():
    return {"suppliers": store.list_suppliers()}


@app.post("/api/suppliers")
def save_supplier_ep(supplier: Supplier, user: dict = Depends(current_user)):
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    return {"supplier": store.save_supplier(supplier.model_dump())}


@app.delete("/api/suppliers/{supplier_id}")
def delete_supplier_ep(supplier_id: str, user: dict = Depends(current_user)):
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    store.delete_supplier(supplier_id)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# 制造工艺路径规划和 BOM 编制(技术工艺第 4 步)
# --------------------------------------------------------------------------- #
def _load_manufacturing_plan(project_id: str) -> ManufacturingPlan:
    saved = store.load_manufacturing(project_id)
    plan = ManufacturingPlan(**saved) if saved else ManufacturingPlan(project_id=project_id)
    plan.project_id = project_id
    return plan


@app.get("/api/projects/{project_id}/manufacturing")
def get_manufacturing(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"manufacturing": store.load_manufacturing(project_id)}


@app.post("/api/projects/{project_id}/manufacturing/recommend")
def recommend_manufacturing(project_id: str, note: str = Form(""),
                            user: dict = Depends(current_user)):
    """Claude 联网产出核心工艺路径/附加工艺评估/工艺 BOM(异步,读 IR + 第3步材料计划)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    material_plan = store.load_material(project_id)
    author = user.get("username", "system")

    def job():
        rec = manufacturing.recommend(ir=ir, material_plan=material_plan, note=note, web=True)
        plan = _load_manufacturing_plan(project_id)
        plan.path.steps = rec.core_path
        if rec.path_summary:
            plan.path.summary = rec.path_summary
        plan.additional = rec.additional
        plan.bom.items = rec.bom
        if rec.bom_summary:
            plan.bom.summary = rec.bom_summary
        plan.assumptions = rec.assumptions
        plan.open_questions = rec.open_questions
        plan.search_sources = rec.search_sources
        plan.updated_at = _now_str()
        d = plan.model_dump()
        store.save_manufacturing(project_id, d, author=author)
        return {"manufacturing": d}

    return {"task_id": tasks.submit(project_id, "manufacturing_recommend", job)}


@app.put("/api/projects/{project_id}/manufacturing")
def update_manufacturing(project_id: str, plan: ManufacturingPlan,
                         user: dict = Depends(current_user)):
    """保存人工编辑后的制造工艺计划。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    plan.project_id = project_id
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_manufacturing(project_id, d, author=user.get("username", "system"))
    return {"manufacturing": d}


@app.post("/api/projects/{project_id}/manufacturing/confirm")
def confirm_manufacturing(project_id: str, section: str,
                          user: dict = Depends(current_user)):
    """人工确认某一节(section=path|bom),记录确认人与时间。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if section not in ("path", "bom"):
        raise HTTPException(400, "section 必须为 path 或 bom")
    plan = _load_manufacturing_plan(project_id)
    who = user.get("username", "system")
    target = plan.path if section == "path" else plan.bom
    if section == "path" and not plan.path.steps:
        raise HTTPException(400, "尚无工艺路径,请先做 AI 推荐")
    if section == "bom" and not plan.bom.items:
        raise HTTPException(400, "尚无工艺 BOM,请先做 AI 推荐")
    target.confirmed = True
    target.confirmed_by = who
    target.confirmed_at = _now_str()
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_manufacturing(project_id, d, author=who)
    return {"manufacturing": d}


@app.post("/api/projects/{project_id}/manufacturing/timing")
def manufacturing_timing(project_id: str, action: str, user: dict = Depends(current_user)):
    """记录该步骤起止时间与是否完成(action=start|finish)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if action not in ("start", "finish"):
        raise HTTPException(400, "action 必须为 start 或 finish")
    plan = _load_manufacturing_plan(project_id)
    if action == "start":
        plan.timing.status = "in_progress"
        plan.timing.started_at = _now_str()
        plan.timing.finished_at = None
        plan.timing.completed = False
    else:
        if not plan.timing.started_at:
            plan.timing.started_at = _now_str()
        plan.timing.status = "done"
        plan.timing.finished_at = _now_str()
        plan.timing.completed = True
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_manufacturing(project_id, d, author=user.get("username", "system"))
    return {"manufacturing": d}


@app.get("/api/projects/{project_id}/manufacturing/bom.csv")
def export_manufacturing_bom(project_id: str):
    """导出工艺 BOM 为 CSV。"""
    plan = store.load_manufacturing(project_id) or {}
    items = ((plan.get("bom") or {}).get("items")) or []
    csv_text = manufacturing.bom_csv(items)
    return Response(
        content=csv_text.encode("utf-8"),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="bom_{project_id}.csv"'},
    )


# --------------------------------------------------------------------------- #
# 清洗与洁净度管控方案制定(技术工艺第 5 步)
# --------------------------------------------------------------------------- #
def _load_cleaning_plan(project_id: str) -> CleaningPlan:
    saved = store.load_cleaning(project_id)
    plan = CleaningPlan(**saved) if saved else CleaningPlan(project_id=project_id)
    plan.project_id = project_id
    return plan


@app.get("/api/projects/{project_id}/cleaning")
def get_cleaning(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"cleaning": store.load_cleaning(project_id)}


@app.post("/api/projects/{project_id}/cleaning/recommend")
def recommend_cleaning(project_id: str, note: str = Form(""),
                       user: dict = Depends(current_user)):
    """Claude 联网依据图纸洁净度等级定制化学清洗+高纯水终漂洗+管控方案(异步)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    material_plan = store.load_material(project_id)
    # 把项目备注(可能含图纸洁净度标注)并入补充说明
    project_note = store.get_note(project_id)
    merged_note = "\n".join(x for x in [project_note, note] if x and x.strip())
    author = user.get("username", "system")

    def job():
        rec = cleaning.recommend(ir=ir, material_plan=material_plan, note=merged_note, web=True)
        plan = _load_cleaning_plan(project_id)
        plan.cleanliness_grade = rec.cleanliness_grade
        plan.grade_source = rec.grade_source
        plan.grade_notes = rec.grade_notes
        plan.chemical_steps = rec.chemical_steps
        plan.rinse_steps = rec.rinse_steps
        plan.controls = rec.controls
        plan.summary = rec.summary
        plan.assumptions = rec.assumptions
        plan.open_questions = rec.open_questions
        plan.search_sources = rec.search_sources
        plan.updated_at = _now_str()
        d = plan.model_dump()
        store.save_cleaning(project_id, d, author=author)
        return {"cleaning": d}

    return {"task_id": tasks.submit(project_id, "cleaning_recommend", job)}


@app.put("/api/projects/{project_id}/cleaning")
def update_cleaning(project_id: str, plan: CleaningPlan,
                    user: dict = Depends(current_user)):
    """保存人工编辑后的清洗方案。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    plan.project_id = project_id
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_cleaning(project_id, d, author=user.get("username", "system"))
    return {"cleaning": d}


@app.post("/api/projects/{project_id}/cleaning/confirm")
def confirm_cleaning(project_id: str, user: dict = Depends(current_user)):
    """人工确认清洗与洁净度管控方案,记录确认人与时间。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    plan = _load_cleaning_plan(project_id)
    if not plan.chemical_steps and not plan.rinse_steps:
        raise HTTPException(400, "尚无清洗方案,请先做 AI 推荐")
    who = user.get("username", "system")
    plan.confirmed = True
    plan.confirmed_by = who
    plan.confirmed_at = _now_str()
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_cleaning(project_id, d, author=who)
    return {"cleaning": d}


@app.post("/api/projects/{project_id}/cleaning/timing")
def cleaning_timing(project_id: str, action: str, user: dict = Depends(current_user)):
    """记录该步骤起止时间与是否完成(action=start|finish)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if action not in ("start", "finish"):
        raise HTTPException(400, "action 必须为 start 或 finish")
    plan = _load_cleaning_plan(project_id)
    if action == "start":
        plan.timing.status = "in_progress"
        plan.timing.started_at = _now_str()
        plan.timing.finished_at = None
        plan.timing.completed = False
    else:
        if not plan.timing.started_at:
            plan.timing.started_at = _now_str()
        plan.timing.status = "done"
        plan.timing.finished_at = _now_str()
        plan.timing.completed = True
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_cleaning(project_id, d, author=user.get("username", "system"))
    return {"cleaning": d}


# --------------------------------------------------------------------------- #
# 组装与检测方案制定(技术工艺第 6 步)
# --------------------------------------------------------------------------- #
def _load_assembly_plan(project_id: str) -> AssemblyPlan:
    saved = store.load_assembly(project_id)
    plan = AssemblyPlan(**saved) if saved else AssemblyPlan(project_id=project_id)
    plan.project_id = project_id
    return plan


@app.get("/api/projects/{project_id}/assembly")
def get_assembly(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"assembly": store.load_assembly(project_id)}


@app.post("/api/projects/{project_id}/assembly/recommend")
def recommend_assembly(project_id: str, note: str = Form(""),
                       user: dict = Depends(current_user)):
    """Claude 联网规划陶瓷-金属组装工艺并制定电性能/吸附力/气密性检测方案(异步)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    material_plan = store.load_material(project_id)
    manufacturing_plan = store.load_manufacturing(project_id)
    author = user.get("username", "system")

    def job():
        rec = assembly.recommend(ir=ir, material_plan=material_plan,
                                 manufacturing_plan=manufacturing_plan, note=note, web=True)
        plan = _load_assembly_plan(project_id)
        plan.assembly.method = rec.bonding_method
        plan.assembly.rationale = rec.bonding_rationale
        plan.assembly.steps = rec.assembly_steps
        plan.inspection.tests = rec.tests
        plan.summary = rec.summary
        plan.assumptions = rec.assumptions
        plan.open_questions = rec.open_questions
        plan.search_sources = rec.search_sources
        plan.updated_at = _now_str()
        d = plan.model_dump()
        store.save_assembly(project_id, d, author=author)
        return {"assembly": d}

    return {"task_id": tasks.submit(project_id, "assembly_recommend", job)}


@app.put("/api/projects/{project_id}/assembly")
def update_assembly(project_id: str, plan: AssemblyPlan,
                    user: dict = Depends(current_user)):
    """保存人工编辑后的组装与检测方案。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    plan.project_id = project_id
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_assembly(project_id, d, author=user.get("username", "system"))
    return {"assembly": d}


@app.post("/api/projects/{project_id}/assembly/confirm")
def confirm_assembly(project_id: str, section: str,
                     user: dict = Depends(current_user)):
    """人工确认某一节(section=assembly|inspection),记录确认人与时间。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if section not in ("assembly", "inspection"):
        raise HTTPException(400, "section 必须为 assembly 或 inspection")
    plan = _load_assembly_plan(project_id)
    who = user.get("username", "system")
    if section == "assembly":
        if not plan.assembly.steps:
            raise HTTPException(400, "尚无组装工艺,请先做 AI 推荐")
        target = plan.assembly
    else:
        if not plan.inspection.tests:
            raise HTTPException(400, "尚无检测方案,请先做 AI 推荐")
        target = plan.inspection
    target.confirmed = True
    target.confirmed_by = who
    target.confirmed_at = _now_str()
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_assembly(project_id, d, author=who)
    return {"assembly": d}


@app.post("/api/projects/{project_id}/assembly/timing")
def assembly_timing(project_id: str, action: str, user: dict = Depends(current_user)):
    """记录该步骤起止时间与是否完成(action=start|finish)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if action not in ("start", "finish"):
        raise HTTPException(400, "action 必须为 start 或 finish")
    plan = _load_assembly_plan(project_id)
    if action == "start":
        plan.timing.status = "in_progress"
        plan.timing.started_at = _now_str()
        plan.timing.finished_at = None
        plan.timing.completed = False
    else:
        if not plan.timing.started_at:
            plan.timing.started_at = _now_str()
        plan.timing.status = "done"
        plan.timing.finished_at = _now_str()
        plan.timing.completed = True
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_assembly(project_id, d, author=user.get("username", "system"))
    return {"assembly": d}


# --------------------------------------------------------------------------- #
# 产线匹配与产能评估(技术工艺第 7 步)
# --------------------------------------------------------------------------- #
def _load_production_plan(project_id: str) -> ProductionPlan:
    saved = store.load_production(project_id)
    plan = ProductionPlan(**saved) if saved else ProductionPlan(project_id=project_id)
    plan.project_id = project_id
    return plan


@app.get("/api/projects/{project_id}/production")
def get_production(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"production": store.load_production(project_id)}


@app.post("/api/projects/{project_id}/production/recommend")
def recommend_production(project_id: str, note: str = Form(""),
                         user: dict = Depends(current_user)):
    """Claude 联网依据制造工艺与设备台账做产线匹配/外协建议/产能评估(异步)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    manufacturing_plan = store.load_manufacturing(project_id)
    equipment = store.list_equipment()
    author = user.get("username", "system")

    def job():
        rec = production.recommend(ir=ir, manufacturing_plan=manufacturing_plan,
                                   equipment=equipment, note=note, web=True)
        plan = _load_production_plan(project_id)
        plan.requirements = rec.requirements
        plan.inhouse.matches = rec.inhouse_matches
        plan.outsourcing.plans = rec.outsourcing
        plan.capacity_summary = rec.capacity_summary
        plan.conclusion = rec.conclusion
        plan.assumptions = rec.assumptions
        plan.open_questions = rec.open_questions
        plan.search_sources = rec.search_sources
        plan.updated_at = _now_str()
        d = plan.model_dump()
        store.save_production(project_id, d, author=author)
        return {"production": d}

    return {"task_id": tasks.submit(project_id, "production_recommend", job)}


@app.put("/api/projects/{project_id}/production")
def update_production(project_id: str, plan: ProductionPlan,
                      user: dict = Depends(current_user)):
    """保存人工编辑后的产线匹配与产能评估。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    plan.project_id = project_id
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_production(project_id, d, author=user.get("username", "system"))
    return {"production": d}


@app.post("/api/projects/{project_id}/production/confirm")
def confirm_production(project_id: str, section: str,
                       user: dict = Depends(current_user)):
    """人工确认某一节(section=inhouse|outsourcing),记录确认人与时间。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if section not in ("inhouse", "outsourcing"):
        raise HTTPException(400, "section 必须为 inhouse 或 outsourcing")
    plan = _load_production_plan(project_id)
    who = user.get("username", "system")
    target = plan.inhouse if section == "inhouse" else plan.outsourcing
    target.confirmed = True
    target.confirmed_by = who
    target.confirmed_at = _now_str()
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_production(project_id, d, author=who)
    return {"production": d}


@app.post("/api/projects/{project_id}/production/timing")
def production_timing(project_id: str, action: str, user: dict = Depends(current_user)):
    """记录该步骤起止时间与是否完成(action=start|finish)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if action not in ("start", "finish"):
        raise HTTPException(400, "action 必须为 start 或 finish")
    plan = _load_production_plan(project_id)
    if action == "start":
        plan.timing.status = "in_progress"
        plan.timing.started_at = _now_str()
        plan.timing.finished_at = None
        plan.timing.completed = False
    else:
        if not plan.timing.started_at:
            plan.timing.started_at = _now_str()
        plan.timing.status = "done"
        plan.timing.finished_at = _now_str()
        plan.timing.completed = True
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_production(project_id, d, author=user.get("username", "system"))
    return {"production": d}


# --------------------------------------------------------------------------- #
# 设备资源台账(全局,种子可维护;供产线匹配/外协评估使用)
# --------------------------------------------------------------------------- #
@app.get("/api/equipment")
def list_equipment_ep():
    return {"equipment": store.list_equipment()}


@app.post("/api/equipment")
def save_equipment_ep(equipment: EquipmentResource, user: dict = Depends(current_user)):
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    return {"equipment": store.save_equipment(equipment.model_dump())}


@app.delete("/api/equipment/{equipment_id}")
def delete_equipment_ep(equipment_id: str, user: dict = Depends(current_user)):
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    store.delete_equipment(equipment_id)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# 技术工艺总结(技术工艺第 8 步:汇总各步 + 可编辑执行摘要 + 导出文档)
# --------------------------------------------------------------------------- #
def _load_summary_doc(project_id: str) -> SummaryDoc:
    saved = store.load_summary(project_id)
    doc = SummaryDoc(**saved) if saved else SummaryDoc(project_id=project_id)
    doc.project_id = project_id
    return doc


@app.get("/api/projects/{project_id}/summary")
def get_summary(project_id: str):
    """汇总各步结果 + 执行摘要(供总结页展示/编辑)。"""
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return summary_svc.aggregate(project_id)


@app.post("/api/projects/{project_id}/summary/recommend")
def recommend_summary(project_id: str, user: dict = Depends(current_user)):
    """Claude 依据各步汇总生成执行摘要(异步)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    author = user.get("username", "system")

    def job():
        agg = summary_svc.aggregate(project_id)
        rec = summary_svc.recommend(agg, web=False)
        doc = _load_summary_doc(project_id)
        doc.overview = rec.overview
        doc.highlights = rec.highlights
        doc.risks = rec.risks
        doc.conclusion = rec.conclusion
        doc.search_sources = rec.search_sources
        doc.updated_at = _now_str()
        d = doc.model_dump()
        store.save_summary(project_id, d, author=author)
        return {"summary": d}

    return {"task_id": tasks.submit(project_id, "summary_recommend", job)}


@app.put("/api/projects/{project_id}/summary")
def update_summary(project_id: str, doc: SummaryDoc, user: dict = Depends(current_user)):
    """保存人工编辑后的执行摘要。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    doc.project_id = project_id
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_summary(project_id, d, author=user.get("username", "system"))
    return {"summary": d}


@app.post("/api/projects/{project_id}/summary/confirm")
def confirm_summary(project_id: str, user: dict = Depends(current_user)):
    """确认技术工艺总结(定稿)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    doc = _load_summary_doc(project_id)
    who = user.get("username", "system")
    doc.confirmed = True
    doc.confirmed_by = who
    doc.confirmed_at = _now_str()
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_summary(project_id, d, author=who)
    return {"summary": d}


@app.post("/api/projects/{project_id}/summary/timing")
def summary_timing(project_id: str, action: str, user: dict = Depends(current_user)):
    """记录该步骤起止时间与是否完成(action=start|finish)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if action not in ("start", "finish"):
        raise HTTPException(400, "action 必须为 start 或 finish")
    doc = _load_summary_doc(project_id)
    if action == "start":
        doc.timing.status = "in_progress"
        doc.timing.started_at = _now_str()
        doc.timing.finished_at = None
        doc.timing.completed = False
    else:
        if not doc.timing.started_at:
            doc.timing.started_at = _now_str()
        doc.timing.status = "done"
        doc.timing.finished_at = _now_str()
        doc.timing.completed = True
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_summary(project_id, d, author=user.get("username", "system"))
    return {"summary": d}


@app.get("/api/projects/{project_id}/summary.html")
def export_summary_html(project_id: str):
    """导出技术工艺总结为可打印 HTML 文档(含各步全部结构化内容)。"""
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    agg = summary_svc.aggregate(project_id)
    return Response(content=summary_svc.render_html(agg), media_type="text/html; charset=utf-8")


@app.get("/api/projects/{project_id}/summary.md")
def export_summary_md(project_id: str):
    """导出技术工艺总结为 Markdown 文档。"""
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    agg = summary_svc.aggregate(project_id)
    return Response(
        content=summary_svc.render_markdown(agg).encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="summary_{project_id}.md"'},
    )


# --------------------------------------------------------------------------- #
# 技术工艺 / 报价 记录(「结束」步:最终确认并录入到管理列表)
# --------------------------------------------------------------------------- #
@app.get("/api/techprocess/records")
def list_records_ep(biz: str = ""):
    """管理列表数据源。biz=tech|quote 过滤;留空返回全部。"""
    items = store.list_records()
    if biz:
        items = [r for r in items if r.get("biz") == biz]
    items.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return {"records": items}


@app.post("/api/techprocess/records")
def register_record_ep(body: TechProcessRecord, user: dict = Depends(current_user)):
    """「结束」步最终确认:把当前技术工艺/报价录入到对应管理列表(按 project_id+biz 幂等)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    biz = body.biz or "tech"
    records = store.list_records()

    # 名称: 优先入参,否则取项目 IR 的器件名
    name = body.name
    if (not name or name == "未命名技术工艺") and body.project_id:
        ir = store.load_ir(body.project_id) or {}
        name = ir.get("device_name") or name
    name = name or "未命名技术工艺"

    # 状态: 优先入参,否则按总结是否定稿
    status = body.status
    if not status and body.project_id:
        summ = store.load_summary(body.project_id) or {}
        status = "已定稿" if summ.get("confirmed") else "已录入"
    status = status or "已录入"

    who = user.get("username", "system")
    # 幂等: 同一 project_id + biz 已录入则更新
    existing = next(
        (r for r in records
         if body.project_id and r.get("project_id") == body.project_id and r.get("biz") == biz),
        None,
    )
    if existing:
        existing.update({"name": name, "status": status, "note": body.note or existing.get("note"),
                         "updated_at": _now_str()})
        return {"record": store.save_record(existing)}

    prefix = "BJ" if biz == "quote" else "GY"
    seq = sum(1 for r in records if r.get("biz") == biz) + 1
    rec = {
        "id": "rec_" + __import__("uuid").uuid4().hex[:8],
        "code": f"{prefix}{__import__('time').strftime('%Y%m%d')}-{seq:03d}",
        "name": name, "project_id": body.project_id, "biz": biz, "status": status,
        "owner": who, "created_at": _now_str(), "note": body.note, "editable": True,
    }
    return {"record": store.save_record(rec)}


@app.delete("/api/techprocess/records/{record_id}")
def delete_record_ep(record_id: str, user: dict = Depends(current_user)):
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    store.delete_record(record_id)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# 成本测算(报价流程第 1 步)
# --------------------------------------------------------------------------- #
def _load_costest(project_id: str) -> CostEstimate:
    saved = store.load_costest(project_id)
    doc = CostEstimate(**saved) if saved else CostEstimate(project_id=project_id)
    doc.project_id = project_id
    return doc


def _save_costest_with_totals(doc: CostEstimate, project_id: str, author: str) -> dict:
    doc.project_id = project_id
    doc.totals = costest.compute(doc.model_dump())
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_costest(project_id, d, author=author)
    return d


@app.get("/api/projects/{project_id}/costest")
def get_costest(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    saved = store.load_costest(project_id)
    totals = costest.compute(saved).model_dump() if saved else None
    return {"costest": saved, "totals": totals}


@app.post("/api/projects/{project_id}/costest/recommend")
def recommend_costest(project_id: str, note: str = Form(""),
                      user: dict = Depends(current_user)):
    """Claude 联网依据 IR/材料/制造工艺与 BOM 做材料/制造/技术附加成本测算(异步)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    material_plan = store.load_material(project_id)
    manufacturing_plan = store.load_manufacturing(project_id)
    author = user.get("username", "system")

    def job():
        rec = costest.recommend(ir=ir, material_plan=material_plan,
                                manufacturing_plan=manufacturing_plan, note=note, web=True)
        doc = _load_costest(project_id)
        doc.material_costs = rec.material_costs
        doc.manufacturing_costs = rec.manufacturing_costs
        doc.technical_costs = rec.technical_costs
        doc.market_notes = rec.market_notes
        doc.summary = rec.summary
        doc.assumptions = rec.assumptions
        doc.open_questions = rec.open_questions
        doc.search_sources = rec.search_sources
        d = _save_costest_with_totals(doc, project_id, author)
        return {"costest": d, "totals": d["totals"]}

    return {"task_id": tasks.submit(project_id, "costest_recommend", job)}


@app.put("/api/projects/{project_id}/costest")
def update_costest(project_id: str, doc: CostEstimate, user: dict = Depends(current_user)):
    """保存人工编辑后的成本测算(平台重算合计)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    d = _save_costest_with_totals(doc, project_id, user.get("username", "system"))
    return {"costest": d, "totals": d["totals"]}


@app.post("/api/projects/{project_id}/costest/confirm")
def confirm_costest(project_id: str, user: dict = Depends(current_user)):
    """确认成本测算(定稿,供后续定价使用)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    doc = _load_costest(project_id)
    if not (doc.material_costs or doc.manufacturing_costs or doc.technical_costs):
        raise HTTPException(400, "尚无成本明细,请先做 AI 测算")
    who = user.get("username", "system")
    doc.confirmed = True
    doc.confirmed_by = who
    doc.confirmed_at = _now_str()
    d = _save_costest_with_totals(doc, project_id, who)
    return {"costest": d, "totals": d["totals"]}


@app.post("/api/projects/{project_id}/costest/timing")
def costest_timing(project_id: str, action: str, user: dict = Depends(current_user)):
    """记录该步骤起止时间与是否完成(action=start|finish)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if action not in ("start", "finish"):
        raise HTTPException(400, "action 必须为 start 或 finish")
    doc = _load_costest(project_id)
    if action == "start":
        doc.timing.status = "in_progress"
        doc.timing.started_at = _now_str()
        doc.timing.finished_at = None
        doc.timing.completed = False
    else:
        if not doc.timing.started_at:
            doc.timing.started_at = _now_str()
        doc.timing.status = "done"
        doc.timing.finished_at = _now_str()
        doc.timing.completed = True
    d = _save_costest_with_totals(doc, project_id, user.get("username", "system"))
    return {"costest": d, "totals": d["totals"]}


@app.get("/api/projects/{project_id}/costest.csv")
def export_costest_csv(project_id: str):
    """导出成本测算为 CSV。"""
    saved = store.load_costest(project_id)
    if not saved:
        raise HTTPException(404, "尚无成本测算")
    return Response(
        content=costest.to_csv(saved).encode("utf-8"),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="costest_{project_id}.csv"'},
    )


# --------------------------------------------------------------------------- #
# 定价方案制定(报价流程第 2 步:成本加成 + 维度调整 + 销售测算→财务审核)
# --------------------------------------------------------------------------- #
class FinanceReview(BaseModel):
    decision: str = "approve"   # approve | reject
    comment: str = ""


def _load_pricing(project_id: str) -> PricingPlan:
    saved = store.load_pricing(project_id)
    doc = PricingPlan(**saved) if saved else PricingPlan(project_id=project_id)
    doc.project_id = project_id
    return doc


def _save_pricing_calc(doc: PricingPlan, project_id: str, author: str) -> dict:
    doc.project_id = project_id
    doc.updated_at = _now_str()
    d = pricing.compute(doc.model_dump())
    store.save_pricing(project_id, d, author=author)
    return d


@app.get("/api/projects/{project_id}/pricing")
def get_pricing(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"pricing": store.load_pricing(project_id)}


@app.post("/api/projects/{project_id}/pricing/recommend")
def recommend_pricing(project_id: str, note: str = Form(""),
                      user: dict = Depends(current_user)):
    """Claude 联网依据成本测算结果给出费率与各维度调整建议(异步);平台确定性算价。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    ce = store.load_costest(project_id)
    if ce and not ce.get("totals"):
        ce["totals"] = costest.compute(ce).model_dump()
    author = user.get("username", "system")

    def job():
        rec = pricing.recommend(ir=ir, costest=ce, note=note, web=True)
        doc = _load_pricing(project_id)
        # 成本基数取自成本测算(确定性)
        totals = (ce or {}).get("totals") or {}
        doc.costs.material_cost = float(totals.get("material_total", 0) or 0)
        doc.costs.production_cost = float(totals.get("manufacturing_total", 0) or 0)
        if rec.management_rate is not None:
            doc.costs.management_rate = rec.management_rate
        if rec.markup_rate is not None:
            doc.markup_rate = rec.markup_rate
        doc.factors = rec.factors
        doc.summary = rec.summary
        doc.assumptions = rec.assumptions
        doc.open_questions = rec.open_questions
        doc.search_sources = rec.search_sources
        d = _save_pricing_calc(doc, project_id, author)
        return {"pricing": d}

    return {"task_id": tasks.submit(project_id, "pricing_recommend", job)}


@app.put("/api/projects/{project_id}/pricing")
def update_pricing(project_id: str, doc: PricingPlan, user: dict = Depends(current_user)):
    """保存人工编辑后的定价方案(平台重算价格)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    d = _save_pricing_calc(doc, project_id, user.get("username", "system"))
    return {"pricing": d}


@app.post("/api/projects/{project_id}/pricing/submit")
def submit_pricing(project_id: str, user: dict = Depends(current_user)):
    """销售测算后提交财务审核。"""
    _require(user, auth.WRITE_ROLES, "需要销售/工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    doc = _load_pricing(project_id)
    if not doc.factors and not doc.costs.material_cost:
        raise HTTPException(400, "尚无定价内容,请先生成/填写定价方案")
    who = user.get("username", "system")
    doc.approval.status = "submitted"
    doc.approval.sales_by = who
    doc.approval.sales_at = _now_str()
    doc.approval.finance_by = None
    doc.approval.finance_at = None
    doc.approval.finance_comment = None
    d = _save_pricing_calc(doc, project_id, who)
    return {"pricing": d}


@app.post("/api/projects/{project_id}/pricing/review")
def review_pricing(project_id: str, body: FinanceReview, user: dict = Depends(current_user)):
    """财务负责人审核确认(通过/驳回)。"""
    _require(user, auth.REVIEW_ROLES, "需要财务/审核或管理员权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(400, "decision 必须为 approve 或 reject")
    doc = _load_pricing(project_id)
    if doc.approval.status != "submitted":
        raise HTTPException(400, "当前状态不可审核(需先由销售提交)")
    who = user.get("username", "system")
    doc.approval.status = "approved" if body.decision == "approve" else "rejected"
    doc.approval.finance_by = who
    doc.approval.finance_at = _now_str()
    doc.approval.finance_comment = body.comment
    d = _save_pricing_calc(doc, project_id, who)
    return {"pricing": d}


@app.post("/api/projects/{project_id}/pricing/timing")
def pricing_timing(project_id: str, action: str, user: dict = Depends(current_user)):
    """记录该步骤起止时间与是否完成(action=start|finish)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if action not in ("start", "finish"):
        raise HTTPException(400, "action 必须为 start 或 finish")
    doc = _load_pricing(project_id)
    if action == "start":
        doc.timing.status = "in_progress"
        doc.timing.started_at = _now_str()
        doc.timing.finished_at = None
        doc.timing.completed = False
    else:
        if not doc.timing.started_at:
            doc.timing.started_at = _now_str()
        doc.timing.status = "done"
        doc.timing.finished_at = _now_str()
        doc.timing.completed = True
    d = _save_pricing_calc(doc, project_id, user.get("username", "system"))
    return {"pricing": d}


# --------------------------------------------------------------------------- #
# 商务及谈判策略(报价流程第 3 步)
# --------------------------------------------------------------------------- #
def _load_negotiation(project_id: str) -> NegotiationPlan:
    saved = store.load_negotiation(project_id)
    doc = NegotiationPlan(**saved) if saved else NegotiationPlan(project_id=project_id)
    doc.project_id = project_id
    return doc


@app.get("/api/projects/{project_id}/negotiation")
def get_negotiation(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"negotiation": store.load_negotiation(project_id)}


@app.post("/api/projects/{project_id}/negotiation/recommend")
def recommend_negotiation(project_id: str, note: str = Form(""),
                          user: dict = Depends(current_user)):
    """Claude 依据定价结果产出商务条款与分客户类型谈判策略/授权(异步)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    pricing_plan = store.load_pricing(project_id)
    author = user.get("username", "system")

    def job():
        rec = negotiation.recommend(ir=ir, pricing=pricing_plan, note=note, web=False)
        doc = _load_negotiation(project_id)
        doc.terms = rec.terms
        doc.strategies = rec.strategies
        doc.summary = rec.summary
        doc.assumptions = rec.assumptions
        doc.open_questions = rec.open_questions
        doc.search_sources = rec.search_sources
        doc.updated_at = _now_str()
        d = doc.model_dump()
        store.save_negotiation(project_id, d, author=author)
        return {"negotiation": d}

    return {"task_id": tasks.submit(project_id, "negotiation_recommend", job)}


@app.put("/api/projects/{project_id}/negotiation")
def update_negotiation(project_id: str, doc: NegotiationPlan, user: dict = Depends(current_user)):
    """保存人工编辑后的商务及谈判策略。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    doc.project_id = project_id
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_negotiation(project_id, d, author=user.get("username", "system"))
    return {"negotiation": d}


@app.post("/api/projects/{project_id}/negotiation/confirm")
def confirm_negotiation(project_id: str, user: dict = Depends(current_user)):
    """确认商务及谈判策略。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    doc = _load_negotiation(project_id)
    if not (doc.terms or doc.strategies):
        raise HTTPException(400, "尚无内容,请先做 AI 生成")
    who = user.get("username", "system")
    doc.confirmed = True
    doc.confirmed_by = who
    doc.confirmed_at = _now_str()
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_negotiation(project_id, d, author=who)
    return {"negotiation": d}


@app.post("/api/projects/{project_id}/negotiation/timing")
def negotiation_timing(project_id: str, action: str, user: dict = Depends(current_user)):
    """记录该步骤起止时间与是否完成(action=start|finish)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if action not in ("start", "finish"):
        raise HTTPException(400, "action 必须为 start 或 finish")
    doc = _load_negotiation(project_id)
    if action == "start":
        doc.timing.status = "in_progress"
        doc.timing.started_at = _now_str()
        doc.timing.finished_at = None
        doc.timing.completed = False
    else:
        if not doc.timing.started_at:
            doc.timing.started_at = _now_str()
        doc.timing.status = "done"
        doc.timing.finished_at = _now_str()
        doc.timing.completed = True
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_negotiation(project_id, d, author=user.get("username", "system"))
    return {"negotiation": d}


# --------------------------------------------------------------------------- #
# 价格协商及谈判(报价流程第 4 步)
# --------------------------------------------------------------------------- #
def _load_pricenego(project_id: str) -> PriceNegotiation:
    saved = store.load_pricenego(project_id)
    doc = PriceNegotiation(**saved) if saved else PriceNegotiation(project_id=project_id)
    doc.project_id = project_id
    return doc


@app.get("/api/projects/{project_id}/pricenego")
def get_pricenego(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"pricenego": store.load_pricenego(project_id)}


@app.post("/api/projects/{project_id}/pricenego/recommend")
def recommend_pricenego(project_id: str, note: str = Form(""),
                        user: dict = Depends(current_user)):
    """Claude 产出初步报价单/阶梯价格/调价联动/特殊条款建议(异步,联网取行情)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    pricing_plan = store.load_pricing(project_id)
    negotiation_plan = store.load_negotiation(project_id)
    author = user.get("username", "system")

    def job():
        rec = pricenego.recommend(ir=ir, pricing=pricing_plan,
                                  negotiation=negotiation_plan, note=note, web=True)
        doc = _load_pricenego(project_id)
        doc.initial_quote = rec.initial_quote
        doc.tiered_prices = rec.tiered_prices
        doc.price_linkage = rec.price_linkage
        doc.special_terms = rec.special_terms
        doc.summary = rec.summary
        doc.assumptions = rec.assumptions
        doc.open_questions = rec.open_questions
        doc.search_sources = rec.search_sources
        doc.updated_at = _now_str()
        d = doc.model_dump()
        store.save_pricenego(project_id, d, author=author)
        return {"pricenego": d}

    return {"task_id": tasks.submit(project_id, "pricenego_recommend", job)}


@app.put("/api/projects/{project_id}/pricenego")
def update_pricenego(project_id: str, doc: PriceNegotiation, user: dict = Depends(current_user)):
    """保存人工编辑后的价格协商(含新增的协商轮次)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    doc.project_id = project_id
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_pricenego(project_id, d, author=user.get("username", "system"))
    return {"pricenego": d}


@app.post("/api/projects/{project_id}/pricenego/confirm")
def confirm_pricenego(project_id: str, user: dict = Depends(current_user)):
    """确认价格协商结果(达成)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    doc = _load_pricenego(project_id)
    who = user.get("username", "system")
    doc.confirmed = True
    doc.confirmed_by = who
    doc.confirmed_at = _now_str()
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_pricenego(project_id, d, author=who)
    return {"pricenego": d}


@app.post("/api/projects/{project_id}/pricenego/timing")
def pricenego_timing(project_id: str, action: str, user: dict = Depends(current_user)):
    """记录该步骤起止时间与是否完成(action=start|finish)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if action not in ("start", "finish"):
        raise HTTPException(400, "action 必须为 start 或 finish")
    doc = _load_pricenego(project_id)
    if action == "start":
        doc.timing.status = "in_progress"
        doc.timing.started_at = _now_str()
        doc.timing.finished_at = None
        doc.timing.completed = False
    else:
        if not doc.timing.started_at:
            doc.timing.started_at = _now_str()
        doc.timing.status = "done"
        doc.timing.finished_at = _now_str()
        doc.timing.completed = True
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_pricenego(project_id, d, author=user.get("username", "system"))
    return {"pricenego": d}


# --------------------------------------------------------------------------- #
# 报价审批与决策(报价流程第 6 步:分级审批)
# --------------------------------------------------------------------------- #
class ApprovalAction(BaseModel):
    decision: str = "approve"   # approve | reject
    comment: str = ""


def _load_approval(project_id: str) -> QuoteApproval:
    saved = store.load_approval(project_id)
    doc = QuoteApproval(**saved) if saved else QuoteApproval(project_id=project_id)
    doc.project_id = project_id
    return doc


@app.get("/api/projects/{project_id}/approval")
def get_approval(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"approval": store.load_approval(project_id)}


@app.post("/api/projects/{project_id}/approval/recommend")
def recommend_approval(project_id: str, note: str = Form(""),
                       user: dict = Depends(current_user)):
    """Claude 研判应走的审批级别;平台据级别生成审批链(异步)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    pricing_plan = store.load_pricing(project_id)
    pn = store.load_pricenego(project_id)
    author = user.get("username", "system")

    def job():
        rec = approval_svc.recommend(ir=ir, pricing=pricing_plan, pricenego=pn, note=note, web=False)
        doc = _load_approval(project_id)
        doc.level = rec.level
        doc.level_reason = rec.level_reason
        doc.classification = rec.classification
        doc.summary = rec.summary
        doc.assumptions = rec.assumptions
        doc.open_questions = rec.open_questions
        doc.search_sources = rec.search_sources
        # 重置审批链为新级别(草稿态)
        doc.chain = approval_svc.build_chain(rec.level)
        doc.status = "draft"
        doc.decision_note = None
        doc.updated_at = _now_str()
        d = doc.model_dump()
        store.save_approval(project_id, d, author=author)
        return {"approval": d}

    return {"task_id": tasks.submit(project_id, "approval_recommend", job)}


@app.post("/api/projects/{project_id}/approval/level")
def set_approval_level(project_id: str, level: int, user: dict = Depends(current_user)):
    """人工设定/调整审批级别(重建审批链,回到草稿态)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if level not in (1, 2, 3):
        raise HTTPException(400, "level 必须为 1/2/3")
    doc = _load_approval(project_id)
    doc.level = level
    doc.chain = approval_svc.build_chain(level)
    doc.status = "draft"
    doc.decision_note = None
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_approval(project_id, d, author=user.get("username", "system"))
    return {"approval": d}


@app.post("/api/projects/{project_id}/approval/submit")
def submit_approval(project_id: str, user: dict = Depends(current_user)):
    """提交内部审批(进入审批中,首个节点待审)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    doc = _load_approval(project_id)
    if not doc.chain:
        doc.chain = approval_svc.build_chain(doc.level)
    doc.status = "in_review"
    for i, n in enumerate(doc.chain):
        n.status = "pending" if i == 0 else "waiting"
        n.approver = None
        n.at = None
        n.comment = None
    doc.decision_note = None
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_approval(project_id, d, author=user.get("username", "system"))
    return {"approval": d}


class ApprovalSend(BaseModel):
    approvers: list[str] = []
    comment: str | None = None


@app.post("/api/projects/{project_id}/approval/send")
def send_approval(project_id: str, body: ApprovalSend, user: dict = Depends(current_user)):
    """选择审批人并「发送审批流」:标记审批中、记录送审人与时间;审批(通过/驳回)由审批人在别处完成,
    不在本报价单表单内做决策。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    approvers = [a.strip() for a in (body.approvers or []) if a and a.strip()]
    if not approvers:
        raise HTTPException(400, "请至少选择一位审批人")
    from .models.approval import ApprovalNode
    doc = _load_approval(project_id)
    doc.approvers = approvers
    doc.sent_at = _now_str()
    doc.status = "in_review"
    # 用所选审批人直接生成审批链(首个待审,其余待前序)
    doc.chain = [ApprovalNode(seq=i + 1, role=r, status=("pending" if i == 0 else "waiting"))
                 for i, r in enumerate(approvers)]
    if body.comment:
        doc.decision_note = body.comment
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_approval(project_id, d, author=user.get("username", "system"))
    return {"approval": d}


@app.post("/api/projects/{project_id}/approval/act")
def act_approval(project_id: str, body: ApprovalAction, user: dict = Depends(current_user)):
    """逐级审批:对当前待审节点 通过/驳回。需审核/管理权限。"""
    _require(user, auth.REVIEW_ROLES, "需要审核/管理(总监/财务/总经理)权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(400, "decision 必须为 approve 或 reject")
    doc = _load_approval(project_id)
    if doc.status != "in_review":
        raise HTTPException(400, "当前不在审批中(请先提交审批)")
    idx = next((i for i, n in enumerate(doc.chain) if n.status == "pending"), None)
    if idx is None:
        raise HTTPException(400, "没有待审节点")
    who = user.get("username", "system")
    node = doc.chain[idx]
    node.approver = who
    node.at = _now_str()
    node.comment = body.comment
    if body.decision == "reject":
        node.status = "rejected"
        doc.status = "rejected"
        doc.decision_note = f"{node.role} 驳回:{body.comment or ''}"
    else:
        node.status = "approved"
        if idx + 1 < len(doc.chain):
            doc.chain[idx + 1].status = "pending"
        else:
            doc.status = "approved"
            doc.decision_note = "全部审批通过,可正式报价/签约"
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_approval(project_id, d, author=who)
    return {"approval": d}


@app.post("/api/projects/{project_id}/approval/timing")
def approval_timing(project_id: str, action: str, user: dict = Depends(current_user)):
    """记录该步骤起止时间与是否完成(action=start|finish)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if action not in ("start", "finish"):
        raise HTTPException(400, "action 必须为 start 或 finish")
    doc = _load_approval(project_id)
    if action == "start":
        doc.timing.status = "in_progress"
        doc.timing.started_at = _now_str()
        doc.timing.finished_at = None
        doc.timing.completed = False
    else:
        if not doc.timing.started_at:
            doc.timing.started_at = _now_str()
        doc.timing.status = "done"
        doc.timing.finished_at = _now_str()
        doc.timing.completed = True
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_approval(project_id, d, author=user.get("username", "system"))
    return {"approval": d}


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
# 子应用目录:技术工艺管理 / 商机报价管理等前端应用统一放在 apps/<name>/ 下,
# 由本服务在 /apps/<name>/ 提供;它们与 /api/* 同源,因此共用同一套后端接口。
# 必须在挂载根目录 "/" 之前注册,否则会被 "/" 这个兜底挂载吞掉。
APPS_DIR = ROOT_DIR / "apps"
if APPS_DIR.exists():
    app.mount("/apps", StaticFiles(directory=str(APPS_DIR), html=True), name="apps")

FRONTEND_DIR = ROOT_DIR / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


@app.exception_handler(RuntimeError)
def runtime_error_handler(request, exc):  # pragma: no cover
    return JSONResponse(status_code=500, content={"detail": str(exc)})

"""
FastAPI 应用: 图纸解析与生成平台后端。

流程: 上传原图 -> Claude 解析为 IR -> Claude 拆解推荐增强 -> CAD 内核生成几何
       -> 前端展示(原图/拆解树/3D 查看器/校验告警/推荐)。

所有阶段结果都落盘(见 storage.store)，形成可追溯证据链。
"""
from __future__ import annotations

import json
import hashlib
import re
import threading
import time
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import (
    Body, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .config import (
    AUTH_ENABLED, AUTH_AUTO_ADMIN, CORS_ALLOW_ORIGINS, LLM_PROVIDER, LOGIN_MAX_ATTEMPTS,
    LOGIN_WINDOW_SECONDS, MAX_UPLOAD_BYTES, ROOT_DIR,
    QWEN_MODEL, QWEN_TEXT_MODEL, DEFAULT_ADMIN_USER, DEFAULT_ADMIN_PASSWORD,
    AUTH_SECRET, TASK_RECOVER_ON_START,
    active_model, active_text_model,
)
from .models.assembly import AssemblyPlan
from .models.approval import QuoteApproval
from .models.ai import VerificationPatch, VerificationPatchDecision
from .models.cleaning import CleaningPlan
from .models.cost import CostAnalysis
from .models.costest import CostEstimate
from .models.negotiation import NegotiationPlan
from .models.pricenego import PriceNegotiation
from .models.pricing import PricingPlan
from .models.ir import DesignIR, Material
from .models.manufacturing import ManufacturingPlan
from .models.material import MaterialPlan, Supplier
from .models.model_lookup import ModelLookupResult
from .models.process import ProcessPlan
from .models.production import EquipmentResource, ProductionPlan
from .models.summary import SummaryDoc
from .models.techprocess import TechProcessRecord
from .models.workflow import (
    ProcessReport, PublishAction, ReportDistributionSettings, RequirementDoc, RequirementDocumentExtraction,
    WorkflowAction, WorkflowReview,
)
from .services import (
    approval as approval_svc, assembly, auth, bom, cleaning, cost, costest, decompose,
    drawing2d, geometry, manufacturing, material, negotiation, pricenego, pricing,
    process, production, requirement_extract, step_import, summary as summary_svc, tasks, tree,
    versioning, vision, qwen_client, llm_client, model_lookup, requirement_pdf,
)
from .storage import store
from .time_utils import now_cst_str


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


class RegisterBody(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=60)
    requested_role: str = "viewer"


class UpdateMyProfile(BaseModel):
    display_name: str = Field(default="", max_length=60)
    current_password: str = Field(default="", max_length=128)
    new_password: str = Field(default="", max_length=128)


class UpdateUserRole(BaseModel):
    role: str


class RuntimeLlmSettingsBody(BaseModel):
    """管理员在首页调整 Qwen 模型池；API Key 仅允许写入，绝不回显。"""
    api_key: str = Field(default="", max_length=512)
    base_url: str = Field(default="", max_length=500)
    vision_models: List[str] = Field(default_factory=list, max_length=20)
    text_models: List[str] = Field(default_factory=list, max_length=20)
    web_search_models: List[str] = Field(default_factory=list, max_length=20)
    provider: str = Field(default="", max_length=40)


class ProjectManageBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class CustomerCreditUpdate(BaseModel):
    """客户信用等级为销售主数据，只接受固定四档。"""
    customer_credit: str = Field(pattern="^[ABCD]$")


class RequirementAiCheckItem(BaseModel):
    """Qwen 对确认表单中单项的结论；服务端会再规整为固定检查清单。"""
    item: str = ""
    status: str = "need_info"
    detail: str = ""


class RequirementAiCheckResult(BaseModel):
    """手动 AI 检查的最小输出契约，刻意保持简短以控制 token 成本。"""
    summary: str = ""
    items: List[RequirementAiCheckItem] = Field(default_factory=list)


class WorkbenchChatTurn(BaseModel):
    role: str = "user"
    content: str = Field(default="", max_length=1600)


class WorkbenchChatRequest(BaseModel):
    """2.1 工作台的文字对话请求；项目图纸本身不会被重新发送给视觉模型。"""
    message: str = Field(min_length=1, max_length=1600)
    part_id: str = Field(default="", max_length=120)
    history: List[WorkbenchChatTurn] = Field(default_factory=list, max_length=6)
    page_context: str = Field(default="", max_length=160)


class ProjectChatRequest(BaseModel):
    """项目级对话：跨需求、解析、报告页面共用同一段留痕。"""
    message: str = Field(min_length=1, max_length=1600)
    page_context: str = Field(default="", max_length=160)


class WorkbenchFeatureEdit(BaseModel):
    feature_index: int = Field(ge=0, le=100)
    field: str = Field(min_length=1, max_length=40)
    value: float


class WorkbenchPartEdit(BaseModel):
    """AI 对话可提出的受控零件修改；服务端仍按特征类型二次校验。"""
    should_apply: bool = False
    name: Optional[str] = Field(default=None, max_length=160)
    quantity: Optional[int] = Field(default=None, ge=1, le=100000)
    material_spec: Optional[str] = Field(default=None, max_length=160)
    feature_updates: List[WorkbenchFeatureEdit] = Field(default_factory=list, max_length=30)
    explanation: str = Field(default="", max_length=1000)

    @field_validator("feature_updates", mode="before")
    @classmethod
    def normalize_text_feature_updates(cls, value):
        # 模型偶尔用一句话概括修改，不允许把它猜测成数值更新；保留说明而不执行。
        return value if isinstance(value, list) else []


class WorkbenchChatAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=4000)
    edit: Optional[WorkbenchPartEdit] = None

    @field_validator("edit", mode="before")
    @classmethod
    def normalize_text_edit(cls, value):
        if isinstance(value, str):
            return {"should_apply": False, "explanation": value[:1000]}
        return value


class ProjectChatAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=4000)


class ModelLookupConfirmation(BaseModel):
    candidate_model: str = Field(min_length=1, max_length=80)
    decision: str = Field(pattern="^(confirmed|rejected)$")
    note: str = Field(default="", max_length=600)


_CONFIRMATION_FIELDS = ("confirmed", "confirmed_by", "confirmed_at")


def _digest_value(value) -> str:
    """为任务输入和业务快照生成稳定摘要，避免把原文/附件写进任务记录。"""
    def normalize(item):
        if isinstance(item, BaseModel):
            return normalize(item.model_dump())
        if isinstance(item, bytes):
            return {"__bytes_sha256__": hashlib.sha256(item).hexdigest(), "size": len(item)}
        if isinstance(item, dict):
            return {str(key): normalize(item[key]) for key in sorted(item, key=str)}
        if isinstance(item, (list, tuple)):
            return [normalize(child) for child in item]
        if isinstance(item, (str, int, float, bool)) or item is None:
            return item
        return str(item)

    payload = json.dumps(normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _task_key(kind: str, *inputs) -> str:
    return f"{kind}:{_digest_value(inputs)}"


def _ir_snapshot(project_id: str) -> str:
    return _digest_value(store.load_ir(project_id) or {})


def _assert_ir_unchanged(project_id: str, expected: str) -> None:
    if _ir_snapshot(project_id) != expected:
        raise RuntimeError("任务执行期间设计 IR 已更新，本次旧输入结果未保存；请基于最新版本重新发起。")


def _input_revision(project_id: str) -> int:
    return int((store.load_meta(project_id) or {}).get("input_revision") or 1)


def _assert_input_unchanged(project_id: str, expected: int) -> None:
    if _input_revision(project_id) != expected:
        raise RuntimeError("任务执行期间原图或附件已更新，本次旧输入结果未保存；请重新发起解析。")


def _assert_dependencies_unchanged(expected: str, current, label: str) -> None:
    if _digest_value(current) != expected:
        raise RuntimeError(f"任务执行期间{label}已更新，本次旧输入结果未保存；请基于最新内容重新发起。")


def _confirmation_payload(value) -> dict:
    data = value.model_dump() if isinstance(value, BaseModel) else dict(value or {})
    ignored = {*_CONFIRMATION_FIELDS, "updated_at", "timing"}

    def strip_control_fields(item):
        if isinstance(item, dict):
            return {
                key: strip_control_fields(child)
                for key, child in item.items() if key not in ignored
            }
        if isinstance(item, list):
            return [strip_control_fields(child) for child in item]
        return item

    return strip_control_fields(data)


def _business_changed(current, incoming) -> bool:
    return current is None or _confirmation_payload(current) != _confirmation_payload(incoming)


def _sync_confirmation(current, incoming) -> bool:
    """确认字段只由服务端维护；业务内容变化时自动撤销旧确认。"""
    unchanged = current is not None and _confirmation_payload(current) == _confirmation_payload(incoming)
    for field in _CONFIRMATION_FIELDS:
        setattr(incoming, field, getattr(current, field, None) if unchanged else (False if field == "confirmed" else None))
    return unchanged


def _sync_pricing_approval(current: Optional[PricingPlan], incoming: PricingPlan) -> bool:
    """定价内容变化后，旧的销售提交/财务审批结论自动失效。"""
    def business_payload(value: PricingPlan) -> dict:
        data = value.model_dump()
        for key in ("approval", "updated_at", "timing", "base_price", "factor_multiplier", "suggested_price"):
            data.pop(key, None)
        costs = data.get("costs") or {}
        for key in ("management_cost", "base_cost"):
            costs.pop(key, None)
        return data

    unchanged = current is not None and business_payload(current) == business_payload(incoming)
    incoming.approval = (
        current.approval.model_copy(deep=True) if unchanged
        else type(incoming.approval)()
    )
    return unchanged


# --------------------------------------------------------------------------- #
# 鉴权: app 级依赖在 /api 层校验令牌(放行 health/login 与静态前端);
# 关闭鉴权时注入隐式 system/admin,保持旧行为。
# --------------------------------------------------------------------------- #
_PUBLIC_PATHS = {"/api/health", "/api/login", "/api/register"}
_PROJECT_ID_PATTERN = re.compile(r"^[0-9a-f]{12}$")
_login_lock = threading.Lock()
_login_attempts: dict[str, list[float]] = {}


def _valid_project_path(path: str) -> bool:
    """只允许系统生成的 12 位十六进制项目 ID，阻断本地文件后端路径穿越。"""
    prefix = "/api/projects/"
    if not path.startswith(prefix):
        return True
    first_segment = path[len(prefix):].split("/", 1)[0]
    if not first_segment:  # 保留 FastAPI 对 /api/projects/ 的标准重定向行为
        return True
    # /api/projects/3d 是创建 3D 项目的固定路由，不是项目 ID。
    return first_segment == "3d" or bool(_PROJECT_ID_PATTERN.fullmatch(first_segment))


async def auth_guard(request: Request):
    if AUTH_AUTO_ADMIN:
        # 演示/内网临时模式：不展示登录页，但仍以管理员身份通过所有业务权限检查。
        request.state.user = {
            "username": DEFAULT_ADMIN_USER,
            "role": "admin",
            "display_name": "默认管理员",
            "is_system": False,
        }
        return
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


async def project_write_guard(request: Request):
    """工程师的项目写操作必须属于本人；经理和管理员可跨项目管理。"""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    match = re.match(r"^/api/projects/([0-9a-f]{12})(?:/|$)", request.url.path)
    if not match:
        return
    user = current_user(request)
    # 只在工程师角色做“本人项目”限制；总监等角色仍交由具体业务接口授权。
    if user.get("role") != "engineer":
        return
    project_id = match.group(1)
    meta = store.load_meta(project_id)
    if not meta or meta.get("deleted_at"):
        raise HTTPException(404, "项目不存在")
    if not auth.can_edit_project(user, meta):
        raise HTTPException(403, "工艺工程师只能修改本人创建的项目")


def _require(user: dict, allowed: set, msg: str) -> None:
    if (user or {}).get("role") not in allowed:
        raise HTTPException(403, msg)


def _login_client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_login_rate(request: Request) -> None:
    key, now = _login_client_key(request), time.monotonic()
    with _login_lock:
        recent = [at for at in _login_attempts.get(key, []) if now - at < LOGIN_WINDOW_SECONDS]
        _login_attempts[key] = recent
        if len(recent) >= LOGIN_MAX_ATTEMPTS:
            raise HTTPException(429, "登录尝试过于频繁，请稍后再试")


def _record_login_attempt(request: Request, success: bool) -> None:
    key = _login_client_key(request)
    with _login_lock:
        if success:
            _login_attempts.pop(key, None)
        else:
            _login_attempts.setdefault(key, []).append(time.monotonic())


@asynccontextmanager
async def lifespan(_app):
    """统一管理启动 housekeeping，兼容 FastAPI 新版 lifespan API。"""
    _startup_housekeeping()
    yield


app = FastAPI(
    title="图纸解析与生成平台",
    version="0.1.0",
    lifespan=lifespan,
    dependencies=[Depends(auth_guard), Depends(project_write_guard)],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(CORS_ALLOW_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def project_id_guard(request: Request, call_next):
    if not _valid_project_path(request.url.path):
        return JSONResponse(status_code=404, content={"detail": "项目不存在"})
    return await call_next(request)


@app.middleware("http")
async def response_hardening(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # 认证响应与动态页面不得被浏览器/代理复用；静态资源也使用 no-cache，
    # 避免多页面手写版本号遗漏时发生“代码已更新但页面仍是旧版”。
    path = request.url.path
    if path.startswith("/api/") or path.endswith((".html", ".js", ".css")):
        response.headers.setdefault("Cache-Control", "no-cache, no-store, must-revalidate")
    return response


def _startup_housekeeping():
    if AUTH_ENABLED:
        # Do not silently expose an authenticated deployment with the source-code
        # fallback secret. Existing installations with a custom secret keep their
        # original behaviour; new deployments get a clear, actionable failure.
        if AUTH_SECRET == "dev-insecure-secret-change-me":
            raise RuntimeError("开启 AUTH_ENABLED 时必须在 .env 设置随机 AUTH_SECRET")
        if not store.list_users() and DEFAULT_ADMIN_PASSWORD == "admin123":
            raise RuntimeError("首次开启 AUTH_ENABLED 时必须在 .env 设置非默认 DEFAULT_ADMIN_PASSWORD")
        auth.ensure_default_admin(store)
        auth.ensure_system_user(store)
        store.backfill_legacy_mine_owner(DEFAULT_ADMIN_USER)
    # ThreadPoolExecutor 任务只在当前进程中存在。服务重启后明确结束旧任务，
    # 使轮询端能恢复操作，而不是永久停留在“处理中”。
    if TASK_RECOVER_ON_START:
        tasks.recover_interrupted_tasks()


@app.post("/api/login")
def login(body: LoginBody, request: Request):
    _check_login_rate(request)
    u = store.get_user(body.username)
    if not u or not auth.verify_password(body.password, u.get("password_hash", "")):
        _record_login_attempt(request, False)
        raise HTTPException(401, "用户名或密码错误")
    if u.get("is_system"):
        _record_login_attempt(request, False)
        raise HTTPException(403, "system 为历史项目归档账号，不能登录")
    _record_login_attempt(request, True)
    return {"token": auth.make_token(u["username"], u["role"]), "user": auth.public_user(u)}


def _valid_username(value: str) -> str:
    username = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username):
        raise HTTPException(400, "用户名需为 3–40 位字母、数字、点、下划线或连字符")
    return username


@app.post("/api/register")
def register(body: RegisterBody):
    """企业账号注册：账号先以只读身份启用，角色由管理员在用户管理中授予。"""
    username = _valid_username(body.username)
    if store.get_user(username):
        raise HTTPException(409, "用户名已存在")
    if body.requested_role not in auth.ROLES or body.requested_role == "admin":
        raise HTTPException(400, "申请角色不合法")
    rec = auth.make_user(username, body.password, "viewer", body.display_name.strip())
    rec["requested_role"] = body.requested_role
    store.save_user(username, rec)
    return {"user": auth.public_user(rec), "message": "注册成功，当前为只读权限；请由系统管理员授予业务角色。"}


@app.get("/api/me")
def whoami(user: dict = Depends(current_user)):
    return {"user": user, "auth_enabled": AUTH_ENABLED and not AUTH_AUTO_ADMIN}


@app.put("/api/me")
def update_my_profile(body: UpdateMyProfile, user: dict = Depends(current_user)):
    username = user.get("username", "")
    saved = store.get_user(username)
    if not saved:
        raise HTTPException(404, "用户不存在")
    display_name = body.display_name.strip()
    if display_name:
        saved["display_name"] = display_name
    if body.new_password:
        if not body.current_password or not auth.verify_password(body.current_password, saved.get("password_hash", "")):
            raise HTTPException(400, "当前密码不正确")
        if len(body.new_password) < 8:
            raise HTTPException(400, "新密码至少需要 8 位")
        saved["password_hash"] = auth.hash_password(body.new_password)
    store.save_user(username, saved)
    public = auth.public_user(saved)
    return {"user": public, "token": auth.make_token(public["username"], public["role"])}


@app.get("/api/users")
def list_users_ep(user: dict = Depends(current_user)):
    _require(user, auth.ADMIN_ROLES, "需要管理员权限")
    return {"users": [auth.public_user(u) for u in store.list_users()]}


@app.post("/api/users")
def create_user_ep(body: NewUser, user: dict = Depends(current_user)):
    _require(user, auth.ADMIN_ROLES, "需要管理员权限")
    username = _valid_username(body.username)
    if body.role not in auth.ROLES:
        raise HTTPException(400, f"非法角色,可选: {', '.join(auth.ROLES)}")
    if store.get_user(username):
        raise HTTPException(409, "用户名已存在")
    rec = auth.make_user(username, body.password, body.role, body.display_name)
    store.save_user(rec["username"], rec)
    return auth.public_user(rec)


@app.put("/api/users/{username}/role")
def update_user_role_ep(username: str, body: UpdateUserRole, user: dict = Depends(current_user)):
    _require(user, auth.ADMIN_ROLES, "需要管理员权限")
    if body.role not in auth.ROLES or body.role == "admin":
        raise HTTPException(400, "仅可授予业务角色；管理员角色不可通过此接口授予")
    target = store.get_user(username)
    if not target:
        raise HTTPException(404, "用户不存在")
    if target.get("is_system"):
        raise HTTPException(400, "system 为历史项目归档账号，不能修改角色")
    target["role"] = body.role
    target["requested_role"] = body.role
    store.save_user(username, target)
    return {"user": auth.public_user(target)}


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health():
    result = {
        "status": "ok",
        "model": active_model(),
        "text_model": active_text_model(),
        "llm_provider": LLM_PROVIDER,
        "cadquery_available": geometry.CADQUERY_AVAILABLE,
        "auth_enabled": AUTH_ENABLED and not AUTH_AUTO_ADMIN,
    }
    if LLM_PROVIDER == "qwen":
        runtime = qwen_client.runtime_settings()
        result["model"] = runtime["vision_models"][0] if runtime["vision_models"] else QWEN_MODEL
        result["text_model"] = runtime["text_models"][0] if runtime["text_models"] else QWEN_TEXT_MODEL
        result["qwen_model_pools"] = qwen_client.model_pool_status()
    return result


@app.get("/api/llm/settings")
def get_runtime_llm_settings(user: dict = Depends(current_user)):
    """模型设置页读取接口：所有已登录用户可见状态，密钥不会被返回。"""
    provider_catalogs = {
        "anthropic": {
            "label": "Anthropic",
            "text": ["claude-opus-4-8", "claude-sonnet-4-5"],
            "vision": ["claude-opus-4-8", "claude-sonnet-4-5"],
            "web": ["claude-opus-4-8", "claude-sonnet-4-5"],
        },
        "openai": {
            "label": "OpenAI",
            "text": ["gpt-5.6", "gpt-5", "gpt-4.1"],
            "vision": ["gpt-5.6", "gpt-5", "gpt-4.1"],
            "web": ["gpt-5.6", "gpt-5", "gpt-4.1"],
        },
        "qwen": {
            "label": "Qwen / 阿里云百炼",
            "text": ["qwen-plus", "qwen3.7-plus", "qwen-max"],
            "vision": ["qwen3-vl-plus", "qwen3-vl-flash", "qwen-vl-plus"],
            "web": ["qwen-plus", "qwen3.7-plus"],
        },
        "deepseek": {
            "label": "DeepSeek",
            "text": ["deepseek-chat", "deepseek-reasoner"],
            "vision": [],
            "web": [],
        },
    }
    if LLM_PROVIDER != "qwen":
        provider_catalogs["team"] = {
            "label": "Team（当前团队配置）",
            "text": [active_text_model()],
            "vision": [active_model()],
            "web": [],
        }
        return {
            "provider": LLM_PROVIDER, "runtime_provider": LLM_PROVIDER, "editable": False, "model": active_model(),
            "text_model": active_text_model(), "reason": "当前部署的模型提供商由服务器环境变量固定",
            "provider_options": [{"value": key, "label": value["label"]} for key, value in provider_catalogs.items()]
            + [{"value": "team", "label": "Team（当前团队配置）"}],
            "provider_catalogs": provider_catalogs,
            "web_search_available": False,
            "web_search_scope": "当前部署提供商未开放本项目的联网搜索能力",
        }
    result = qwen_client.runtime_settings()
    provider_catalogs["team"] = {
        "label": "Team（当前团队配置）",
        "text": result["text_models"],
        "vision": result["vision_models"],
        "web": result["web_search_models"],
    }
    result.update({"provider": "team", "runtime_provider": "qwen", "editable": user.get("role") == "admin", "model": result["vision_models"][0] if result["vision_models"] else QWEN_MODEL,
                   "provider_options": [{"value": key, "label": value["label"]} for key, value in provider_catalogs.items()],
                   "provider_catalogs": provider_catalogs,
                   "web_search_available": bool(result["web_search_models"] and result["api_key_configured"]),
                   "web_search_scope": "仅支持型号联网核验；普通工艺/成本分析不会调用联网工具"})
    return result


@app.put("/api/llm/settings")
def update_runtime_llm_settings(body: RuntimeLlmSettingsBody, user: dict = Depends(current_user)):
    """仅管理员可修改全局 Qwen 模型/API 配置，并保存到 data 供重启后恢复。"""
    _require(user, auth.ADMIN_ROLES, "需要系统管理员权限才能修改全局模型与 API 配置")
    if body.provider and body.provider not in {"team", "qwen"}:
        raise HTTPException(409, "当前服务只允许保存 Team（当前团队配置）的模型；切换到其他供应商需修改服务器部署配置后重启")
    if LLM_PROVIDER != "qwen":
        raise HTTPException(409, "当前部署不是 Qwen；切换提供商需修改服务器 .env 的 LLM_PROVIDER 后重启容器")
    try:
        result = qwen_client.configure_runtime_settings(
            api_key=body.api_key or None,
            base_url=body.base_url or None,
            vision_models=body.vision_models or None,
            text_models=body.text_models or None,
            web_search_models=body.web_search_models or None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    result.update({"editable": True, "message": "模型与 API 设置已保存并立即生效"})
    return result


@app.get("/api/projects")
def projects():
    return store.list_projects()


@app.patch("/api/projects/{project_id}/management")
def rename_project(project_id: str, body: ProjectManageBody, user: dict = Depends(current_user)):
    """首页项目编辑：仅改业务名称，保留图纸、工艺结果和完整审计链。"""
    _require(user, auth.WRITE_ROLES, "需要工艺工程师、技术经理或管理员权限")
    meta = _workflow_project(project_id)
    if not auth.can_edit_project(user, meta):
        raise HTTPException(403, "仅项目创建人、工艺技术经理或管理员可以编辑项目")
    name = body.name.strip()
    updated = store.rename_project(project_id, name, author=user.get("username", "system"))
    requirement = store.load_requirement(project_id)
    if requirement and requirement.get("status") in ("draft", "rejected"):
        requirement["title"] = name
        requirement["updated_at"] = _now_str()
        store.save_requirement(project_id, requirement, author=user.get("username", "system"))
    return {"project": updated, "requirement": requirement}


@app.delete("/api/projects/{project_id}/management")
def delete_project(project_id: str, user: dict = Depends(current_user)):
    """首页删除：采用软删除，避免破坏已发布报告和审计留痕。"""
    _require(user, auth.WRITE_ROLES, "需要工艺工程师、技术经理或管理员权限")
    meta = _workflow_project(project_id)
    if not auth.can_edit_project(user, meta):
        raise HTTPException(403, "仅项目创建人、工艺技术经理或管理员可以删除项目")
    store.archive_project(project_id, author=user.get("username", "system"))
    return {"ok": True}


async def _read_upload_limited(file: UploadFile, *, label: str = "文件") -> bytes:
    """分块读取 multipart 文件，并在超过服务端限额时立即终止。"""
    declared_size = getattr(file, "size", None)
    if isinstance(declared_size, int) and declared_size > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"{label}超过大小上限（{MAX_UPLOAD_BYTES // 1024 // 1024} MiB）")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"{label}超过大小上限（{MAX_UPLOAD_BYTES // 1024 // 1024} MiB）")
        chunks.append(chunk)
    return b"".join(chunks)


def _check_workflow_input_change(project_id: str, user: dict) -> dict:
    """输入变更门禁：审核中的单据不可变更，已审批需求仅经理可发起修订。"""
    requirement = store.load_requirement(project_id) or {}
    status = requirement.get("status")
    if status in {"pending_confirmation", "pending_review"}:
        raise HTTPException(409, "需求正在确认或审核中，请先退回草稿后再变更输入资料")
    report = store.load_process_report(project_id) or {}
    if report.get("status") in {"in_review", "approved", "published"}:
        raise HTTPException(409, "评估报告已送审或发布；已发布报告请先创建新版本，再变更输入资料")
    if status == "approved" and user.get("role") not in auth.MANAGER_ROLES:
        raise HTTPException(403, "已审批需求的输入修订须由工艺技术经理或管理员发起")
    return requirement


def _reset_approved_requirement_after_input_change(
    project_id: str, requirement: dict, user: dict, reason: str,
) -> None:
    """审批后的工程输入发生变更时，旧审批结论自动失效并形成修订留痕。"""
    if requirement.get("status") != "approved":
        return
    doc = RequirementDoc(**requirement)
    doc.status = "draft"
    doc.confirmed_by = None
    doc.confirmed_at = None
    doc.confirmation_note = ""
    doc.reviewed_by = None
    doc.reviewed_at = None
    doc.review_note = ""
    doc.ai_check = {}
    doc.history.append(_workflow_event("approved_requirement_reopened", user, reason))
    doc.updated_at = _now_str()
    store.save_requirement(project_id, doc.model_dump(), author=user.get("username", "system"))
    store.audit(project_id, "workflow:requirement_reopened_for_input_change", {
        "by": user.get("username", "system"), "reason": reason,
    })


@app.post("/api/projects")
async def upload_project(
    file: Optional[UploadFile] = File(None),
    files: List[UploadFile] = File(default=[]),
    note: str = Form(""),
    attachments: List[UploadFile] = File(default=[]),
    user: dict = Depends(current_user),
):
    """上传一个或多个设备需求图纸，首份为原图，其余保留为可追溯图纸附件。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    drawing_files = [item for item in ([file] if file else []) + list(files or []) if item and item.filename]
    if not drawing_files:
        raise HTTPException(400, "请至少上传一份模型图纸")
    primary = drawing_files[0]
    content = await _read_upload_limited(primary, label="需求图纸")
    if not content:
        raise HTTPException(400, "空文件")
    project_id = store.create_project(
        primary.filename or "source.png", content, note=note,
        owner=user.get("username", "system"),
        owner_display_name=user.get("display_name") or user.get("username", "system"),
    )
    extra_drawings = []
    author = user.get("username", "system")
    for drawing in drawing_files[1:]:
        data = await _read_upload_limited(drawing, label="补充模型图纸")
        if data:
            name = drawing.filename or "drawing"
            store.add_attachment(project_id, name, data, author)
            extra_drawings.append(name)
    for att in attachments or []:
        data = await _read_upload_limited(att, label="补充文件")
        if data:
            store.add_attachment(project_id, att.filename or "attachment", data, author)
    if extra_drawings:
        store.audit(project_id, "upload_additional_drawings", {
            "by": user.get("username", "system"), "files": extra_drawings,
        })
    return {"project_id": project_id, "source_filename": primary.filename, "additional_drawings": extra_drawings}


@app.post("/api/projects/{project_id}/attachments")
async def upload_project_attachments(
    project_id: str, files: List[UploadFile] = File(...), user: dict = Depends(current_user),
):
    """为已创建的需求追加图纸、模型、BOM 或技术资料，并写入审计。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    requirement = _check_workflow_input_change(project_id, user)
    prepared = []
    for item in files or []:
        data = await _read_upload_limited(item, label="补充文件")
        if data:
            prepared.append((item.filename or "attachment", data))
    if not prepared:
        raise HTTPException(400, "未收到有效附件")
    saved = []
    author = user.get("username", "system")
    for name, data in prepared:
        store.add_attachment(project_id, name, data, author)
        saved.append(name)
    _reset_approved_requirement_after_input_change(project_id, requirement, user, "补充输入附件")
    store.audit(project_id, "upload_workflow_attachments", {"by": user.get("username", "system"), "files": saved})
    return {"attachments": (store.load_meta(project_id) or {}).get("attachments", [])}


@app.post("/api/projects/{project_id}/source")
async def replace_project_source(
    project_id: str, file: UploadFile = File(...), user: dict = Depends(current_user),
):
    """替换需求的原始 2D 图纸；后续解析始终使用新图纸。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    requirement = _check_workflow_input_change(project_id, user)
    content = await _read_upload_limited(file, label="需求图纸")
    if not content:
        raise HTTPException(400, "空文件")
    store.replace_source(project_id, file.filename or "source.png", content, user.get("username", "system"))
    _reset_approved_requirement_after_input_change(project_id, requirement, user, "替换原始图纸")
    return {"source_filename": (store.load_meta(project_id) or {}).get("source_filename")}


@app.get("/api/projects/{project_id}/attachments")
def list_project_attachments(project_id: str):
    meta = store.load_meta(project_id)
    if not meta:
        raise HTTPException(404, "项目不存在")
    return {"attachments": meta.get("attachments", [])}


@app.get("/api/projects/{project_id}/attachments/{filename}")
def get_project_attachment(project_id: str, filename: str):
    path = store.attachment_file(project_id, filename)
    if not path or not path.exists():
        raise HTTPException(404, "附件不存在")
    return FileResponse(str(path), filename=path.name)


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
    content = await _read_upload_limited(file, label="3D 模型")
    if not content:
        raise HTTPException(400, "空文件")
    fname = file.filename or "model.step"
    project_id = store.create_project(
        fname, content, note=note,
        owner=user.get("username", "system"),
        owner_display_name=user.get("display_name") or user.get("username", "system"),
    )
    expected_input_revision = _input_revision(project_id)

    def job():
        ir, solid_map = step_import.import_step(content, fname)
        _assert_input_unchanged(project_id, expected_input_revision)
        ir_payload = ir.model_dump()
        ir_hash = _digest_value(ir_payload)
        out_dir = store.geometry_dir(project_id)
        name_by_id = {p.part_id: p.name for p in ir.parts}
        g_results = [
            geometry.result_from_solid(pid, name_by_id.get(pid, pid), solid, out_dir)
            for pid, solid in solid_map
        ]
        geometry_payload = _geometry_payload(project_id, g_results)
        geometry_payload["source_ir_hash"] = ir_hash
        d_results = [
            drawing2d.generate_from_solid(pid, name_by_id.get(pid, pid), solid, out_dir)
            for pid, solid in solid_map
        ]
        _assert_input_unchanged(project_id, expected_input_revision)
        store.save_ir(project_id, ir_payload, stage="parsed_3d",
                      author=user.get("username", "system"))
        store.save_geometry_result(project_id, geometry_payload)
        drawings_payload = _drawings_payload(project_id, d_results)
        drawings_payload["source_ir_hash"] = ir_hash
        store.save_drawings_result(project_id, drawings_payload)
        store.sync_geometry(project_id)  # 同步到对象存储(Local 后端空操作)
        store.audit(project_id, "import_step", {"parts": len(solid_map)})
        return {"parts": len(solid_map)}

    task_id = tasks.submit(
        project_id, "import_3d", job, cad=True,
        dedup_key=_task_key("import_3d", expected_input_revision, content),
    )
    return {"project_id": project_id, "task_id": task_id}


@app.post("/api/projects/{project_id}/parse")
def parse(project_id: str, user: dict = Depends(current_user)):
    """调用当前配置的视觉模型解析原图(结合补充说明/佐证文件) -> IR(异步任务)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    src = store.source_path(project_id)
    if not src or not src.exists():
        raise HTTPException(404, "项目或原图不存在")
    data, name = src.read_bytes(), src.name
    note, atts = store.get_note(project_id), store.load_attachments(project_id)
    author = user.get("username", "system")
    expected_input_revision = _input_revision(project_id)

    def job():
        manifest = vision.build_input_manifest(name, data, atts)
        store.audit(project_id, "drawing_parse_stage:manifest", manifest)
        tasks.report_progress("正在读取图纸、提取文本并调用视觉模型")
        ir = vision.parse_drawing(data, name, note=note, attachments=atts)
        tasks.report_progress("视觉模型已返回，正在校验尺寸、证据和 IR")
        _assert_input_unchanged(project_id, expected_input_revision)
        store.save_drawing_analysis(project_id, vision.pipeline_report(ir, manifest))
        store.save_ir(project_id, ir.model_dump(), stage="parsed", author=author)
        store.audit(project_id, "parse_input_context", {
            "source_file": name,
            "note_included": bool(note.strip()),
            "attachments_included": [attachment_name for attachment_name, _ in atts],
            "sop_version": ir.sop_version,
            "ai_status": ir.ai_status.value,
            "evidence_count": len(ir.evidence_ledger),
        })
        return ir.model_dump()

    return {"task_id": tasks.submit(
        project_id, "parse", job,
        dedup_key=_task_key("parse", expected_input_revision, note, atts),
    )}


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
    expected_ir = _digest_value(ir_dict)
    expected_input_revision = _input_revision(project_id)

    def job():
        tasks.report_progress("正在调用视觉模型复核图纸字段")
        original = DesignIR(**ir_dict)
        try:
            patch = vision.verify_drawing(original, data, name, note=note, attachments=atts)
        except RuntimeError as exc:
            # 若模型多轮修复后仍违反 CAD IR 契约，绝不可让它覆盖原始解析；
            # 原始版本保留，用户可查看失败详情后再次发起校验。
            message = str(exc)
            if not any(marker in message for marker in (
                "已返回结果，但字段未通过本地数据校验",
                "未通过本地字段校验",
                "未通过本地数据校验",
            )):
                raise
            return {
                "ir": original.model_dump(),
                "verification": {
                    "status": "rejected",
                    "message": (
                        "自校验模型在自动修复重试后仍未通过 CAD 几何契约，原始解析结果已完整保留；"
                        "请查看校验详情并在确认后再次发起校验。"
                    ),
                    "detail": message,
                },
            }
        verified, applied, pending = vision.apply_verification_patch(original, patch, auto_only=True)
        _assert_input_unchanged(project_id, expected_input_revision)
        _assert_ir_unchanged(project_id, expected_ir)
        if applied:
            store.save_ir(
                project_id, verified.model_dump(), stage="verified", author=author,
                note=f"字段级 AI 校核自动应用 {len(applied)} 项强证据修改",
            )
        verification_report = {
            "by": author,
            "summary": patch.summary, "sop_version": "drawing-verify-1.0",
            "applied_changes": applied, "pending_changes": pending, "decisions": {},
        }
        store.save_verification_report(project_id, verification_report, author=author)
        store.audit(project_id, "verify_patch", verification_report)
        return {
            "ir": verified.model_dump(),
            "verification": {
                "status": "applied" if applied else ("pending" if pending else "no_change"),
                "message": f"校核完成：自动应用 {len(applied)} 项强证据修改，{len(pending)} 项等待人工确认。",
                "applied_changes": applied, "pending_changes": pending,
                "summary": patch.summary,
            },
        }

    return {"task_id": tasks.submit(
        project_id, "verify", job,
        dedup_key=_task_key("verify", expected_ir, expected_input_revision, note, atts),
    )}


@app.get("/api/projects/{project_id}/verification")
def get_verification(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return store.load_verification_report(project_id) or {
        "applied_changes": [], "pending_changes": [], "decisions": {},
    }


@app.post("/api/projects/{project_id}/verification/decide")
def decide_verification_patch(
    project_id: str, body: VerificationPatchDecision, user: dict = Depends(current_user),
):
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    report = store.load_verification_report(project_id)
    if not report:
        raise HTTPException(404, "尚无待确认的 AI 校核结果")
    pending = next(
        (item for item in report.get("pending_changes", []) if item.get("field") == body.field), None
    )
    if not pending:
        raise HTTPException(404, "该字段没有待确认的校核修改")
    actor = user.get("username", "system")
    report.setdefault("decisions", {})[body.field] = {
        "decision": body.decision, "note": body.note.strip(), "by": actor, "at": _now_str(),
    }
    if body.decision == "confirmed":
        ir_dict = store.load_ir(project_id)
        if not ir_dict:
            raise HTTPException(404, "当前 IR 不存在")
        patch = VerificationPatch.model_validate({"changes": [pending]})
        updated, applied, still_pending = vision.apply_verification_patch(
            DesignIR(**ir_dict), patch, auto_only=False,
        )
        if not applied:
            detail = (still_pending[0].get("rejected_reason") if still_pending else "补丁不可应用")
            raise HTTPException(409, detail)
        updated = vision.confirm_evidence_field(updated, body.field, actor)
        store.save_ir(project_id, updated.model_dump(), stage="verified", author=actor,
                      note=f"人工确认 AI 校核字段 {body.field}")
        report.setdefault("applied_changes", []).extend(applied)
    report["pending_changes"] = [
        item for item in report.get("pending_changes", []) if item.get("field") != body.field
    ]
    store.save_verification_report(project_id, report, author=actor)
    return report


@app.post("/api/projects/{project_id}/model-lookup")
def model_lookup_search(project_id: str, user: dict = Depends(current_user)):
    """联网核验型号候选；结论保存为待确认，人工确认前不写入 IR/BOM。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if LLM_PROVIDER != "qwen":
        raise HTTPException(409, "型号联网核验当前仅在 LLM_PROVIDER=qwen 时可用")
    if not _workflow_project(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(409, "请先完成图纸解析，再进行型号联网核验")
    attachments = store.load_attachments(project_id)
    author = user.get("username", "system")
    expected_ir = _digest_value(ir_dict)

    def job():
        tasks.report_progress("正在调用模型识别候选型号")
        result = model_lookup.identify_models(DesignIR(**ir_dict), attachments)
        tasks.report_progress("型号候选已返回，正在去重并保存待确认结果")
        _assert_ir_unchanged(project_id, expected_ir)
        payload = result.model_dump()
        payload["confirmations"] = {}
        payload["applied_changes"] = []
        payload["requires_confirmation"] = True
        payload["pending_since"] = _now_str()
        payload["source_ir_hash"] = expected_ir
        store.save_model_lookup(project_id, payload, author=author)
        return payload

    return {"task_id": tasks.submit(
        project_id, "model_lookup", job,
        dedup_key=_task_key("model_lookup", expected_ir, attachments),
    )}


def _apply_model_lookup_result(project_id: str, report: dict, author: str,
                               candidates: Optional[set[str]] = None) -> dict:
    """只把已人工确认的可靠结论写入新 IR 版本；可安全重复调用。"""
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        return report
    confirmed = {
        key.upper() for key, value in (report.get("confirmations") or {}).items()
        if value.get("decision") == "confirmed"
    }
    if candidates:
        confirmed &= {item.upper() for item in candidates}
    filtered = dict(report)
    filtered["identifications"] = [
        item for item in report.get("identifications", [])
        if str(item.get("candidate_model") or "").strip().upper() in confirmed
    ]
    # 产品级推演没有独立人工确认键，不能随型号确认一起自动进入 BOM。
    filtered["proposed_components"] = []
    updated_ir, changes = model_lookup.apply_lookup_results(DesignIR(**ir_dict), filtered)
    existing_changes = report.setdefault("applied_changes", [])
    known = {(item.get("target"), item.get("candidate_model")) for item in existing_changes}
    new_changes = [item for item in changes if (item.get("target"), item.get("candidate_model")) not in known]
    existing_changes.extend(new_changes)
    report["confirmed_sync_attempted_at"] = _now_str()
    if new_changes:
        note = f"人工确认后同步联网型号 {len(new_changes)} 项"
        store.save_ir(project_id, updated_ir.model_dump(), stage="model_lookup_applied", author=author, note=note)
        store.audit(project_id, "apply_model_lookup", {"by": author, "changes": new_changes})
    store.save_model_lookup(project_id, report, author=author)
    return report


@app.get("/api/projects/{project_id}/model-lookup")
def get_model_lookup(project_id: str):
    if not _workflow_project(project_id):
        raise HTTPException(404, "项目不存在")
    return store.load_model_lookup(project_id) or {"identifications": [], "confirmations": {}}


@app.post("/api/projects/{project_id}/model-lookup/apply")
def apply_existing_model_lookup(project_id: str, user: dict = Depends(current_user)):
    """仅同步已人工确认的旧核验结果，不调用模型或联网搜索。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not _workflow_project(project_id):
        raise HTTPException(404, "项目不存在")
    report = store.load_model_lookup(project_id)
    if not report:
        raise HTTPException(404, "尚无型号联网核验结果")
    return _apply_model_lookup_result(project_id, report, user.get("username", "system"))


@app.post("/api/projects/{project_id}/model-lookup/confirm")
def confirm_model_lookup(
    project_id: str, body: ModelLookupConfirmation, user: dict = Depends(current_user),
):
    """记录人工复核；确认后才把该型号同步到 IR/BOM。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    report = store.load_model_lookup(project_id)
    if not report:
        raise HTTPException(404, "尚无型号联网核验结果")
    source_ir_hash = str(report.get("source_ir_hash") or "")
    if source_ir_hash and source_ir_hash != _ir_snapshot(project_id):
        raise HTTPException(409, "IR 已在型号核验后发生变化，请重新执行型号核验再确认")
    available = {
        str(item.get("candidate_model") or "").strip().upper()
        for item in report.get("identifications", [])
    }
    candidate = body.candidate_model.strip()
    if candidate.upper() not in available:
        raise HTTPException(404, "型号不在当前核验结果中")
    report.setdefault("confirmations", {})[candidate] = {
        "decision": body.decision,
        "note": body.note.strip(),
        "by": user.get("username", "system"),
        "at": _now_str(),
    }
    if body.decision == "confirmed":
        report = _apply_model_lookup_result(
            project_id, report, user.get("username", "system"), {candidate}
        )
    store.save_model_lookup(project_id, report, author=user.get("username", "system"))
    store.audit(project_id, "confirm_model_lookup", {
        "candidate_model": candidate, "decision": body.decision, "by": user.get("username", "system"),
    })
    return report


@app.post("/api/projects/{project_id}/decompose")
def decompose_recommend(project_id: str, user: dict = Depends(current_user)):
    """对已解析 IR 做拆解推荐增强(异步任务)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(404, "请先解析(parse)得到 IR")
    author = user.get("username", "system")
    expected_ir = _digest_value(ir_dict)

    def job():
        tasks.report_progress("正在调用模型生成零件拆解建议")
        enriched = decompose.enrich_with_recommendations(DesignIR(**ir_dict))
        tasks.report_progress("拆解建议已返回，正在校验零件和几何特征")
        _assert_ir_unchanged(project_id, expected_ir)
        store.save_ir(project_id, enriched.model_dump(), stage="decomposed", author=author)
        return enriched.model_dump()

    return {"task_id": tasks.submit(
        project_id, "decompose", job, dedup_key=_task_key("decompose", expected_ir)
    )}


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
    expected_ir = _digest_value(ir_dict)

    def job():
        ir = DesignIR(**ir_dict)
        issues = geometry.preflight_parts(ir.parts)
        if issues:
            raise RuntimeError(
                "CAD 几何预检未通过（未调用模型，也不会产生 API 费用）：\n- "
                + "\n- ".join(issues)
            )
        results = geometry.generate_all(ir.parts, store.geometry_dir(project_id))
        _assert_ir_unchanged(project_id, expected_ir)
        payload = _geometry_payload(project_id, results)
        payload["source_ir_hash"] = expected_ir
        store.save_geometry_result(project_id, payload)
        store.sync_geometry(project_id)  # 同步到对象存储(Local 后端空操作)
        return payload

    return {"task_id": tasks.submit(
        project_id, "generate", job, cad=True, dedup_key=_task_key("generate", expected_ir)
    )}


@app.post("/api/projects/{project_id}/drawings")
def drawings(project_id: str, user: dict = Depends(current_user)):
    """据 IR 用 CAD 内核生成各零件 2D 工程图(三视图 SVG + 下料 DXF,异步任务)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(404, "请先解析(parse)得到 IR")
    if not drawing2d.AVAILABLE:
        raise HTTPException(503, "CadQuery 未安装，2D 工程图生成不可用。")
    expected_ir = _digest_value(ir_dict)

    def job():
        ir = DesignIR(**ir_dict)
        issues = geometry.preflight_parts(ir.parts)
        if issues:
            raise RuntimeError(
                "2D 工程图几何预检未通过（未调用模型，也不会产生 API 费用）：\n- "
                + "\n- ".join(dict.fromkeys(issues))
            )
        results = drawing2d.generate_all(ir.parts, store.geometry_dir(project_id))
        _assert_ir_unchanged(project_id, expected_ir)
        payload = _drawings_payload(project_id, results)
        payload["source_ir_hash"] = expected_ir
        store.save_drawings_result(project_id, payload)
        store.sync_geometry(project_id)  # 同步到对象存储(Local 后端空操作)
        return payload

    return {"task_id": tasks.submit(
        project_id, "drawings", job, cad=True, dedup_key=_task_key("drawings", expected_ir)
    )}


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
    actor = user.get("username", "system")
    ir = vision.mark_human_confirmed(ir, actor)
    store.save_ir(project_id, ir.model_dump(), stage="edited", author=actor,
                  note="人工保存并确认当前关键工程字段")
    return ir.model_dump()


_WORKBENCH_CHAT_SYSTEM = """你是企业 CAD 图纸解析工作台中的工艺助手。
仅依据项目提供的结构化解析结果、当前零件和用户问题回答，不得声称重新查看了原始图纸、联网检索或访问外部资料。
回答使用简洁的中文，优先给出可执行的工艺判断、风险、需要人工确认的尺寸或材料信息；不确定时明确说明不确定。
不要编造标准号、供应商、价格或未识别的尺寸。

当且仅当用户明确要求“修改/改为/设为/调整”当前已选零件，并且给出了具体目标值时，才输出 edit：
- edit.should_apply=true；只可修改 name、quantity、material_spec，以及当前零件已有 feature 的数值字段；
- feature_updates 每项使用 feature_index、field、value；不得增加/删除特征、不得修改 type、不得修改其它零件；
- 用户只是咨询“怎么改”、没有给出明确目标值、没有选零件、或信息不足时，edit 必须为 null 或 should_apply=false，并在 answer 中说明需要什么信息；
- 不能根据常识擅自补全尺寸。
只输出合法 JSON：{\"answer\":\"...\",\"edit\":null 或 {\"should_apply\":true,...}}。"""

_PROJECT_CHAT_SYSTEM = """你是企业 AI 工艺平台的项目助手，服务于同一个项目在需求、图纸解析、技术工艺和报告页面中的连续对话。
仅依据项目已保存的需求、解析结果、已生成计划和当前页面上下文回答；没有资料时明确说明缺失，不得声称已读取原图、联网检索或访问外部资料。
回答使用简洁中文，优先说明当前可执行动作、风险、需要确认的信息和对应流程步骤。
这是跨页面的通用对话：不得直接修改需求表、零件参数、BOM、工艺计划或报告。若用户要修改图纸零件参数，应提示其在 2.1 图纸解析页选中零件后提出明确数值修改。
只输出合法 JSON：{\"answer\":\"...\"}。"""


def _project_chat_messages(project_id: str) -> list[dict]:
    """把持久化消息规整为模型可用的最近上下文，过滤旧/异常字段。"""
    rows = store.load_project_chat(project_id).get("messages", [])
    return [
        {"role": item.get("role") if item.get("role") in {"user", "assistant"} else "user", "content": str(item.get("content", ""))[:1600]}
        for item in rows[-12:]
        if isinstance(item, dict) and str(item.get("content", "")).strip()
    ]


def _save_project_chat_turn(project_id: str, user_message: str, answer: str, user: dict, page_context: str = "") -> None:
    """只追加本次问答，保留同一项目在每个页面之间连续的会话。"""
    messages = store.load_project_chat(project_id).get("messages", [])
    timestamp = now_cst_str()
    messages.extend([
        {"role": "user", "content": user_message.strip(), "at": timestamp, "by": user.get("username", "system"), "page": page_context[:160]},
        {"role": "assistant", "content": answer.strip(), "at": timestamp, "page": page_context[:160]},
    ])
    store.save_project_chat(project_id, messages, author=user.get("username", "system"))

_CHAT_FEATURE_FIELDS = {
    "plate": {"length", "width", "thickness"},
    "box": {"length", "width", "height"},
    "cylinder": {"diameter", "height"},
    "hole": {"diameter", "x", "y"},
    "hole_pattern": {"diameter", "count_x", "count_y", "spacing_x", "spacing_y"},
    "fillet": {"radius"},
    "chamfer": {"distance"},
}


def _apply_workbench_chat_edit(part, edit: WorkbenchPartEdit) -> tuple[list[dict], bool]:
    """对模型建议执行白名单修改，返回变更与是否需要重生几何。"""
    changes: list[dict] = []
    geometry_changed = False
    if edit.name is not None and edit.name.strip() and edit.name.strip() != part.name:
        before = part.name
        part.name = edit.name.strip()
        changes.append({"field": "name", "old": before, "new": part.name})
    if edit.quantity is not None and edit.quantity != part.quantity:
        before = part.quantity
        part.quantity = edit.quantity
        changes.append({"field": "quantity", "old": before, "new": part.quantity})
    if edit.material_spec is not None and edit.material_spec.strip():
        spec = edit.material_spec.strip()
        before = part.material.spec if part.material else ""
        if spec != before:
            if part.material:
                part.material.spec = spec
            else:
                part.material = Material(spec=spec)
            changes.append({"field": "material.spec", "old": before, "new": spec})
    for update in edit.feature_updates:
        if update.feature_index >= len(part.features):
            raise HTTPException(422, f"特征序号 {update.feature_index + 1} 不存在")
        feature = part.features[update.feature_index]
        feature_type = feature.type.value if hasattr(feature.type, "value") else str(feature.type)
        if update.field not in _CHAT_FEATURE_FIELDS.get(feature_type, set()):
            raise HTTPException(422, f"特征 #{update.feature_index + 1}（{feature_type}）不允许修改字段 {update.field}")
        value = int(update.value) if update.field.startswith("count_") else float(update.value)
        if value <= 0 and update.field not in {"x", "y"}:
            raise HTTPException(422, f"{update.field} 必须大于 0")
        before = getattr(feature, update.field)
        if before != value:
            setattr(feature, update.field, value)
            changes.append({"field": f"features[{update.feature_index}].{update.field}", "old": before, "new": value})
            geometry_changed = True
    return changes, geometry_changed


@app.post("/api/projects/{project_id}/workbench-chat")
def workbench_chat(
    project_id: str, body: WorkbenchChatRequest, user: dict = Depends(current_user),
):
    """基于已解析 IR 的 2.1 文字问答，不重传图纸，故始终优先走文本模型池。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    meta = store.load_meta(project_id)
    if not meta:
        raise HTTPException(404, "项目不存在")
    saved_ir = store.load_ir(project_id)
    if not saved_ir:
        raise HTTPException(409, "请先完成图纸解析，再向 AI 提问")
    ir = DesignIR(**saved_ir)
    selected = next((part for part in ir.parts if part.part_id == body.part_id), None)
    if body.part_id and not selected:
        raise HTTPException(404, f"零件 {body.part_id} 不存在")

    # 只传可追溯的结构化摘要；避免把原始图片再次发给模型，也控制每次对话的 token。
    context = {
        "project": {
            "project_id": project_id,
            "device_name": ir.device_name,
            "design_intent": ir.design_intent,
            "overall_dims": ir.overall_dims,
            "assembly_notes": ir.assembly_notes,
        },
        "current_part": selected.model_dump() if selected else None,
        "parts": [
            {
                "part_id": part.part_id,
                "name": part.name,
                "material": part.material.spec if part.material else "",
                "quantity": part.quantity,
                "features": [feature.type for feature in part.features],
                "confidence": part.confidence,
            }
            for part in ir.parts[:80]
        ],
        "open_questions": [question.model_dump() for question in ir.open_questions[:20]],
    }
    request_history = [
        {"role": turn.role if turn.role in {"user", "assistant"} else "user", "content": turn.content}
        for turn in body.history
        if turn.content.strip()
    ]
    # 新悬浮对话框不再依赖页面内存，会从项目级留痕恢复上下文；兼容旧工作台传来的 history。
    history = request_history or _project_chat_messages(project_id)
    prompt = json.dumps(
        {"project_context": context, "recent_conversation": history, "user_question": body.message},
        ensure_ascii=False,
        default=str,
    )
    if len(prompt) > 32000:
        prompt = prompt[:32000] + "\n【上下文按预算截断】"
    try:
        tasks.report_progress("正在调用项目上下文问答模型")
        result = llm_client.complete_to_model(
            _WORKBENCH_CHAT_SYSTEM, prompt, WorkbenchChatAnswer, max_tokens=1200,
        )
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    answer = result.answer.strip()
    if not answer:
        raise HTTPException(502, "AI 未返回有效对话内容")
    model = qwen_client.last_used_model() if LLM_PROVIDER == "qwen" else active_text_model()
    edit_applied = None
    # 已导入的精确 STEP/STP 实体不允许通过文本修改 IR 特征，以免与真实实体脱节。
    source_name = str(meta.get("source_filename") or "").lower()
    is_imported_3d = source_name.endswith((".step", ".stp"))
    if result.edit and result.edit.should_apply:
        if not selected:
            answer += "\n\n未选择零件，未应用参数修改。"
        elif is_imported_3d:
            answer += "\n\n当前为导入的精确 3D 模型，未自动改写其参数；请在原 CAD 中修改后重新导入。"
        else:
            changes, geometry_changed = _apply_workbench_chat_edit(selected, result.edit)
            if changes:
                note = f"AI 对话修改 {selected.part_id}：" + "、".join(change["field"] for change in changes)
                store.save_ir(project_id, ir.model_dump(), stage="ai_chat_edited", author=user.get("username", "system"), note=note)
                edit_applied = {
                    "part_id": selected.part_id,
                    "changes": changes,
                    "requires_regeneration": geometry_changed,
                    "explanation": result.edit.explanation,
                }
                store.audit(project_id, "workbench_chat_edit", {
                    "by": user.get("username", "system"), "part_id": selected.part_id,
                    "changes": changes, "model": model,
                })
                answer += "\n\n已应用到当前零件，并已创建可回溯版本。"
            else:
                answer += "\n\n未发现需要变更的值，当前参数保持不变。"
    store.audit(project_id, "workbench_chat", {
        "by": user.get("username", "system"),
        "part_id": body.part_id,
        "model": model,
        "question_length": len(body.message),
    })
    _save_project_chat_turn(project_id, body.message, answer, user, body.page_context or "2.1 图纸解析")
    return {"answer": answer, "model": model, "edit_applied": edit_applied}


@app.get("/api/projects/{project_id}/ai-chat")
def get_project_chat(project_id: str, user: dict = Depends(current_user)):
    """返回项目级共享对话；所有流程页面读取同一记录。"""
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return store.load_project_chat(project_id)


@app.post("/api/projects/{project_id}/ai-chat")
def project_chat(
    project_id: str, body: ProjectChatRequest, user: dict = Depends(current_user),
):
    """跨页面项目问答。仅走文本模型，且不对业务数据自动写入。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    meta = store.load_meta(project_id)
    if not meta:
        raise HTTPException(404, "项目不存在")
    ir = store.load_ir(project_id) or {}
    requirement = store.load_requirement(project_id) or {}
    report = store.load_process_report(project_id) or {}
    summary_doc = store.load_summary(project_id) or {}
    requirement_data = requirement.get("data", {}) if isinstance(requirement, dict) else {}
    context = {
        "project": {
            "project_id": project_id,
            "name": meta.get("project_name") or meta.get("source_filename") or project_id,
            "note": meta.get("note", ""),
            "attachments": meta.get("attachments", [])[:20],
        },
        "requirement": {
            "requirement_no": requirement.get("requirement_no", ""),
            "status": requirement.get("status", ""),
            "customer": requirement_data.get("final_customer_name") or requirement_data.get("customer_project", ""),
            "product": requirement_data.get("product_name") or requirement_data.get("product_model", ""),
            "industry": requirement_data.get("industry", ""),
        },
        "drawing_parse": {
            "device_name": ir.get("device_name", ""),
            "design_intent": ir.get("design_intent", ""),
            "parts": [
                {"part_id": part.get("part_id"), "name": part.get("name"), "quantity": part.get("quantity")}
                for part in (ir.get("parts", []) if isinstance(ir, dict) else [])[:80]
            ],
            "open_questions": (ir.get("open_questions", []) if isinstance(ir, dict) else [])[:20],
        },
        "process_summary": {"status": summary_doc.get("status", ""), "title": summary_doc.get("title", "")},
        "report": {"report_no": report.get("report_no", ""), "status": report.get("status", "")},
        "current_page": body.page_context or "项目工作流页面",
    }
    prompt = json.dumps(
        {"project_context": context, "recent_conversation": _project_chat_messages(project_id), "user_question": body.message},
        ensure_ascii=False,
        default=str,
    )
    if len(prompt) > 32000:
        prompt = prompt[:32000] + "\n【上下文按预算截断】"
    try:
        result = llm_client.complete_to_model(_PROJECT_CHAT_SYSTEM, prompt, ProjectChatAnswer, max_tokens=1200)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    answer = result.answer.strip()
    if not answer:
        raise HTTPException(502, "AI 未返回有效对话内容")
    model = qwen_client.last_used_model() if LLM_PROVIDER == "qwen" else active_text_model()
    _save_project_chat_turn(project_id, body.message, answer, user, body.page_context)
    store.audit(project_id, "project_chat", {
        "by": user.get("username", "system"), "page": body.page_context, "model": model,
        "question_length": len(body.message),
    })
    return {"answer": answer, "model": model}


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
    issues = geometry.preflight_parts([part])
    if issues:
        raise HTTPException(409, "单零件几何预检未通过：\n- " + "\n- ".join(dict.fromkeys(issues)))

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
    source_ir_hash = str(gp.get("source_ir_hash") or "")
    if source_ir_hash and source_ir_hash != _ir_snapshot(project_id):
        return None
    for p in gp.get("parts", []):
        if p.get("part_id") == part_id:
            return {"bbox": p.get("bbox"), "volume_mm3": p.get("volume_mm3"), "mass_g": p.get("mass_g")}
    return None


async def _read_attachments(attachments: List[UploadFile]):
    out = []
    for att in attachments or []:
        data = await _read_upload_limited(att, label="补充文件")
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
    expected_ir = _digest_value(ir_dict)

    def job():
        plan = process.decompose_process(part, overall=ir, geom=geom, note=note, attachments=atts)
        _assert_ir_unchanged(project_id, expected_ir)
        plan_dict = plan.model_dump()
        store.save_process(project_id, part_id, plan_dict, author=author)
        return {"plan": plan_dict, "validation": process.compute(plan_dict)}

    return {"task_id": tasks.submit(
        project_id, "process", job,
        dedup_key=_task_key("process", part_id, expected_ir, geom, note, atts),
    )}


@app.get("/api/projects/{project_id}/parts/{part_id}/process")
def get_process(project_id: str, part_id: str):
    """读取某零件已保存的工艺路线 + 确定性派生量(工时合计/依赖校验)。"""
    plan = store.load_process(project_id, part_id)
    if plan and not (plan.get("steps") or []):
        ir_dict = store.load_ir(project_id)
        part = next(
            (item for item in DesignIR(**ir_dict).parts if item.part_id == part_id), None
        ) if ir_dict else None
        if part:
            repaired = process.ensure_minimum_route(ProcessPlan.model_validate(plan), part)
            repaired.rule_warnings = process.validate_rules(repaired.model_dump(), part)
            plan = repaired.model_dump()
            store.save_process(project_id, part_id, plan, author="system_route_repair")
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
    ir_dict = store.load_ir(project_id)
    part = next((item for item in DesignIR(**ir_dict).parts if item.part_id == part_id), None) if ir_dict else None
    plan.rule_warnings = process.validate_rules(plan.model_dump(), part)
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
    expected_ir = _digest_value(ir_dict)

    def job():
        tasks.report_progress("正在调用模型生成零件成本拆解")
        analysis = cost.analyze_cost(part, overall=ir, geom=geom, quantity=qty,
                                     note=note, attachments=atts)
        tasks.report_progress("成本结果已返回，正在重算金额并保存价格依据")
        _assert_ir_unchanged(project_id, expected_ir)
        a_dict = analysis.model_dump()
        store.save_cost(project_id, part_id, a_dict, author=author)
        return {"analysis": a_dict, "summary": cost.compute(a_dict)}

    return {"task_id": tasks.submit(
        project_id, "cost", job,
        dedup_key=_task_key("cost", part_id, qty, expected_ir, geom, note, atts),
    )}


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
    return now_cst_str()


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
    dependency_hash = _digest_value((ir_dict, store.load_material(project_id)))

    def job():
        tasks.report_progress("正在调用模型生成材料方案")
        rec = material.recommend(ir=ir, note=note, web=True)
        tasks.report_progress("材料方案已返回，正在校验候选和供应要求")
        _assert_dependencies_unchanged(
            dependency_hash, (store.load_ir(project_id), store.load_material(project_id)), "IR 或材料草稿"
        )
        plan = _load_material_plan(project_id)
        previous_plan = plan.model_copy(deep=True)
        previous_body = plan.body.model_copy(deep=True)
        previous_metallization = plan.metallization.model_copy(deep=True)
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
        _sync_confirmation(previous_body, plan.body)
        _sync_confirmation(previous_metallization, plan.metallization)
        plan.updated_at = _now_str()
        d = plan.model_dump()
        store.save_material(project_id, d, author=author)
        if _business_changed(previous_plan, plan):
            store.invalidate_confirmations(
                project_id,
                ["manufacturing", "cleaning", "assembly", "production", "summary",
                 "costest", "pricing", "negotiation", "pricenego", "approval"],
                "材料定性方案已更新", author,
            )
        return {"material": d}

    return {"task_id": tasks.submit(
        project_id, "material_recommend", job,
        dedup_key=_task_key("material_recommend", dependency_hash, note),
    )}


@app.put("/api/projects/{project_id}/material")
def update_material(project_id: str, plan: MaterialPlan,
                    user: dict = Depends(current_user)):
    """保存人工编辑后的材料计划(选定材料/配方/粉末要求等)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    current = _load_material_plan(project_id)
    changed = _business_changed(current, plan)
    _sync_confirmation(current.body, plan.body)
    _sync_confirmation(current.metallization, plan.metallization)
    plan.project_id = project_id
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_material(project_id, d, author=user.get("username", "system"))
    if changed:
        store.invalidate_confirmations(
            project_id,
            ["manufacturing", "cleaning", "assembly", "production", "summary",
             "costest", "pricing", "negotiation", "pricenego", "approval"],
            "材料定性方案已人工修改", user.get("username", "system"),
        )
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
        if not (plan.metallization.paste or plan.metallization.layers or plan.metallization.rationale):
            raise HTTPException(400, "金属化方案为空；不适用时请填写不适用依据后再确认")
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
    store.invalidate_confirmations(
        project_id,
        ["manufacturing", "assembly", "production", "summary", "costest", "pricing",
         "negotiation", "pricenego", "approval"],
        "供应商能力评估结果已更新", user.get("username", "system"),
    )
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
    dependency_hash = _digest_value((ir_dict, material_plan, store.load_manufacturing(project_id)))

    def job():
        tasks.report_progress("正在调用模型生成制造工艺方案")
        rec = manufacturing.recommend(ir=ir, material_plan=material_plan, note=note, web=True)
        tasks.report_progress("制造方案已返回，正在校验工序与 BOM")
        _assert_dependencies_unchanged(
            dependency_hash,
            (store.load_ir(project_id), store.load_material(project_id), store.load_manufacturing(project_id)),
            "IR、材料方案或制造草稿",
        )
        plan = _load_manufacturing_plan(project_id)
        previous_plan = plan.model_copy(deep=True)
        previous_path = plan.path.model_copy(deep=True)
        previous_bom = plan.bom.model_copy(deep=True)
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
        _sync_confirmation(previous_path, plan.path)
        _sync_confirmation(previous_bom, plan.bom)
        plan.updated_at = _now_str()
        d = plan.model_dump()
        store.save_manufacturing(project_id, d, author=author)
        if _business_changed(previous_plan, plan):
            store.invalidate_confirmations(
                project_id,
                ["assembly", "production", "summary", "costest", "pricing",
                 "negotiation", "pricenego", "approval"],
                "制造工艺方案已更新", author,
            )
        return {"manufacturing": d}

    return {"task_id": tasks.submit(
        project_id, "manufacturing_recommend", job,
        dedup_key=_task_key("manufacturing_recommend", dependency_hash, note),
    )}


@app.put("/api/projects/{project_id}/manufacturing")
def update_manufacturing(project_id: str, plan: ManufacturingPlan,
                         user: dict = Depends(current_user)):
    """保存人工编辑后的制造工艺计划。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    current = _load_manufacturing_plan(project_id)
    changed = _business_changed(current, plan)
    _sync_confirmation(current.path, plan.path)
    _sync_confirmation(current.bom, plan.bom)
    plan.project_id = project_id
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_manufacturing(project_id, d, author=user.get("username", "system"))
    if changed:
        store.invalidate_confirmations(
            project_id,
            ["assembly", "production", "summary", "costest", "pricing",
             "negotiation", "pricenego", "approval"],
            "制造工艺方案已人工修改", user.get("username", "system"),
        )
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
    dependency_hash = _digest_value((ir_dict, material_plan, store.load_cleaning(project_id)))

    def job():
        tasks.report_progress("正在调用模型生成洁净方案")
        rec = cleaning.recommend(ir=ir, material_plan=material_plan, note=merged_note, web=True)
        tasks.report_progress("洁净方案已返回，正在校验等级和操作要求")
        _assert_dependencies_unchanged(
            dependency_hash,
            (store.load_ir(project_id), store.load_material(project_id), store.load_cleaning(project_id)),
            "IR、材料方案或清洗草稿",
        )
        plan = _load_cleaning_plan(project_id)
        previous = plan.model_copy(deep=True)
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
        _sync_confirmation(previous, plan)
        plan.updated_at = _now_str()
        d = plan.model_dump()
        store.save_cleaning(project_id, d, author=author)
        if _business_changed(previous, plan):
            store.invalidate_confirmations(project_id, ["summary"], "清洗方案已更新", author)
        return {"cleaning": d}

    return {"task_id": tasks.submit(
        project_id, "cleaning_recommend", job,
        dedup_key=_task_key("cleaning_recommend", dependency_hash, merged_note),
    )}


@app.put("/api/projects/{project_id}/cleaning")
def update_cleaning(project_id: str, plan: CleaningPlan,
                    user: dict = Depends(current_user)):
    """保存人工编辑后的清洗方案。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    current = _load_cleaning_plan(project_id)
    changed = _business_changed(current, plan)
    _sync_confirmation(current, plan)
    plan.project_id = project_id
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_cleaning(project_id, d, author=user.get("username", "system"))
    if changed:
        store.invalidate_confirmations(
            project_id, ["summary"], "清洗方案已人工修改", user.get("username", "system")
        )
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
    dependency_hash = _digest_value((
        ir_dict, material_plan, manufacturing_plan, store.load_costest(project_id),
    ))
    dependency_hash = _digest_value((
        ir_dict, material_plan, manufacturing_plan, store.load_assembly(project_id),
    ))

    def job():
        tasks.report_progress("正在调用模型生成组装检测方案")
        rec = assembly.recommend(ir=ir, material_plan=material_plan,
                                 manufacturing_plan=manufacturing_plan, note=note, web=True)
        tasks.report_progress("组装检测方案已返回，正在校验检测项和依赖")
        _assert_dependencies_unchanged(
            dependency_hash,
            (store.load_ir(project_id), store.load_material(project_id),
             store.load_manufacturing(project_id), store.load_assembly(project_id)),
            "IR、上游工艺或组装草稿",
        )
        plan = _load_assembly_plan(project_id)
        previous_plan = plan.model_copy(deep=True)
        previous_assembly = plan.assembly.model_copy(deep=True)
        previous_inspection = plan.inspection.model_copy(deep=True)
        plan.assembly.method = rec.bonding_method
        plan.assembly.rationale = rec.bonding_rationale
        plan.assembly.steps = rec.assembly_steps
        plan.inspection.tests = rec.tests
        plan.summary = rec.summary
        plan.assumptions = rec.assumptions
        plan.open_questions = rec.open_questions
        plan.search_sources = rec.search_sources
        _sync_confirmation(previous_assembly, plan.assembly)
        _sync_confirmation(previous_inspection, plan.inspection)
        plan.updated_at = _now_str()
        d = plan.model_dump()
        store.save_assembly(project_id, d, author=author)
        if _business_changed(previous_plan, plan):
            store.invalidate_confirmations(project_id, ["summary"], "组装检测方案已更新", author)
        return {"assembly": d}

    return {"task_id": tasks.submit(
        project_id, "assembly_recommend", job,
        dedup_key=_task_key("assembly_recommend", dependency_hash, note),
    )}


@app.put("/api/projects/{project_id}/assembly")
def update_assembly(project_id: str, plan: AssemblyPlan,
                    user: dict = Depends(current_user)):
    """保存人工编辑后的组装与检测方案。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    current = _load_assembly_plan(project_id)
    changed = _business_changed(current, plan)
    _sync_confirmation(current.assembly, plan.assembly)
    _sync_confirmation(current.inspection, plan.inspection)
    plan.project_id = project_id
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_assembly(project_id, d, author=user.get("username", "system"))
    if changed:
        store.invalidate_confirmations(
            project_id, ["summary"], "组装检测方案已人工修改", user.get("username", "system")
        )
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
    dependency_hash = _digest_value((ir_dict, manufacturing_plan, equipment, store.load_production(project_id)))

    def job():
        tasks.report_progress("正在调用模型生成产能评估方案")
        rec = production.recommend(ir=ir, manufacturing_plan=manufacturing_plan,
                                   equipment=equipment, note=note, web=True)
        tasks.report_progress("产能方案已返回，正在校验设备和产能约束")
        _assert_dependencies_unchanged(
            dependency_hash,
            (store.load_ir(project_id), store.load_manufacturing(project_id),
             store.list_equipment(), store.load_production(project_id)),
            "IR、制造方案、设备台账或产线草稿",
        )
        plan = _load_production_plan(project_id)
        previous_plan = plan.model_copy(deep=True)
        previous_inhouse = plan.inhouse.model_copy(deep=True)
        previous_outsourcing = plan.outsourcing.model_copy(deep=True)
        plan.requirements = rec.requirements
        plan.inhouse.matches = rec.inhouse_matches
        plan.outsourcing.plans = rec.outsourcing
        plan.capacity_summary = rec.capacity_summary
        plan.conclusion = rec.conclusion
        plan.assumptions = rec.assumptions
        plan.open_questions = rec.open_questions
        plan.search_sources = rec.search_sources
        _sync_confirmation(previous_inhouse, plan.inhouse)
        _sync_confirmation(previous_outsourcing, plan.outsourcing)
        plan.updated_at = _now_str()
        d = plan.model_dump()
        store.save_production(project_id, d, author=author)
        if _business_changed(previous_plan, plan):
            store.invalidate_confirmations(project_id, ["summary"], "产线产能方案已更新", author)
        return {"production": d}

    return {"task_id": tasks.submit(
        project_id, "production_recommend", job,
        dedup_key=_task_key("production_recommend", dependency_hash, note),
    )}


@app.put("/api/projects/{project_id}/production")
def update_production(project_id: str, plan: ProductionPlan,
                      user: dict = Depends(current_user)):
    """保存人工编辑后的产线匹配与产能评估。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    current = _load_production_plan(project_id)
    changed = _business_changed(current, plan)
    _sync_confirmation(current.inhouse, plan.inhouse)
    _sync_confirmation(current.outsourcing, plan.outsourcing)
    plan.project_id = project_id
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_production(project_id, d, author=user.get("username", "system"))
    if changed:
        store.invalidate_confirmations(
            project_id, ["summary"], "产线产能方案已人工修改", user.get("username", "system")
        )
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
    if section == "inhouse" and not plan.inhouse.matches:
        raise HTTPException(400, "尚无自有产线匹配结果")
    if section == "outsourcing" and not plan.outsourcing.plans:
        raise HTTPException(400, "尚无外协处置方案")
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
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    author = user.get("username", "system")
    submitted_aggregate = summary_svc.aggregate(project_id)
    dependency_hash = _digest_value(submitted_aggregate)

    def job():
        tasks.report_progress("正在调用模型汇总工艺评估结果")
        rec = summary_svc.recommend(submitted_aggregate, web=False)
        tasks.report_progress("汇总结果已返回，正在生成审签报告")
        _assert_dependencies_unchanged(
            dependency_hash, summary_svc.aggregate(project_id), "工艺汇总输入"
        )
        doc = _load_summary_doc(project_id)
        previous = doc.model_copy(deep=True)
        doc.overview = rec.overview
        doc.highlights = rec.highlights
        doc.risks = rec.risks
        doc.conclusion = rec.conclusion
        doc.search_sources = rec.search_sources
        _sync_confirmation(previous, doc)
        doc.updated_at = _now_str()
        d = doc.model_dump()
        store.save_summary(project_id, d, author=author)
        return {"summary": d}

    return {"task_id": tasks.submit(
        project_id, "summary_recommend", job,
        dedup_key=_task_key("summary_recommend", dependency_hash),
    )}


@app.put("/api/projects/{project_id}/summary")
def update_summary(project_id: str, doc: SummaryDoc, user: dict = Depends(current_user)):
    """保存人工编辑后的执行摘要。"""
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    _sync_confirmation(_load_summary_doc(project_id), doc)
    doc.project_id = project_id
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_summary(project_id, d, author=user.get("username", "system"))
    return {"summary": d}


@app.post("/api/projects/{project_id}/summary/confirm")
def confirm_summary(project_id: str, user: dict = Depends(current_user)):
    """确认技术工艺总结(定稿)。"""
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    doc = _load_summary_doc(project_id)
    if not doc.overview or not doc.conclusion:
        raise HTTPException(400, "总结概述和总体结论完整后才能确认定稿")
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
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
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


def _require_record_edit(user: dict, record: dict | None = None, project_id: str = "") -> None:
    """技术工艺/报价管理记录也遵循项目归属，不能绕过项目级写入守卫。"""
    role = user.get("role")
    if role in {"admin", "process_manager"}:
        return
    if role != "engineer":
        raise HTTPException(403, "需要工艺工程师、技术经理或管理员权限")
    if record and record.get("owner") != user.get("username"):
        raise HTTPException(403, "工艺工程师只能修改本人创建的管理记录")
    target_project = project_id or (record or {}).get("project_id", "")
    if target_project:
        meta = _workflow_project(target_project)
        if not auth.can_edit_project(user, meta):
            raise HTTPException(403, "工艺工程师只能操作本人创建项目的管理记录")


@app.post("/api/techprocess/records")
def register_record_ep(body: TechProcessRecord, user: dict = Depends(current_user)):
    """「结束」步最终确认:把当前技术工艺/报价录入到对应管理列表(按 project_id+biz 幂等)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    biz = body.biz or "tech"
    records = store.list_records()
    _require_record_edit(user, project_id=body.project_id)

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
        _require_record_edit(user, existing, body.project_id)
        existing.update({"name": name, "status": status, "note": body.note or existing.get("note"),
                         "updated_at": _now_str()})
        return {"record": store.save_record(existing)}

    prefix = "BJ" if biz == "quote" else "GY"
    seq = sum(1 for r in records if r.get("biz") == biz) + 1
    rec = {
        "id": "rec_" + __import__("uuid").uuid4().hex[:8],
        "code": f"{prefix}{now_cst_str('%Y%m%d')}-{seq:03d}",
        "name": name, "project_id": body.project_id, "biz": biz, "status": status,
        "owner": who, "created_at": _now_str(), "note": body.note, "editable": True,
    }
    return {"record": store.save_record(rec)}


@app.delete("/api/techprocess/records/{record_id}")
def delete_record_ep(record_id: str, user: dict = Depends(current_user)):
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    record = store.get_record(record_id)
    if not record:
        raise HTTPException(404, "管理记录不存在")
    _require_record_edit(user, record)
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
        tasks.report_progress("正在调用模型生成完整成本测算")
        rec = costest.recommend(ir=ir, material_plan=material_plan,
                                manufacturing_plan=manufacturing_plan, note=note, web=True)
        tasks.report_progress("成本测算已返回，正在重算合计并检查依赖")
        _assert_dependencies_unchanged(
            dependency_hash,
            (store.load_ir(project_id), store.load_material(project_id),
             store.load_manufacturing(project_id), store.load_costest(project_id)),
            "成本测算输入或成本草稿",
        )
        doc = _load_costest(project_id)
        previous = doc.model_copy(deep=True)
        doc.material_costs = rec.material_costs
        doc.manufacturing_costs = rec.manufacturing_costs
        doc.technical_costs = rec.technical_costs
        doc.market_notes = rec.market_notes
        doc.summary = rec.summary
        doc.assumptions = rec.assumptions
        doc.open_questions = rec.open_questions
        doc.search_sources = rec.search_sources
        _sync_confirmation(previous, doc)
        d = _save_costest_with_totals(doc, project_id, author)
        if _business_changed(previous, doc):
            store.invalidate_confirmations(
                project_id, ["pricing", "negotiation", "pricenego", "approval"],
                "成本测算已更新", author,
            )
        return {"costest": d, "totals": d["totals"]}

    return {"task_id": tasks.submit(
        project_id, "costest_recommend", job,
        dedup_key=_task_key("costest_recommend", dependency_hash, note),
    )}


@app.put("/api/projects/{project_id}/costest")
def update_costest(project_id: str, doc: CostEstimate, user: dict = Depends(current_user)):
    """保存人工编辑后的成本测算(平台重算合计)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    current = _load_costest(project_id)
    changed = _business_changed(current, doc)
    _sync_confirmation(current, doc)
    d = _save_costest_with_totals(doc, project_id, user.get("username", "system"))
    if changed:
        store.invalidate_confirmations(
            project_id, ["pricing", "negotiation", "pricenego", "approval"],
            "成本测算已人工修改", user.get("username", "system"),
        )
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
    dependency_hash = _digest_value((ir_dict, ce, store.load_pricing(project_id)))

    def job():
        tasks.report_progress("正在调用模型生成定价建议")
        rec = pricing.recommend(ir=ir, costest=ce, note=note, web=True)
        tasks.report_progress("定价建议已返回，正在按成本基数计算报价")
        current_costest = store.load_costest(project_id)
        if current_costest and not current_costest.get("totals"):
            current_costest = dict(current_costest)
            current_costest["totals"] = costest.compute(current_costest).model_dump()
        _assert_dependencies_unchanged(
            dependency_hash,
            (store.load_ir(project_id), current_costest, store.load_pricing(project_id)),
            "定价输入或定价草稿",
        )
        doc = _load_pricing(project_id)
        previous = doc.model_copy(deep=True)
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
        pricing_changed = not _sync_pricing_approval(previous, doc)
        d = _save_pricing_calc(doc, project_id, author)
        if pricing_changed:
            store.invalidate_confirmations(
                project_id, ["negotiation", "pricenego", "approval"], "定价方案已更新", author
            )
        return {"pricing": d}

    return {"task_id": tasks.submit(
        project_id, "pricing_recommend", job,
        dedup_key=_task_key("pricing_recommend", dependency_hash, note),
    )}


@app.put("/api/projects/{project_id}/pricing")
def update_pricing(project_id: str, doc: PricingPlan, user: dict = Depends(current_user)):
    """保存人工编辑后的定价方案(平台重算价格)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    current = _load_pricing(project_id)
    changed = not _sync_pricing_approval(current, doc)
    d = _save_pricing_calc(doc, project_id, user.get("username", "system"))
    if changed:
        store.invalidate_confirmations(
            project_id, ["negotiation", "pricenego", "approval"],
            "定价方案已人工修改", user.get("username", "system"),
        )
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
    _require(user, auth.FINANCE_ROLES, "需要财务负责人或管理员权限")
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
    dependency_hash = _digest_value((ir_dict, pricing_plan, store.load_negotiation(project_id)))

    def job():
        tasks.report_progress("正在调用模型生成商务谈判策略")
        rec = negotiation.recommend(ir=ir, pricing=pricing_plan, note=note, web=False)
        tasks.report_progress("谈判策略已返回，正在校验报价依赖")
        _assert_dependencies_unchanged(
            dependency_hash,
            (store.load_ir(project_id), store.load_pricing(project_id), store.load_negotiation(project_id)),
            "商务策略输入或谈判策略草稿",
        )
        doc = _load_negotiation(project_id)
        previous = doc.model_copy(deep=True)
        doc.terms = rec.terms
        doc.strategies = rec.strategies
        doc.summary = rec.summary
        doc.assumptions = rec.assumptions
        doc.open_questions = rec.open_questions
        doc.search_sources = rec.search_sources
        _sync_confirmation(previous, doc)
        doc.updated_at = _now_str()
        d = doc.model_dump()
        store.save_negotiation(project_id, d, author=author)
        if _business_changed(previous, doc):
            store.invalidate_confirmations(
                project_id, ["pricenego", "approval"], "商务谈判策略已更新", author
            )
        return {"negotiation": d}

    return {"task_id": tasks.submit(
        project_id, "negotiation_recommend", job,
        dedup_key=_task_key("negotiation_recommend", dependency_hash, note),
    )}


@app.put("/api/projects/{project_id}/negotiation")
def update_negotiation(project_id: str, doc: NegotiationPlan, user: dict = Depends(current_user)):
    """保存人工编辑后的商务及谈判策略。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    current = _load_negotiation(project_id)
    changed = _business_changed(current, doc)
    _sync_confirmation(current, doc)
    doc.project_id = project_id
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_negotiation(project_id, d, author=user.get("username", "system"))
    if changed:
        store.invalidate_confirmations(
            project_id, ["pricenego", "approval"], "商务谈判策略已人工修改",
            user.get("username", "system"),
        )
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
    dependency_hash = _digest_value((
        ir_dict, pricing_plan, negotiation_plan, store.load_pricenego(project_id),
    ))

    def job():
        tasks.report_progress("正在调用模型生成价格协商建议")
        rec = pricenego.recommend(ir=ir, pricing=pricing_plan,
                                  negotiation=negotiation_plan, note=note, web=True)
        tasks.report_progress("价格协商建议已返回，正在校验调整项")
        _assert_dependencies_unchanged(
            dependency_hash,
            (store.load_ir(project_id), store.load_pricing(project_id),
             store.load_negotiation(project_id), store.load_pricenego(project_id)),
            "价格协商输入或协商草稿",
        )
        doc = _load_pricenego(project_id)
        previous = doc.model_copy(deep=True)
        doc.initial_quote = rec.initial_quote
        doc.tiered_prices = rec.tiered_prices
        doc.price_linkage = rec.price_linkage
        doc.special_terms = rec.special_terms
        doc.summary = rec.summary
        doc.assumptions = rec.assumptions
        doc.open_questions = rec.open_questions
        doc.search_sources = rec.search_sources
        _sync_confirmation(previous, doc)
        doc.updated_at = _now_str()
        d = doc.model_dump()
        store.save_pricenego(project_id, d, author=author)
        if _business_changed(previous, doc):
            store.invalidate_confirmations(project_id, ["approval"], "价格协商内容已更新", author)
        return {"pricenego": d}

    return {"task_id": tasks.submit(
        project_id, "pricenego_recommend", job,
        dedup_key=_task_key("pricenego_recommend", dependency_hash, note),
    )}


@app.put("/api/projects/{project_id}/pricenego")
def update_pricenego(project_id: str, doc: PriceNegotiation, user: dict = Depends(current_user)):
    """保存人工编辑后的价格协商(含新增的协商轮次)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    current = _load_pricenego(project_id)
    changed = _business_changed(current, doc)
    _sync_confirmation(current, doc)
    doc.project_id = project_id
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_pricenego(project_id, d, author=user.get("username", "system"))
    if changed:
        store.invalidate_confirmations(
            project_id, ["approval"], "价格协商内容已人工修改", user.get("username", "system")
        )
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
    dependency_hash = _digest_value((ir_dict, pricing_plan, pn, store.load_approval(project_id)))

    def job():
        tasks.report_progress("正在调用模型生成审批定级建议")
        rec = approval_svc.recommend(ir=ir, pricing=pricing_plan, pricenego=pn, note=note, web=False)
        tasks.report_progress("审批建议已返回，正在生成审批节点")
        _assert_dependencies_unchanged(
            dependency_hash,
            (store.load_ir(project_id), store.load_pricing(project_id),
             store.load_pricenego(project_id), store.load_approval(project_id)),
            "审批定级输入或审批草稿",
        )
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

    return {"task_id": tasks.submit(
        project_id, "approval_recommend", job,
        dedup_key=_task_key("approval_recommend", dependency_hash, note),
    )}


@app.post("/api/projects/{project_id}/approval/level")
def set_approval_level(project_id: str, level: int, user: dict = Depends(current_user)):
    """人工设定/调整审批级别(重建审批链,回到草稿态)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if level not in (1, 2, 3):
        raise HTTPException(400, "level 必须为 1/2/3")
    minimum_level, reason = approval_svc.determine_level(
        store.load_pricing(project_id), store.load_pricenego(project_id)
    )
    if level < minimum_level:
        raise HTTPException(409, f"审批级别不得低于平台风险矩阵确定的 L{minimum_level}：{reason}")
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
    minimum_level, reason = approval_svc.determine_level(
        store.load_pricing(project_id), store.load_pricenego(project_id)
    )
    if doc.level < minimum_level:
        raise HTTPException(409, f"当前审批级别低于最低 L{minimum_level}：{reason}")
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
    required_roles = [node.role for node in approval_svc.build_chain(doc.level)]
    if approvers != required_roles:
        raise HTTPException(409, "审批链必须严格匹配当前级别：" + " → ".join(required_roles))
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
    _require(user, auth.QUOTE_APPROVAL_ROLES, "需要当前报价审批节点对应角色或管理员权限")
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
    user_role = user.get("role")
    expected_role = auth.QUOTE_NODE_BY_ROLE.get(user_role)
    if user_role != "admin" and expected_role != node.role:
        raise HTTPException(403, f"当前节点需要“{node.role}”审批")
    if user_role != "admin" and any(item.approver == who for item in doc.chain[:idx]):
        raise HTTPException(409, "同一人员不能连续审批多个报价授权节点")
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
    current_ir = store.load_ir(project_id)
    current_hash = _digest_value(current_ir or {})
    geometry_result = store.load_geometry_result(project_id)
    drawings_result = store.load_drawings_result(project_id)
    geometry_stale = bool(
        geometry_result and (
            (geometry_result.get("source_ir_hash") and geometry_result.get("source_ir_hash") != current_hash)
            or (not geometry_result.get("source_ir_hash") and meta.get("derived_results_stale"))
        )
    )
    drawings_stale = bool(
        drawings_result and (
            (drawings_result.get("source_ir_hash") and drawings_result.get("source_ir_hash") != current_hash)
            or (not drawings_result.get("source_ir_hash") and meta.get("derived_results_stale"))
        )
    )
    return {
        "meta": meta,
        "ir": current_ir,
        "geometry": None if geometry_stale else geometry_result,
        "drawings": None if drawings_stale else drawings_result,
        "artifact_status": {"geometry_stale": geometry_stale, "drawings_stale": drawings_stale},
    }


@app.get("/api/projects/{project_id}/ai-results")
def get_ai_results(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {
        "results": store.load_ai_result_metadata(project_id),
        "drawing_analysis": store.load_drawing_analysis(project_id),
    }


@app.get("/api/projects/{project_id}/audit")
def get_audit(project_id: str):
    """项目审计轨迹(可追溯): 每次解析/校验/生成/导出等动作的留痕。"""
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"audit": store.list_audit(project_id)}


# --------------------------------------------------------------------------- #
# 工艺评估业务流程: 接受需求 -> 图纸解析 -> 工艺评估报告汇总/审核/发布。
# 这些端点只编排和留痕既有数据，不会触发任何模型调用。
# --------------------------------------------------------------------------- #
def _workflow_project(project_id: str) -> dict:
    meta = store.load_meta(project_id)
    if not meta or meta.get("deleted_at"):
        raise HTTPException(404, "项目不存在")
    return meta


def _requirement_no(project_id: str) -> str:
    return f"REQ-{project_id.upper()}"


def _report_no(project_id: str, requirement_no: str = "") -> str:
    """报告编号沿用 RPT 前缀，并与项目流水号保持一一对应。"""
    return f"RPT-{project_id.upper()}"


def _workflow_event(action: str, user: dict, comment: str = "") -> WorkflowReview:
    return WorkflowReview(
        action=action,
        actor=user.get("username", "system"),
        role=user.get("role", ""),
        comment=comment or "",
        at=_now_str(),
    )


def _is_filled(value) -> bool:
    """确认页的规则检查：空值与明确标为待确认的数据都需要人工补充。"""
    if value is None:
        return False
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    text = str(value).strip()
    return bool(text) and "待确认" not in text and "系统自动" not in text


def _requirement_precheck(project_id: str, doc: RequirementDoc) -> dict:
    """基于已保存需求字段的确定性完整性检查；不调用任何 AI/模型。"""
    data = doc.data or {}
    meta = store.load_meta(project_id) or {}
    industry = str(data.get("industry") or "semiconductor").strip().lower()
    product_checks = [
        ("三、产品技术规格（Section C）", ["product_name", "product_model", "overall_dimensions", "base_material"], "产品基础规格已录入"),
        ("3.1 基础参数", ["product_name", "wafer_size", "base_material", "overall_dimensions"], "基础参数已录入"),
        ("3.2 精度与性能参数", ["roughness", "adsorption_uniformity", "temperature_range", "cleanliness"], "性能要求已录入"),
        ("3.3 应用场景", ["target_equipment", "process_stage", "vacuum_environment"], "应用场景已录入"),
    ]
    if industry == "battery":
        product_checks = [
            ("三、产品技术规格（Section C）", ["battery_model", "cathode_material", "anode_material", "nominal_voltage"], "电芯基础规格已录入"),
            ("3.1 基本电性能参数", ["battery_model", "cathode_material", "anode_material", "nominal_voltage"], "基本电性能参数已录入"),
            ("3.2 安全与可靠性参数", ["battery_operating_temperature", "cycle_life"], "安全与可靠性参数已录入"),
            ("3.3 核心工艺特点", ["stacking_process"], "核心工艺特点已录入"),
            ("3.4 形状与尺寸", ["battery_form_factor"], "形状与尺寸已录入"),
        ]
    elif industry == "flexible":
        dynamic_fields = (data.get("flexible_spec") or {}).get("fields") or []
        required_by_section = {
            section: [str(field.get("key") or "") for field in dynamic_fields if field.get("section") == section and field.get("required")]
            for section in ("3.1", "3.2", "3.3")
        }
        product_checks = [
            ("三、产品技术规格（Section C）", [], "灵活行业规格由 AI 根据技术资料生成"),
            ("3.1 基础参数", required_by_section["3.1"], "AI 生成的基础参数已录入"),
            ("3.2 精度与性能参数", required_by_section["3.2"], "AI 生成的性能要求已录入"),
            ("3.3 应用场景", required_by_section["3.3"], "AI 生成的应用场景已录入"),
        ]
    checks = [
        ("一、需求基本信息（Section A）", ["title", "requirement_type", "priority", "bu", "disclosure", "description"], "基础信息完整"),
        ("二、客户与项目信息（Section B）", ["customer_type", "customer_industry", "final_customer_name", "project_name", "project_code", "product_iteration"], "客户与项目字段完整"),
        *product_checks,
        ("3.4 图纸与技术资料", [], "原始图纸已关联"),
        ("四、市场与商务信息（Section D）", ["annual_forecast", "first_sample_due", "mass_production_due"], "商务信息已录入"),
        ("五、项目时间计划（Section E）", ["evaluation_due", "milestones"], "时间节点已录入"),
        ("六、分类与标签（Section F）", ["category_a", "product_type", "complexity"], "分类清晰"),
        ("七、备注与附件（Section G）", [], "原始图纸已上传"),
    ]
    items = []
    dynamic_values = {
        str(field.get("key") or ""): field.get("value")
        for field in ((data.get("flexible_spec") or {}).get("fields") or [])
        if isinstance(field, dict)
    }
    for label, fields, ok_message in checks:
        missing = [field for field in fields if not _is_filled(data.get(field, dynamic_values.get(field)))]
        if label == "3.4 图纸与技术资料" or label.startswith("七、"):
            if not meta.get("source_filename"):
                missing.append("source")
        if missing:
            items.append({"item": label, "status": "need_info", "detail": f"待补充：{', '.join(missing)}"})
        else:
            items.append({"item": label, "status": "ok", "detail": ok_message})
    needs = [row for row in items if row["status"] == "need_info"]
    generated_note = (
        "系统完整性检查完成：全部关键字段已具备，可提交审核。"
        if not needs else
        "系统完整性检查发现待补充项：" + "；".join(row["item"] + "（" + row["detail"] + "）" for row in needs) + "。"
    )
    return {"items": items, "ok": not needs, "generated_note": generated_note, "engine": "deterministic_rules"}


_REQUIREMENT_AI_CHECK_SYSTEM = """你是半导体零部件工艺评估需求单的审核工程师。
请核对“需求表单字段”和（如有）“原始工程图”，判断信息是否足以进入需求审核。
只根据输入资料判断；图上或字段里没有明确的信息，必须标为 need_info，不能臆测。

你必须检查下列 11 项，items 中每一项的 item 必须逐字使用下列名称，顺序也必须相同：
1. 一、需求基本信息（Section A）
2. 二、客户与项目信息（Section B）
3. 三、产品技术规格（Section C）
4. 3.1 基础参数
5. 3.2 精度与性能参数
6. 3.3 应用场景
7. 3.4 图纸与技术资料
8. 四、市场与商务信息（Section D）
9. 五、项目时间计划（Section E）
10. 六、分类与标签（Section F）
11. 七、备注与附件（Section G）

输出 JSON 对象：
{
  "summary": "不超过 120 字的总体结论",
  "items": [
    {"item": "上述固定名称", "status": "ok 或 need_info", "detail": "不超过 60 字的依据或待补充项"}
  ]
}
status 只能是 ok 或 need_info。不要输出 Markdown、不要追加解释。"""


def _normalize_requirement_ai_check(rule_check: dict, result: RequirementAiCheckResult) -> dict:
    """模型输出即使少项/标签轻微偏差，也固定映射回确认页的 11 项检查表。"""
    reference = rule_check.get("items") or []
    raw = result.items or []

    def match(label: str, index: int):
        for row in raw:
            candidate = (row.item or "").strip()
            if candidate == label or (candidate and (candidate in label or label in candidate)):
                return row
        # Qwen 已被要求固定顺序；仅在数量完全一致时按顺序兜底，避免错位覆盖。
        return raw[index] if len(raw) == len(reference) else None

    rows = []
    for index, fallback in enumerate(reference):
        row = match(fallback["item"], index)
        raw_status = (row.status if row else fallback["status"]).strip().lower()
        status = "ok" if raw_status in ("ok", "确认ok", "通过", "完整") else "need_info"
        detail = (row.detail if row and row.detail else fallback["detail"]).strip()
        rows.append({"item": fallback["item"], "status": status, "detail": detail[:240]})
    summary = (result.summary or "").strip()[:500]
    if not summary:
        summary = "AI 已完成需求单检查，请根据各项结论补充或确认。"
    actual_model = str(qwen_client.last_used_model() or "").strip()
    return {
        "items": rows,
        "ok": all(row["status"] == "ok" for row in rows),
        "generated_note": summary,
        "engine": "qwen",
        # 只记录本次请求实际成功使用的模型；没有返回实际标识时保持为空，不能伪装成配置默认模型。
        "model": actual_model,
        "model_source": "runtime_actual" if actual_model else "unavailable",
        "checked_at": _now_str(),
    }


def _history_key(value: object) -> str:
    """用于历史比对的保守标准化：忽略空白和常见标点，但不做模糊猜测。"""
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(value or "")).casefold()


def _history_requirement_autofill(project_id: str, current: dict, extracted: dict, title: str = "") -> tuple[dict, dict]:
    """从此前需求单判定客户新旧及产品全新/迭代。

    仅采用名称或型号的精确标准化匹配，避免把相似项目误判为老客户/迭代。
    人工已经填写的值永远优先；返回的 evidence 会随需求单保存，方便追溯判定依据。
    """
    merged = {**(extracted or {}), **(current or {})}
    customer_values = [
        str(merged.get("final_customer_name") or "").strip(),
        str(merged.get("transaction_customer_name") or "").strip(),
    ]
    customer_keys = {key for value in customer_values if (key := _history_key(value))}
    product_values = [
        str(merged.get("product_model") or "").strip(),
        str(merged.get("product_name") or "").strip(),
        str(merged.get("project_name") or "").strip(),
    ]
    product_keys = {key for value in product_values if (key := _history_key(value))}

    customer_matches: list[dict] = []
    product_matches: list[dict] = []
    for row in store.list_requirements():
        previous = row.get("requirement") or {}
        previous_project = row.get("project") or {}
        if previous.get("project_id") == project_id or previous_project.get("project_id") == project_id:
            continue
        previous_data = previous.get("data") or {}
        previous_customer_keys = {
            _history_key(previous_data.get(field))
            for field in ("final_customer_name", "transaction_customer_name")
            if _history_key(previous_data.get(field))
        }
        previous_product_keys = {
            _history_key(previous_data.get(field))
            for field in ("product_model", "product_name", "project_name")
            if _history_key(previous_data.get(field))
        }
        reference = {
            "project_id": previous_project.get("project_id") or previous.get("project_id"),
            "requirement_no": previous.get("requirement_no") or "—",
            "title": previous.get("title") or previous_data.get("title") or previous_project.get("source_name") or "未命名需求",
            "created_at": previous.get("created_at") or previous_project.get("created_at") or "",
        }
        if customer_keys and customer_keys.intersection(previous_customer_keys):
            customer_matches.append(reference)
        if product_keys and product_keys.intersection(previous_product_keys):
            product_matches.append(reference)

    fields: dict[str, str] = {}
    if customer_keys and not str(current.get("customer_type") or "").strip():
        fields["customer_type"] = "old" if customer_matches else "new"
    # 产品型号、产品名称或项目名称只要与已归档需求精确对应，即视为迭代；否则是全新。
    # requirement_type 与“全新/迭代”保持一致，避免同一页面两个选择项互相矛盾。
    current_requirement_type = str(current.get("requirement_type") or "").strip()
    if product_keys:
        iteration_value = "iteration" if product_matches else "new"
        if not str(current.get("product_iteration") or "").strip():
            fields["product_iteration"] = iteration_value
        if not current_requirement_type or current_requirement_type == "工艺评估":
            fields["requirement_type"] = iteration_value

    evidence = {
        "engine": "historical_requirement_comparison",
        "compared_at": _now_str(),
        "customer_candidates": [value for value in customer_values if value],
        "product_candidates": [value for value in product_values if value],
        "customer_match_count": len(customer_matches),
        "product_match_count": len(product_matches),
        "customer_matches": customer_matches[:10],
        "product_matches": product_matches[:10],
        "decision": {
            "customer_type": fields.get("customer_type") or str(current.get("customer_type") or ""),
            "product_iteration": fields.get("product_iteration") or str(current.get("product_iteration") or ""),
            "requirement_type": fields.get("requirement_type") or current_requirement_type,
        },
    }
    return fields, evidence


def _apply_requirement_document_extraction(
    project_id: str,
    doc: RequirementDoc,
    result: RequirementDocumentExtraction,
    processed_files: list[str],
    skipped_files: list[str],
    user: dict,
) -> dict:
    """仅补齐空字段，保留首页输入和人工已填内容，形成可追溯的 1.1 草稿。"""
    data = dict(doc.data or {})
    filled: list[str] = []
    recommended: dict[str, str] = {}
    recommendation_confidence: dict[str, float] = {}
    industry_selection = str(data.get("industry_selection") or data.get("industry") or "semiconductor").strip().lower()
    detected_industry = str(result.industry or "flexible").strip().lower()
    if detected_industry not in {"semiconductor", "battery", "flexible"}:
        detected_industry = "flexible"
    industry = industry_selection if industry_selection in {"semiconductor", "battery", "flexible"} else "semiconductor"
    required_recommendation_fields = requirement_extract._required_recommendation_fields_for_industry(industry)
    previous_extraction = data.get("document_extraction") or {}
    previous_recommendations = dict(previous_extraction.get("recommendations") or {})
    ai_recommendation_keys = {
        *previous_recommendations,
        *(previous_extraction.get("recommended_fields") or []),
        *(previous_extraction.get("all_recommended_fields") or []),
    }
    # 清理旧版本曾经写入的非必填推荐，但只清理“值仍等于旧推荐”的字段；
    # 用户后来手工改过的内容继续保留。
    for key, old_value in previous_recommendations.items():
        if key in required_recommendation_fields or key.startswith("flexible_spec."):
            continue
        if str(data.get(key, "")).strip() == str(old_value or "").strip():
            data[key] = ""
    # 旧版本可能把模型自造的枚举文字（例如 refrigerator）直接写进 data。
    # 只处理 AI 来源且未被人工改写的值；人工输入仍由页面枚举控件和校验保护。
    for key in ai_recommendation_keys:
        current = str(data.get(key, "")).strip()
        if not current or key not in requirement_extract._RECOMMENDATION_ENUMS:
            continue
        normalized = requirement_extract._normalize_recommendation_value(key, current)
        if normalized != current:
            data[key] = ""
    for key, value in result.fields.items():
        existing = str(data.get(key, "")).strip()
        if existing:
            continue
        normalized = requirement_extract._normalize_recommendation_value(key, value)
        if key in requirement_extract._RECOMMENDATION_ENUMS and normalized != str(value).strip():
            result.recommendations[key] = normalized
            continue
        data[key] = value
        filled.append(key)
    # 全新/迭代必须先由历史需求比对决定，再处理通用 AI 推荐；否则通用兜底的
    # product_iteration=new 可能会把历史命中的迭代需求提前占住。
    history_fields, history_evidence = _history_requirement_autofill(
        project_id, data, result.fields, result.title,
    )
    for key, value in history_fields.items():
        if not str(data.get(key, "")).strip():
            data[key] = value
    for key, value in result.recommendations.items():
        if key not in required_recommendation_fields:
            continue
        existing = str(data.get(key, "")).strip()
        if existing:
            continue
        normalized = requirement_extract._normalize_recommendation_value(key, value)
        data[key] = normalized
        recommended[key] = normalized
        try:
            confidence = float((result.recommendation_confidence or {}).get(key, 0.35))
        except (TypeError, ValueError):
            confidence = 0.35
        recommendation_confidence[key] = max(0.0, min(1.0, confidence))
    # 最终写入前再做一次确定性必填兜底，避免模型漏掉某个必填项时出现局部缺失。
    # 这里只遍历必填集合，非必填字段仍保持空白。
    for key in required_recommendation_fields:
        if str(data.get(key, "")).strip():
            continue
        raw_value = (result.recommendations or {}).get(key) or requirement_extract._recommendation_fallback_value(key)
        value = requirement_extract._normalize_recommendation_value(key, raw_value)
        data[key] = value
        recommended[key] = value
        try:
            confidence = float((result.recommendation_confidence or {}).get(key, 0.35))
        except (TypeError, ValueError):
            confidence = 0.35
        recommendation_confidence[key] = max(0.0, min(1.0, confidence))
    data["industry"] = industry
    data["industry_assessment"] = {
        "selected_mode": industry_selection if industry_selection in {"semiconductor", "battery", "flexible"} else "semiconductor",
        "detected_industry": detected_industry,
        "effective_industry": industry,
        "confidence": max(0.0, min(1.0, float(result.industry_confidence or 0.0))),
        "reason": str(result.industry_reason or "").strip()[:160],
        "assessed_at": _now_str(),
        # 只记录本次请求实际成功使用的模型；没有返回实际标识时保持为空。
        "model": qwen_client.last_used_model() or "",
    }
    if industry == "flexible" and result.flexible_spec_fields:
        existing_spec = dict(data.get("flexible_spec") or {})
        existing_fields = {
            str(field.get("key") or ""): field
            for field in (existing_spec.get("fields") or []) if isinstance(field, dict)
        }
        merged_spec = []
        for proposed in result.flexible_spec_fields:
            field = proposed.model_dump()
            prior = existing_fields.get(field["key"])
            if prior:
                prior_value = str(prior.get("value") or "")
                previous_dynamic_recommendation = previous_recommendations.get(
                    f"flexible_spec.{field['key']}"
                )
                # 旧版本可能给非必填动态字段写过“待人工确认”；如果用户没有改过，
                # 这次解析将其清掉。若模型本次带回了明确文档值，则保留新值。
                if (
                    not field.get("required")
                    and previous_dynamic_recommendation is not None
                    and prior_value.strip() == str(previous_dynamic_recommendation).strip()
                ):
                    prior_value = ""
                field["value"] = prior_value or str(field.get("value") or "")
                field["required"] = bool(prior.get("required", field["required"]))
            elif field["value"]:
                filled.append(f"flexible_spec.{field['key']}")
            elif field.get("key") and field.get("required"):
                field["value"] = "待人工确认"
                field["ai_recommended"] = True
                field["recommendation_confidence"] = 0.35
                recommended[f"flexible_spec.{field['key']}"] = field["value"]
                recommendation_confidence[f"flexible_spec.{field['key']}"] = 0.35
            elif field.get("key"):
                # 非必填动态字段没有文档事实时保持空白，不生成推荐标记。
                field["value"] = ""
                field.pop("ai_recommended", None)
                field.pop("recommendation_confidence", None)
            merged_spec.append(field)
        data["flexible_spec"] = {"generated_by": "qwen_text", "generated_at": _now_str(), "fields": merged_spec}
    ai_filled_fields = list(filled)
    if result.title and not doc.title.strip():
        doc.title = result.title
        data["title"] = result.title
        filled.append("title")
    # 首页创建时已把文件存为项目附件；这里同时挂到 1.1 的“技术规格说明书”区域。
    file_roles = dict(data.get("file_roles") or {})
    current_technical = list(file_roles.get("technical_spec") or [])
    uploaded_attachments = (store.load_meta(project_id) or {}).get("attachments", [])
    file_roles["technical_spec"] = list(dict.fromkeys([
        *current_technical, *uploaded_attachments, *processed_files,
    ]))
    data["file_roles"] = file_roles
    all_filled_fields = list(dict.fromkeys([
        *(previous_extraction.get("all_filled_fields") or previous_extraction.get("filled_fields") or []),
        *ai_filled_fields,
    ]))
    dynamic_required_keys = {
        f"flexible_spec.{field.get('key')}"
        for field in ((data.get("flexible_spec") or {}).get("fields") or [])
        if isinstance(field, dict) and field.get("required") and field.get("key")
    }
    allowed_previous_recommendations = required_recommendation_fields | dynamic_required_keys
    all_recommendations = {
        key: value for key, value in previous_recommendations.items()
        if key in allowed_previous_recommendations
    }
    all_recommendations.update(recommended)
    all_recommendation_confidence = {
        key: value for key, value in dict(previous_extraction.get("recommendation_confidence") or {}).items()
        if key in allowed_previous_recommendations
    }
    all_recommendation_confidence.update(recommendation_confidence)
    all_recommended_fields = list(dict.fromkeys([
        *[key for key in (previous_extraction.get("all_recommended_fields") or previous_extraction.get("recommended_fields") or []) if key in allowed_previous_recommendations],
        *recommended,
    ]))
    data["document_extraction"] = {
        "engine": "qwen_text",
        # 只记录本次请求实际成功使用的模型；没有返回实际标识时保持为空。
        "model": qwen_client.last_used_model() or "",
        "processed_files": processed_files,
        "skipped_files": skipped_files,
        # 仅记录文本模型从技术资料带入的字段；客户/产品历史比对另有独立留痕。
        "filled_fields": ai_filled_fields,
        # 本次与历史 AI 带入字段分开保存：前端可准确展示本次结果，
        # 同时在用户后续再次解析（本次没有新增字段）后仍保留字段来源标识。
        "all_filled_fields": all_filled_fields,
        # 推荐值写入需求草稿供用户继续修改，但来源单独留痕，前端用黄色标识。
        "recommendations": all_recommendations,
        "recommendation_confidence": all_recommendation_confidence,
        "recommended_fields": list(recommended),
        "all_recommended_fields": all_recommended_fields,
        "required_recommended_fields": [
            key for key in recommended
            if key in required_recommendation_fields or key.startswith("flexible_spec.")
        ],
        "history_filled_fields": list(history_fields),
        "industry": data["industry_assessment"],
        "summary": result.summary,
        "open_questions": result.open_questions,
        "extracted_at": _now_str(),
    }
    data["history_comparison"] = history_evidence
    doc.data = data
    doc.history.append(_workflow_event(
        "qwen_document_extracted", user,
        f"已从 {len(processed_files)} 份技术文档提取并补充 {len(ai_filled_fields)} 个 AI 字段；"
        f"另生成 {len(recommended)} 个 AI 推荐默认值，并完成 {len(history_fields)} 个历史比对字段判断。",
    ))
    doc.updated_at = _now_str()
    saved = doc.model_dump()
    store.save_requirement(project_id, saved, author=user.get("username", "system"))
    store.audit(project_id, "workflow:requirement_document_extracted", {
        "model": data["document_extraction"]["model"],
        "processed_files": processed_files,
        "skipped_files": skipped_files,
        "filled_fields": ai_filled_fields,
        "recommended_fields": list(recommended),
        "recommendations": recommended,
        "recommendation_confidence": recommendation_confidence,
        "history_filled_fields": list(history_fields),
        "history_decision": history_evidence["decision"],
        "industry": data["industry_assessment"],
    })
    return {"requirement": saved, "filled_fields": ai_filled_fields, "recommended_fields": list(recommended), "recommendations": recommended, "recommendation_confidence": recommendation_confidence, "required_recommended_fields": data["document_extraction"]["required_recommended_fields"], "history_filled_fields": list(history_fields), "processed_files": processed_files, "skipped_files": skipped_files}


@app.get("/api/requirements")
def list_requirements():
    """真实需求列表；只返回已保存过需求单的项目，不生成演示记录。"""
    return {"items": store.list_requirements()}


@app.get("/api/projects/{project_id}/requirement")
def get_requirement(project_id: str):
    _workflow_project(project_id)
    return {"requirement": store.load_requirement(project_id)}


@app.get("/api/projects/{project_id}/requirement/pdf")
def get_requirement_pdf(project_id: str, download: bool = False, user: dict = Depends(current_user)):
    """生成当前需求单的真实 PDF；确认与审核页共用同一份可追溯表单。"""
    _workflow_project(project_id)
    saved = store.load_requirement(project_id)
    if not saved:
        raise HTTPException(404, "需求单不存在")
    content = requirement_pdf.build_requirement_pdf(saved, store.load_meta(project_id) or {})
    filename = f"requirement_{saved.get('requirement_no') or project_id}.pdf"
    disposition = "attachment" if download else "inline"
    return Response(content=content, media_type="application/pdf", headers={
        "Content-Disposition": f'{disposition}; filename="{filename}"',
        "Cache-Control": "no-store",
    })


@app.get("/api/projects/{project_id}/requirement/precheck")
def precheck_requirement(project_id: str):
    """确认页结构化完整性检查，不发起外部模型请求。"""
    _workflow_project(project_id)
    saved = store.load_requirement(project_id)
    if not saved:
        raise HTTPException(404, "需求单不存在")
    return _requirement_precheck(project_id, RequirementDoc(**saved))


@app.post("/api/projects/{project_id}/requirement/extract-documents")
def extract_requirement_documents(project_id: str, user: dict = Depends(current_user)):
    """从已上传技术文档自动补齐 1.1 草稿；仅文字文档走 QWEN_TEXT_MODEL。"""
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    _workflow_project(project_id)
    saved = store.load_requirement(project_id)
    if not saved:
        raise HTTPException(404, "请先保存需求单")
    doc = RequirementDoc(**saved)
    if doc.status not in {"draft", "rejected"}:
        raise HTTPException(409, "当前需求已进入确认流程，不能自动覆盖草稿")
    prepared = requirement_extract.prepare_documents(store.load_attachments(project_id))
    context_data = doc.data or {}
    context_lines = [
        "【首页与需求表单上下文】",
        f"需求名称：{doc.title or context_data.get('title') or '未填写'}",
        f"需求描述：{context_data.get('description') or '未填写'}",
        f"原始图纸文件：{(store.load_meta(project_id) or {}).get('source_filename') or '未上传'}",
    ]
    context = "\n".join(context_lines)
    # 即使没有可读附件，也用首页描述和图纸文件名完成轻量行业判断；不再静默跳过。
    prepared = requirement_extract.PreparedDocuments(
        text=f"{context}\n\n{prepared.text}".strip(),
        processed_files=prepared.processed_files,
        skipped_files=prepared.skipped_files,
    )
    if not context_data.get("description") and not prepared.processed_files:
        return {
            "skipped": True,
            "reason": "未找到可提取的 TXT、Markdown、CSV、PDF 或 DOCX 技术文档",
            "processed_files": prepared.processed_files,
            "skipped_files": prepared.skipped_files,
        }
    expected_input_revision = _input_revision(project_id)
    expected_industry = str(
        context_data.get("industry_selection") or context_data.get("industry") or "semiconductor"
    )

    def job():
        _assert_input_unchanged(project_id, expected_input_revision)
        tasks.report_progress("正在读取技术资料并调用模型提取需求字段")
        extracted = requirement_extract.extract_requirement_fields(prepared, expected_industry)
        tasks.report_progress("需求字段已返回，正在校验必填项并生成推荐值")
        _assert_input_unchanged(project_id, expected_input_revision)
        # 任务完成前可能有人工保存，重新读取最新草稿，并坚持“只补空字段”。
        latest = store.load_requirement(project_id)
        if not latest:
            raise RuntimeError("需求单在技术文档提取期间被删除")
        latest_doc = RequirementDoc(**latest)
        if latest_doc.status not in {"draft", "rejected"}:
            raise RuntimeError("需求单已进入确认流程，已停止自动补充")
        latest_data = latest_doc.data or {}
        latest_industry = str(
            latest_data.get("industry_selection") or latest_data.get("industry") or "semiconductor"
        )
        if latest_industry != expected_industry:
            raise RuntimeError("任务执行期间行业分类已变化，本次旧分类提取结果未保存；请重新发起。")
        return _apply_requirement_document_extraction(
            project_id, latest_doc, extracted, prepared.processed_files, prepared.skipped_files, user,
        )

    return {"task_id": tasks.submit(
        project_id, "requirement_document_extract", job,
        dedup_key=_task_key(
            "requirement_document_extract", expected_input_revision, expected_industry, prepared.text,
        ),
    )}


@app.post("/api/projects/{project_id}/requirement/ai-check")
def ai_check_requirement(project_id: str, user: dict = Depends(current_user)):
    """用户手动触发的 Qwen 审阅；不重试，结果与模型/时间一同落入需求单留痕。"""
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    meta = _workflow_project(project_id)
    saved = store.load_requirement(project_id)
    if not saved:
        raise HTTPException(404, "需求单不存在")
    doc = RequirementDoc(**saved)
    rule_check = _requirement_precheck(project_id, doc)
    payload = json.dumps(
        {"requirement_no": doc.requirement_no, "title": doc.title, "status": doc.status, "data": doc.data},
        ensure_ascii=False, default=str,
    )
    if len(payload) > 24000:
        payload = payload[:24000] + "\n【表单内容已按检查上下文预算截断】"
    content = [qwen_client.text_block("【待确认的工艺评估需求表单】\n" + payload)]
    source = store.source_path(project_id)
    source_name = meta.get("source_filename") or "source.png"
    if source and source.exists() and source.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
        image_bytes = source.read_bytes()
        if len(image_bytes) <= 5 * 1024 * 1024:
            content.extend([
                qwen_client.text_block(f"【原始工程图：{source_name}】"),
                qwen_client.image_block(image_bytes, source_name, detail="low"),
            ])
        else:
            content.append(qwen_client.text_block("【原始工程图过大，本次仅检查表单字段】"))
    result = qwen_client.run(
        _REQUIREMENT_AI_CHECK_SYSTEM, content, RequirementAiCheckResult, max_tokens=1800,
    )
    check = _normalize_requirement_ai_check(rule_check, result)
    doc.ai_check = check
    doc.history.append(_workflow_event("qwen_requirement_checked", user, check["generated_note"]))
    doc.updated_at = _now_str()
    out = doc.model_dump()
    store.save_requirement(project_id, out, author=user.get("username", "system"))
    store.audit(project_id, "workflow:requirement_qwen_checked", {
        "model": check["model"], "ok": check["ok"], "checked_at": check["checked_at"],
    })
    return {"check": check}


@app.put("/api/projects/{project_id}/requirement/customer-credit")
def update_requirement_customer_credit(
    project_id: str, body: CustomerCreditUpdate, user: dict = Depends(current_user),
):
    """销售经理首次录入客户信用等级；已有等级仅管理员可修改。"""
    _workflow_project(project_id)
    saved = store.load_requirement(project_id)
    if not saved:
        raise HTTPException(404, "请先保存需求单后再录入客户信用等级")
    doc = RequirementDoc(**saved)
    old_value = str((doc.data or {}).get("customer_credit") or "").strip().upper()
    new_value = body.customer_credit
    role = user.get("role")
    if role != "admin":
        if role != "sales_manager":
            raise HTTPException(403, "客户信用等级仅可由销售经理首次录入")
        if old_value:
            raise HTTPException(403, "客户信用等级已录入，仅系统管理员可以修改")
        if doc.status not in {"draft", "rejected"}:
            raise HTTPException(409, "需求已进入确认流程，请联系系统管理员修改客户信用等级")
    doc.data = dict(doc.data or {})
    doc.data["customer_credit"] = new_value
    action = "customer_credit_modified" if old_value else "customer_credit_recorded"
    detail = f"客户信用等级：{old_value or '未填写'} → {new_value}"
    doc.history.append(_workflow_event(action, user, detail))
    doc.updated_at = _now_str()
    out = doc.model_dump()
    store.save_requirement(project_id, out, author=user.get("username", "system"))
    store.audit(project_id, "workflow:customer_credit_updated", {
        "by": user.get("username", "system"), "old": old_value, "new": new_value,
    })
    return {"requirement": out, "customer_credit": new_value}


@app.put("/api/projects/{project_id}/requirement")
def save_requirement(project_id: str, doc: RequirementDoc, user: dict = Depends(current_user)):
    """保存/更新需求单草稿。已进入确认或审核的需求不可被静默改写。"""
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    _workflow_project(project_id)
    current = store.load_requirement(project_id)
    if current and current.get("status") not in ("draft", "rejected"):
        raise HTTPException(409, "需求已提交，不能直接修改；请先退回后再编辑")
    existing_credit = str(((current or {}).get("data") or {}).get("customer_credit") or "").strip().upper()
    incoming_credit = str((doc.data or {}).get("customer_credit") or "").strip().upper()
    if incoming_credit not in {"", "A", "B", "C", "D"}:
        raise HTTPException(422, "客户信用等级只能为 A、B、C 或 D")
    if incoming_credit != existing_credit and user.get("role") != "admin":
        # 销售经理必须走专用接口，确保其无法借整张表单保存改动其它需求字段。
        raise HTTPException(403, "客户信用等级仅可由销售经理首次录入，或由系统管理员修改")
    doc.project_id = project_id
    doc.requirement_no = doc.requirement_no or (current or {}).get("requirement_no") or _requirement_no(project_id)
    doc.created_by = (current or {}).get("created_by") or user.get("username", "system")
    doc.created_at = (current or {}).get("created_at") or _now_str()
    doc.status = (current or {}).get("status") if current else (doc.status if doc.status == "draft" else "draft")
    doc.history = [WorkflowReview(**row) for row in (current or {}).get("history", [])]
    doc.updated_at = _now_str()
    saved = doc.model_dump()
    store.save_requirement(project_id, saved, author=user.get("username", "system"))
    store.audit(project_id, "workflow:requirement_saved", {"requirement_no": doc.requirement_no})
    return {"requirement": saved}


@app.post("/api/projects/{project_id}/requirement/submit-confirmation")
def submit_requirement_confirmation(
    project_id: str, body: WorkflowAction = Body(default=WorkflowAction()),
    user: dict = Depends(current_user),
):
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    _workflow_project(project_id)
    saved = store.load_requirement(project_id)
    if not saved:
        raise HTTPException(404, "请先保存需求单")
    doc = RequirementDoc(**saved)
    if doc.status not in ("draft", "rejected"):
        raise HTTPException(409, "当前需求不在可提交状态")
    # 1.1 的唯一提交门槛由页面星号必填字段负责；不再用另一套完整性清单拦截。
    doc.status = "pending_confirmation"
    doc.history.append(_workflow_event("submit_confirmation", user, body.comment))
    doc.updated_at = _now_str()
    out = doc.model_dump()
    store.save_requirement(project_id, out, author=user.get("username", "system"))
    store.audit(project_id, "workflow:requirement_submitted", {"comment": body.comment})
    return {"requirement": out}


@app.post("/api/projects/{project_id}/requirement/confirm")
def confirm_requirement(
    project_id: str, body: WorkflowAction = Body(default=WorkflowAction()),
    user: dict = Depends(current_user),
):
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    _workflow_project(project_id)
    saved = store.load_requirement(project_id)
    if not saved:
        raise HTTPException(404, "需求单不存在")
    doc = RequirementDoc(**saved)
    if doc.status != "pending_confirmation":
        raise HTTPException(409, "当前需求不在待确认状态")
    doc.status = "pending_review"
    doc.confirmed_by = user.get("username", "system")
    doc.confirmed_at = _now_str()
    doc.confirmation_note = body.comment
    doc.history.append(_workflow_event("confirmed", user, body.comment))
    doc.updated_at = _now_str()
    out = doc.model_dump()
    store.save_requirement(project_id, out, author=user.get("username", "system"))
    store.audit(project_id, "workflow:requirement_confirmed", {"comment": body.comment})
    return {"requirement": out}


@app.post("/api/projects/{project_id}/requirement/return-to-draft")
def return_requirement_to_draft(
    project_id: str, body: WorkflowAction = Body(default=WorkflowAction()),
    user: dict = Depends(current_user),
):
    """确认人退回需求草稿，供创建人补充后再次提交。"""
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    _workflow_project(project_id)
    saved = store.load_requirement(project_id)
    if not saved:
        raise HTTPException(404, "需求单不存在")
    doc = RequirementDoc(**saved)
    if doc.status != "pending_confirmation":
        raise HTTPException(409, "当前需求不在待确认状态")
    doc.status = "draft"
    doc.confirmation_note = body.comment
    doc.history.append(_workflow_event("confirmation_returned", user, body.comment))
    doc.updated_at = _now_str()
    out = doc.model_dump()
    store.save_requirement(project_id, out, author=user.get("username", "system"))
    store.audit(project_id, "workflow:requirement_returned", {"comment": body.comment})
    return {"requirement": out}


@app.post("/api/projects/{project_id}/requirement/review")
def review_requirement(
    project_id: str, body: WorkflowAction, user: dict = Depends(current_user)
):
    _require(user, auth.DIRECTOR_ROLES, "需要工艺技术总监或管理员权限")
    _workflow_project(project_id)
    saved = store.load_requirement(project_id)
    if not saved:
        raise HTTPException(404, "需求单不存在")
    doc = RequirementDoc(**saved)
    if doc.status != "pending_review":
        raise HTTPException(409, "当前需求不在待审核状态")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(400, "decision 必须为 approve 或 reject")
    doc.status = "approved" if body.decision == "approve" else "rejected"
    doc.reviewed_by = user.get("username", "system")
    doc.reviewed_at = _now_str()
    doc.review_note = body.comment
    doc.history.append(_workflow_event(f"review_{body.decision}", user, body.comment))
    doc.updated_at = _now_str()
    out = doc.model_dump()
    store.save_requirement(project_id, out, author=user.get("username", "system"))
    store.audit(project_id, f"workflow:requirement_{body.decision}", {"comment": body.comment})
    return {"requirement": out}


def _report_prerequisite_issues(project_id: str) -> list[str]:
    """正式报告送审前的确定性门禁；AI 不能代替这些人工确认。"""
    issues: list[str] = []
    requirement = store.load_requirement(project_id) or {}
    if requirement.get("status") != "approved":
        issues.append("需求单尚未由工艺技术总监审核通过")

    ir = store.load_ir(project_id) or {}
    if not (ir.get("parts") or []):
        issues.append("2.1 图纸解析尚未形成有效零件 IR")

    material_doc = store.load_material(project_id)
    if not material_doc:
        issues.append("2.2 材料定性尚未完成")
    else:
        material_plan = MaterialPlan(**material_doc)
        if not material_plan.body.selected or not material_plan.body.confirmed:
            issues.append("2.2 主体材料尚未选定并人工确认")
        has_metallization = bool(
            material_plan.metallization.paste
            or material_plan.metallization.layers
            or material_plan.metallization.rationale
        )
        if has_metallization and not material_plan.metallization.confirmed:
            issues.append("2.2 金属化方案已有内容但尚未人工确认")

    manufacturing_doc = store.load_manufacturing(project_id)
    if not manufacturing_doc:
        issues.append("2.3 制造工艺路径尚未完成")
    else:
        manufacturing_plan = ManufacturingPlan(**manufacturing_doc)
        if not manufacturing_plan.path.steps or not manufacturing_plan.path.confirmed:
            issues.append("2.3 工艺路径尚未形成并人工确认")
        if not manufacturing_plan.bom.items or not manufacturing_plan.bom.confirmed:
            issues.append("2.3 工艺 BOM 尚未形成并人工确认")

    cleaning_doc = store.load_cleaning(project_id)
    if not cleaning_doc:
        issues.append("2.4 清洗与洁净度方案尚未完成")
    else:
        cleaning_plan = CleaningPlan(**cleaning_doc)
        if not (cleaning_plan.chemical_steps or cleaning_plan.rinse_steps or cleaning_plan.controls):
            issues.append("2.4 清洗与洁净度方案没有有效内容")
        elif not cleaning_plan.confirmed:
            issues.append("2.4 清洗与洁净度方案尚未人工确认")

    assembly_doc = store.load_assembly(project_id)
    if not assembly_doc:
        issues.append("2.5 组装与检测方案尚未完成")
    else:
        assembly_plan = AssemblyPlan(**assembly_doc)
        if not assembly_plan.assembly.steps or not assembly_plan.assembly.confirmed:
            issues.append("2.5 组装方案尚未形成并人工确认")
        if not assembly_plan.inspection.tests or not assembly_plan.inspection.confirmed:
            issues.append("2.5 检测方案尚未形成并人工确认")

    production_doc = store.load_production(project_id)
    if not production_doc:
        issues.append("2.6 产线匹配与产能评估尚未完成")
    else:
        production_plan = ProductionPlan(**production_doc)
        if not production_plan.requirements or not production_plan.conclusion:
            issues.append("2.6 产线需求或总体结论尚未形成")
        if production_plan.inhouse.matches and not production_plan.inhouse.confirmed:
            issues.append("2.6 自有产线匹配结果尚未人工确认")
        if production_plan.outsourcing.plans and not production_plan.outsourcing.confirmed:
            issues.append("2.6 外协方案尚未人工确认")
        if not production_plan.inhouse.matches and not production_plan.outsourcing.plans:
            issues.append("2.6 尚无自有产线匹配或外协处置方案")

    summary_doc = store.load_summary(project_id)
    if not summary_doc:
        issues.append("技术工艺总结尚未生成")
    else:
        summary_doc_model = SummaryDoc(**summary_doc)
        if not summary_doc_model.conclusion or not summary_doc_model.confirmed:
            issues.append("技术工艺总结尚未形成结论并由经理确认")
    return issues


def _report_content_issues(doc: ProcessReport) -> list[str]:
    issues: list[str] = []
    if not doc.title.strip():
        issues.append("报告标题为空")
    if not doc.conclusion.strip():
        issues.append("报告总结论为空")
    if not doc.evaluation_items:
        issues.append("工艺可行性结论为空")
    for item in doc.evaluation_items:
        if not item.conclusion.strip() or item.status in {"待评估", "需补充"}:
            issues.append(f"评估项“{item.item}”尚未形成可送审结论")
    if not doc.stage_results:
        issues.append("各工艺阶段汇总结论为空")
    for item in doc.stage_results:
        text = item.conclusion.strip()
        if not text or "尚未" in text or "暂无" in text:
            issues.append(f"阶段“{item.stage}”尚未形成有效结论")
    return list(dict.fromkeys(issues))


def _report_source_payload(snapshot: dict) -> dict:
    """报告审核依据只取业务数据，排除会随审计写入变化的项目 meta。"""
    source = snapshot or {}
    return {
        "device_name": source.get("device_name"),
        "ir": source.get("ir") or {},
        "steps": source.get("steps") or {},
        "summary": source.get("summary") or {},
    }


def _report_source_is_current(project_id: str, doc: ProcessReport) -> bool:
    current = summary_svc.aggregate(project_id)
    return _digest_value(_report_source_payload(doc.source_snapshot)) == _digest_value(_report_source_payload(current))


def _new_report(project_id: str, user: dict) -> ProcessReport:
    requirement = store.load_requirement(project_id) or {}
    aggregate = summary_svc.aggregate(project_id)
    summary = aggregate.get("summary") or {}
    device_name = aggregate.get("device_name") or "未命名项目"
    now = _now_str()
    preparer = user.get("display_name") or user.get("username", "system")
    report_no = _report_no(project_id)
    return ProcessReport(
        project_id=project_id,
        report_no=report_no,
        requirement_no=requirement.get("requirement_no", ""),
        title=f"{device_name}工艺评估报告",
        overview=summary.get("overview") or "",
        highlights=summary.get("highlights") or [],
        risks=summary.get("risks") or [],
        conclusion=summary.get("conclusion") or "",
        source_snapshot=aggregate,
        basic_info={
            "report_no": report_no,
            "requirement_no": requirement.get("requirement_no", ""),
            "prepared_by": preparer,
            "prepared_at": now,
        },
        prepared_by=preparer,
        prepared_at=now,
        updated_at=now,
        history=[_workflow_event("report_prepared", user)],
    )


@app.get("/api/projects/{project_id}/process-report")
def get_process_report(project_id: str):
    _workflow_project(project_id)
    return {"report": store.load_process_report(project_id)}


@app.post("/api/projects/{project_id}/process-report/prepare")
def prepare_process_report(project_id: str, user: dict = Depends(current_user)):
    """从已保存的图纸、工艺与总结快照生成报告草稿，不调用模型。"""
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    _workflow_project(project_id)
    current = store.load_process_report(project_id)
    if current and current.get("status") not in ("draft", "rejected"):
        raise HTTPException(409, "报告已送审或发布，不能覆盖；请基于现有版本继续处理")
    doc = _new_report(project_id, user)
    if current:
        prior = ProcessReport(**current)
        doc.version = prior.version + 1
        doc.history = prior.history + [_workflow_event("report_refreshed", user)]
    out = doc.model_dump()
    store.save_process_report(project_id, out, author=user.get("username", "system"))
    store.audit(project_id, "workflow:report_prepared", {"version": doc.version})
    return {"report": out}


@app.put("/api/projects/{project_id}/process-report")
def save_process_report(project_id: str, doc: ProcessReport, user: dict = Depends(current_user)):
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    _workflow_project(project_id)
    current = store.load_process_report(project_id)
    if current and current.get("status") not in ("draft", "rejected"):
        raise HTTPException(409, "报告已送审或发布，不能直接修改")
    requirement_no = (store.load_requirement(project_id) or {}).get("requirement_no", "")
    expected_report_no = _report_no(project_id)
    doc.project_id = project_id
    # 单据号、编制人和编制时间由服务端生成，避免页面手动值覆盖正式留痕。
    # 已有报告优先保留其正式编号；新草稿由服务端生成 RPT 编号。
    current_report_no = (current or {}).get("report_no", "")
    doc.report_no = current_report_no or expected_report_no
    doc.requirement_no = requirement_no
    doc.prepared_by = (current or {}).get("prepared_by") or user.get("display_name") or user.get("username", "system")
    doc.prepared_at = (current or {}).get("prepared_at") or _now_str()
    doc.version = int((current or {}).get("version") or 1)
    # 审核、发布签名以及正式来源快照均由服务端维护，客户端不能伪造。
    doc.reviewed_by = (current or {}).get("reviewed_by")
    doc.reviewed_at = (current or {}).get("reviewed_at")
    doc.review_note = (current or {}).get("review_note", "")
    doc.published_by = (current or {}).get("published_by")
    doc.published_at = (current or {}).get("published_at")
    doc.recipients = ProcessReport(**current).recipients if current else []
    doc.source_snapshot = summary_svc.aggregate(project_id)
    doc.basic_info = {
        **(doc.basic_info or {}),
        "report_no": doc.report_no,
        "requirement_no": doc.requirement_no,
        "prepared_by": doc.prepared_by,
        "prepared_at": doc.prepared_at,
    }
    doc.status = (current or {}).get("status") if current else "draft"
    doc.history = [WorkflowReview(**row) for row in (current or {}).get("history", [])]
    doc.updated_at = _now_str()
    out = doc.model_dump()
    store.save_process_report(project_id, out, author=user.get("username", "system"))
    store.audit(project_id, "workflow:report_saved", {"report_no": doc.report_no})
    return {"report": out}


@app.post("/api/projects/{project_id}/process-report/submit-review")
def submit_process_report_review(
    project_id: str, body: WorkflowAction = Body(default=WorkflowAction()),
    user: dict = Depends(current_user),
):
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    _workflow_project(project_id)
    saved = store.load_process_report(project_id)
    if not saved:
        raise HTTPException(404, "请先汇总并保存评估报告")
    doc = ProcessReport(**saved)
    if doc.status not in ("draft", "rejected"):
        raise HTTPException(409, "当前报告不在可送审状态")
    issues = _report_prerequisite_issues(project_id) + _report_content_issues(doc)
    if issues:
        raise HTTPException(409, "报告暂不可送审：" + "；".join(issues))
    # 送审时重新冻结一次来源快照，保证审核人与发布人看到同一份依据。
    doc.source_snapshot = summary_svc.aggregate(project_id)
    doc.status = "in_review"
    doc.history.append(_workflow_event("report_submitted", user, body.comment))
    doc.updated_at = _now_str()
    out = doc.model_dump()
    store.save_process_report(project_id, out, author=user.get("username", "system"))
    store.audit(project_id, "workflow:report_submitted", {"comment": body.comment})
    return {"report": out}


@app.post("/api/projects/{project_id}/process-report/review")
def review_process_report(project_id: str, body: WorkflowAction, user: dict = Depends(current_user)):
    _require(user, auth.DIRECTOR_ROLES, "需要工艺技术总监或管理员权限")
    _workflow_project(project_id)
    saved = store.load_process_report(project_id)
    if not saved:
        raise HTTPException(404, "评估报告不存在")
    doc = ProcessReport(**saved)
    if doc.status != "in_review":
        raise HTTPException(409, "当前报告不在待审核状态")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(400, "decision 必须为 approve 或 reject")
    if body.decision == "approve":
        issues = _report_prerequisite_issues(project_id) + _report_content_issues(doc)
        if issues:
            raise HTTPException(409, "报告内容仍不满足通过条件：" + "；".join(issues))
        if not _report_source_is_current(project_id, doc):
            raise HTTPException(409, "报告送审后上游工艺数据已变化，请驳回并重新汇总后送审")
    doc.status = "approved" if body.decision == "approve" else "rejected"
    doc.reviewed_by = user.get("username", "system")
    doc.reviewed_at = _now_str()
    doc.review_note = body.comment
    if body.review_items:
        doc.review_items = body.review_items
    if body.review_conclusion:
        doc.review_conclusion = body.review_conclusion
    if body.distribution_scope:
        doc.distribution_scope = body.distribution_scope
    if body.distribution_cc:
        doc.distribution_cc = body.distribution_cc
    doc.history.append(_workflow_event(f"report_review_{body.decision}", user, body.comment))
    doc.updated_at = _now_str()
    out = doc.model_dump()
    store.save_process_report(project_id, out, author=user.get("username", "system"))
    store.audit(project_id, f"workflow:report_{body.decision}", {"comment": body.comment})
    return {"report": out}


@app.put("/api/projects/{project_id}/process-report/distribution")
def update_process_report_distribution(
    project_id: str, body: ReportDistributionSettings, user: dict = Depends(current_user),
):
    """在审核或发布阶段维护分发范围，不改变审核/发布状态。"""
    _require(user, auth.MANAGER_ROLES | auth.DIRECTOR_ROLES, "需要工艺技术经理、工艺技术总监或管理员权限")
    _workflow_project(project_id)
    saved = store.load_process_report(project_id)
    if not saved:
        raise HTTPException(404, "评估报告不存在")
    doc = ProcessReport(**saved)
    if doc.status not in ("draft", "rejected", "in_review", "approved"):
        raise HTTPException(409, "已发布报告不可再维护发布设置")
    doc.distribution_scope = body.distribution_scope.strip()
    doc.distribution_cc = body.distribution_cc.strip()
    doc.history.append(_workflow_event("report_distribution_updated", user, "更新发布范围与抄送对象"))
    doc.updated_at = _now_str()
    out = doc.model_dump()
    store.save_process_report(project_id, out, author=user.get("username", "system"))
    store.audit(project_id, "workflow:report_distribution_updated", {
        "scope": doc.distribution_scope, "cc": doc.distribution_cc,
    })
    return {"report": out}


@app.post("/api/projects/{project_id}/process-report/publish")
def publish_process_report(project_id: str, body: PublishAction, user: dict = Depends(current_user)):
    _require(user, auth.DIRECTOR_ROLES, "需要工艺技术总监或管理员权限")
    _workflow_project(project_id)
    saved = store.load_process_report(project_id)
    if not saved:
        raise HTTPException(404, "评估报告不存在")
    doc = ProcessReport(**saved)
    if doc.status != "approved":
        raise HTTPException(409, "报告须审核通过后才能发布")
    issues = _report_prerequisite_issues(project_id) + _report_content_issues(doc)
    if issues:
        raise HTTPException(409, "报告当前不满足发布条件：" + "；".join(issues))
    if not _report_source_is_current(project_id, doc):
        raise HTTPException(409, "报告审核通过后上游工艺数据已变化，请重新走报告审核流程")
    if not body.recipients:
        raise HTTPException(409, "请至少设置一个正式发布对象")
    doc.status = "published"
    doc.published_by = user.get("username", "system")
    doc.published_at = _now_str()
    doc.recipients = body.recipients
    doc.history.append(_workflow_event("report_published", user, body.comment))
    doc.updated_at = _now_str()
    out = doc.model_dump()
    store.save_process_report(project_id, out, author=user.get("username", "system"))
    store.audit(project_id, "workflow:report_published", {
        "comment": body.comment,
        "recipients": [r.model_dump() for r in body.recipients],
    })
    return {"report": out}


@app.get("/api/projects/{project_id}/process-report/versions")
def list_process_report_versions(project_id: str):
    _workflow_project(project_id)
    return {"versions": store.list_process_report_versions(project_id)}


@app.get("/api/projects/{project_id}/process-report/versions/{version}")
def get_process_report_version(project_id: str, version: int):
    _workflow_project(project_id)
    report = store.get_process_report_version(project_id, version)
    if not report:
        raise HTTPException(404, "已发布报告版本不存在")
    return {"report": report}


@app.post("/api/projects/{project_id}/process-report/new-version")
def create_process_report_version(project_id: str, user: dict = Depends(current_user)):
    """从已发布报告创建下一版草稿，保留既有内容和完整审计链。"""
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    _workflow_project(project_id)
    saved = store.load_process_report(project_id)
    if not saved:
        raise HTTPException(404, "评估报告不存在")
    prior = ProcessReport(**saved)
    if prior.status != "published":
        raise HTTPException(409, "仅已发布报告可创建新版本")
    # 兼容升级前已发布的数据：创建新草稿前先确保旧版进入不可变版本库。
    store.save_process_report(project_id, prior.model_dump(), author=user.get("username", "system"))
    doc = prior.model_copy(deep=True)
    doc.version = prior.version + 1
    doc.status = "draft"
    doc.reviewed_by = None
    doc.reviewed_at = None
    doc.review_note = ""
    doc.review_items = []
    doc.review_conclusion = ""
    doc.published_by = None
    doc.published_at = None
    doc.recipients = []
    doc.history.append(_workflow_event("report_new_version", user, f"基于 V{prior.version} 创建 V{doc.version} 草稿"))
    doc.updated_at = _now_str()
    out = doc.model_dump()
    store.save_process_report(project_id, out, author=user.get("username", "system"))
    store.audit(project_id, "workflow:report_new_version", {"from_version": prior.version, "version": doc.version})
    return {"report": out}


@app.get("/api/projects/{project_id}/workflow")
def get_workflow(project_id: str):
    """一条项目级业务总览，供首页、需求详情和全流程留痕页面共同使用。"""
    meta = _workflow_project(project_id)
    return {
        "project": meta,
        "requirement": store.load_requirement(project_id),
        "report": store.load_process_report(project_id),
        "summary": store.load_summary(project_id),
        "audit": store.list_audit(project_id),
    }


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
    @app.get("/", include_in_schema=False)
    def root_to_home():
        """根地址默认进入首页；2.1 工作台仍通过 /index.html 显式访问。"""
        return RedirectResponse(url="/home.html", status_code=307)

    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


@app.exception_handler(RuntimeError)
def runtime_error_handler(request, exc):  # pragma: no cover
    return JSONResponse(status_code=500, content={"detail": str(exc)})

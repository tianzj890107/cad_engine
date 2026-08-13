"""全局配置。从环境变量 / .env 读取。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# 项目根目录
ROOT_DIR = Path(__file__).resolve().parent.parent

# 运行时数据目录(存放上传原图、IR、生成的几何文件)
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 模型提供商: anthropic(默认，保持原行为) | openai | qwen。
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()

# Anthropic / Claude 原有配置。
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-8")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# OpenAI 的可选独立配置。
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# OpenAI 请求的显式代理/超时配置。未单独设置代理时复用 HTTPS_PROXY。
OPENAI_PROXY_URL = os.getenv("OPENAI_PROXY_URL", os.getenv("HTTPS_PROXY", ""))
OPENAI_TIMEOUT_SECONDS = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "180"))
# 后台 Responses 任务的总等待时长。每次 API 请求仍使用上面的短连接超时，
# 因而不会让代理维持一条数分钟的空闲长连接。
OPENAI_BACKGROUND_TIMEOUT_SECONDS = float(
    os.getenv("OPENAI_BACKGROUND_TIMEOUT_SECONDS", "600")
)
# 团队 API 模式不再按个人费用做单次调用保护；重试次数仍可配置，避免网络异常时无限循环。
OPENAI_MAX_RETRIES = max(0, min(5, int(os.getenv("OPENAI_MAX_RETRIES", "3"))))
# 复杂工程图需要足够空间输出完整 IR；真正的上下文上限由服务端决定。
OPENAI_MAX_OUTPUT_TOKENS = max(4096, int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "32768")))
# 结构化校验失败时自动在同一模型上请求修复，减少人工重复上传图纸。
OPENAI_SCHEMA_REPAIR_RETRIES = max(
    0, min(3, int(os.getenv("OPENAI_SCHEMA_REPAIR_RETRIES", "2")))
)
# 视觉图纸与后续纯文本分析可独立限额/换模型。默认不改变模型选择，先保证兼容。
OPENAI_TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", OPENAI_MODEL)
OPENAI_VISION_MAX_OUTPUT_TOKENS = min(
    OPENAI_MAX_OUTPUT_TOKENS,
    max(4096, int(os.getenv("OPENAI_VISION_MAX_OUTPUT_TOKENS", "32768"))),
)
OPENAI_TEXT_MAX_OUTPUT_TOKENS = min(
    OPENAI_MAX_OUTPUT_TOKENS,
    max(4096, int(os.getenv("OPENAI_TEXT_MAX_OUTPUT_TOKENS", "12000"))),
)
OPENAI_MAX_TOOL_CALLS = max(0, int(os.getenv("OPENAI_MAX_TOOL_CALLS", "1")))
# GPT-5 / o 系列的推理 token 也会计入 max_output_tokens；设为空字符串可完全交给模型默认值。
_DEFAULT_REASONING_EFFORT = (
    "low" if OPENAI_MODEL.lower().startswith(("gpt-5", "o1", "o3", "o4")) else ""
)
OPENAI_REASONING_EFFORT = os.getenv(
    "OPENAI_REASONING_EFFORT", _DEFAULT_REASONING_EFFORT
).strip().lower()

# 阿里云百炼 Qwen（OpenAI Chat Completions 兼容接口）。视觉解析默认使用
# qwen3-vl-plus；它是工程图场景的高质量视觉模型，flash 作为快速故障转移。
# 建议将 QWEN_BASE_URL 配成业务空间专属域名；旧的 dashscope 域名仍可作为默认兼容地址。
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_BASE_URL = os.getenv(
    "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
).rstrip("/")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen3-vl-plus")
# 文本任务（技术文档提取、工艺推荐、汇总等）可单独选择非视觉模型，避免不必要
# 地消耗 VL 模型额度。未配置时仍向后兼容地使用 QWEN_MODEL。
QWEN_TEXT_MODEL = os.getenv("QWEN_TEXT_MODEL", QWEN_MODEL)


def _model_pool(env_name: str, primary: str) -> tuple[str, ...]:
    """Parse a comma-separated ordered fallback pool without duplicate calls."""
    configured = [primary, *os.getenv(env_name, "").split(",")]
    unique: list[str] = []
    for item in configured:
        model = item.strip()
        if model and model not in unique:
            unique.append(model)
    return tuple(unique)


# 视觉与纯文本模型分别维护候选池。首个是上述主模型；遇到模型不可用、限流、
# 网络失败或多轮结构化修复仍失败时，才按顺序切换同能力候选。
QWEN_VISION_MODELS = _model_pool("QWEN_VISION_MODELS", QWEN_MODEL)
QWEN_TEXT_MODELS = _model_pool("QWEN_TEXT_MODELS", QWEN_TEXT_MODEL)
# 型号联网核验使用百炼原生 DashScope 协议，以便取得可追溯的搜索来源。
# 不复用视觉模型：当前视觉模型池不保证支持联网搜索。
QWEN_WEB_SEARCH_MODEL = os.getenv("QWEN_WEB_SEARCH_MODEL", "qwen-plus")
QWEN_WEB_SEARCH_MODELS = _model_pool("QWEN_WEB_SEARCH_MODELS", QWEN_WEB_SEARCH_MODEL)
QWEN_DASHSCOPE_BASE_URL = os.getenv(
    "QWEN_DASHSCOPE_BASE_URL",
    QWEN_BASE_URL.replace(
        "/compatible-mode/v1", "/api/v1/services/aigc/text-generation/generation"
    ) if "/compatible-mode/v1" in QWEN_BASE_URL else
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
).rstrip("/")
# 百炼中国地域通常直连可达；绝不自动继承为 OpenAI 配置的 VPN/Clash 代理。
# 如业务环境确实必须经代理访问，再显式设置 QWEN_PROXY_URL。
QWEN_PROXY_URL = os.getenv("QWEN_PROXY_URL", "")
QWEN_TIMEOUT_SECONDS = max(30.0, float(os.getenv("QWEN_TIMEOUT_SECONDS", "300")))
# 团队 API 不再按个人成本限制重试；保留可配置上限，避免网络抖动造成无限重试。
QWEN_MAX_RETRIES = max(0, min(5, int(os.getenv("QWEN_MAX_RETRIES", "3"))))
QWEN_SCHEMA_REPAIR_RETRIES = max(0, min(3, int(os.getenv("QWEN_SCHEMA_REPAIR_RETRIES", "2"))))
QWEN_MAX_OUTPUT_TOKENS = max(4096, int(os.getenv("QWEN_MAX_OUTPUT_TOKENS", "32768")))
QWEN_VISION_MAX_OUTPUT_TOKENS = min(
    QWEN_MAX_OUTPUT_TOKENS,
    max(4096, int(os.getenv("QWEN_VISION_MAX_OUTPUT_TOKENS", "24000"))),
)
QWEN_TEXT_MAX_OUTPUT_TOKENS = min(
    QWEN_MAX_OUTPUT_TOKENS,
    max(4096, int(os.getenv("QWEN_TEXT_MAX_OUTPUT_TOKENS", "12000"))),
)
# 视觉 JSON 任务默认关闭思考：部分兼容网关在开启思考时只返回 reasoning_content，
# 没有可解析的 JSON 正文；文本推理任务则默认开启思考以提高方案质量。
QWEN_ENABLE_THINKING = os.getenv("QWEN_ENABLE_THINKING", "true").strip().lower() in (
    "1", "true", "yes", "on"
)
QWEN_VISION_ENABLE_THINKING = os.getenv("QWEN_VISION_ENABLE_THINKING", "false").strip().lower() in (
    "1", "true", "yes", "on"
)
QWEN_TEXT_ENABLE_THINKING = os.getenv("QWEN_TEXT_ENABLE_THINKING", "true").strip().lower() in (
    "1", "true", "yes", "on"
)

# 送给模型的佐证附件预算；原始文件仍完整保存，本限制只作用于模型上下文。
LLM_MAX_ATTACHMENTS = max(0, int(os.getenv("LLM_MAX_ATTACHMENTS", "20")))
LLM_MAX_ATTACHMENT_TEXT_CHARS = max(
    0, int(os.getenv("LLM_MAX_ATTACHMENT_TEXT_CHARS", "100000"))
)
LLM_MAX_ATTACHMENT_IMAGE_BYTES = max(
    0, int(os.getenv("LLM_MAX_ATTACHMENT_IMAGE_BYTES", str(20 * 1024 * 1024)))
)
# 技术文档本地抽取预算。它们只防止一次请求把整个磁盘/内存读入模型，
# 不再使用早期个人 API 的 8 文件、12k/文件、50k 总文本限制。
LLM_MAX_DOCUMENTS = max(1, int(os.getenv("LLM_MAX_DOCUMENTS", "32")))
LLM_MAX_DOCUMENT_CHARS = max(0, int(os.getenv("LLM_MAX_DOCUMENT_CHARS", "100000")))
LLM_MAX_TOTAL_DOCUMENT_CHARS = max(0, int(os.getenv("LLM_MAX_TOTAL_DOCUMENT_CHARS", "500000")))
# HTTP 上传上限与模型上下文预算是两回事：原文件仍会完整保存，因此需要单独
# 限制，避免服务把超大 multipart 文件一次性读入内存。默认 50 MiB，部署时可调。
MAX_UPLOAD_BYTES = max(1, int(os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))))


def active_model() -> str:
    if LLM_PROVIDER == "openai":
        return OPENAI_MODEL
    if LLM_PROVIDER == "qwen":
        return QWEN_MODEL
    return CLAUDE_MODEL


def active_text_model() -> str:
    """当前提供商处理无图片请求时实际选用的模型，供健康检查与运维确认。"""
    if LLM_PROVIDER == "openai":
        return OPENAI_TEXT_MODEL
    if LLM_PROVIDER == "qwen":
        return QWEN_TEXT_MODEL
    return CLAUDE_MODEL

# 生成几何的输出格式
GEOMETRY_FORMATS = ("step", "stl")

# ---- 存储后端 ----
# 元数据(项目/IR/几何/图纸结果/审计)存储后端: "file"(默认,JSON 文件) | "sql"
# 二进制文件(原图/附件/STEP/STL/SVG/DXF)始终落在 DATA_DIR/<project_id>/ 下,两后端通用。
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "file")

# SQL 后端连接串。默认本地 SQLite(无需外部服务); 生产可换 Postgres:
#   postgresql+psycopg://user:pwd@host:5432/dbname
DATABASE_URL = os.getenv(
    "DATABASE_URL", f"sqlite:///{(DATA_DIR / 'app.db').as_posix()}"
)

# ---- 鉴权 / RBAC ----
# 默认关闭: 行为与之前完全一致(隐式 system/admin),便于本地开发与现有流程。
# 私有化部署置 AUTH_ENABLED=true 开启登录与角色权限。
def _bool(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


# ---- 二进制对象存储后端 ----
# local(默认,落 DATA_DIR 磁盘) | s3 / minio(私有化集群共享对象存储,需 boto3)
BLOB_BACKEND = os.getenv("BLOB_BACKEND", "local")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")   # MinIO 形如 http://minio:9000
S3_BUCKET = os.getenv("S3_BUCKET", "drawings")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")
S3_REGION = os.getenv("S3_REGION", "")
S3_PREFIX = os.getenv("S3_PREFIX", "")               # 对象 key 前缀(多租户/多环境隔离)

AUTH_ENABLED = _bool(os.getenv("AUTH_ENABLED", "false"))
# 临时演示模式：开启后跳过登录，所有请求使用默认管理员身份。
# 保留完整 RBAC 与登录接口，关闭此开关即可恢复正常登录流程。
AUTH_AUTO_ADMIN = _bool(os.getenv("AUTH_AUTO_ADMIN", "false"))
# 令牌签名密钥(开启鉴权时务必在 .env 设置为随机长串)
AUTH_SECRET = os.getenv("AUTH_SECRET", "dev-insecure-secret-change-me")
TOKEN_TTL_HOURS = int(os.getenv("TOKEN_TTL_HOURS", "12"))
LOGIN_MAX_ATTEMPTS = max(3, int(os.getenv("LOGIN_MAX_ATTEMPTS", "8")))
LOGIN_WINDOW_SECONDS = max(60, int(os.getenv("LOGIN_WINDOW_SECONDS", "600")))
# 进程内任务无法在重启后续跑；单实例可标记旧任务为失败，多副本部署应关闭并使用外部队列。
TASK_RECOVER_ON_START = _bool(os.getenv("TASK_RECOVER_ON_START", "true"))
# 首次启动自动创建的管理员账号(开启鉴权后请尽快改密)
DEFAULT_ADMIN_USER = os.getenv("DEFAULT_ADMIN_USER", "admin")
DEFAULT_ADMIN_PASSWORD = os.getenv("DEFAULT_ADMIN_PASSWORD", "admin123")

# 同源访问无需 CORS。跨域控制台必须显式填写实际域名，以逗号分隔。
CORS_ALLOW_ORIGINS = tuple(
    item.strip() for item in os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if item.strip()
)

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

# Claude 模型: 默认使用最新最强的 Opus 4.8
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-8")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

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
# 令牌签名密钥(开启鉴权时务必在 .env 设置为随机长串)
AUTH_SECRET = os.getenv("AUTH_SECRET", "dev-insecure-secret-change-me")
TOKEN_TTL_HOURS = int(os.getenv("TOKEN_TTL_HOURS", "12"))
# 首次启动自动创建的管理员账号(开启鉴权后请尽快改密)
DEFAULT_ADMIN_USER = os.getenv("DEFAULT_ADMIN_USER", "admin")
DEFAULT_ADMIN_PASSWORD = os.getenv("DEFAULT_ADMIN_PASSWORD", "admin123")

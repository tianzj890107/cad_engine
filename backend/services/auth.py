"""
鉴权与 RBAC(私有化部署底座)。零外部依赖:
  - 口令: pbkdf2_hmac(sha256) 加盐散列,绝不存明文;
  - 令牌: HMAC-SHA256 签名的自包含 token(sub/role/exp),无需服务端会话表;
  - 角色: viewer(只读) / engineer(工艺工程师) / process_manager(工艺技术经理)
    / process_director(工艺技术总监) / admin(全权+用户管理)。

鉴权默认关闭(config.AUTH_ENABLED=false),此时上层注入隐式 system/admin —— 现有
本地流程零改动。开启后由 main.py 的依赖在 /api 层校验令牌并按角色放行。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Optional

from ..config import (
    AUTH_SECRET,
    DEFAULT_ADMIN_PASSWORD,
    DEFAULT_ADMIN_USER,
    TOKEN_TTL_HOURS,
)
from ..time_utils import now_cst_str

ROLES = ("viewer", "engineer", "process_manager", "reviewer", "process_director", "admin")
ROLE_LABEL = {
    "viewer": "只读用户",
    "engineer": "工艺工程师",
    "process_manager": "工艺技术经理",
    "reviewer": "校核人员（历史角色）",
    "process_director": "工艺技术总监",
    "admin": "系统管理员",
}

WRITE_ROLES = {"engineer", "process_manager", "admin"}  # 建模/改参/生成
MANAGER_ROLES = {"process_manager", "admin"}               # 需求与评估报告主责
REVIEW_ROLES = {"reviewer", "process_director", "admin"}  # 通用校核
DIRECTOR_ROLES = {"process_director", "admin"}              # 需求/评估报告终审
ADMIN_ROLES = {"admin"}

# 鉴权关闭时使用的隐式用户(保持旧行为)
SYSTEM_USER = {"username": "system", "role": "admin", "display_name": "系统"}


# --------------------------------------------------------------------------- #
# 口令散列
# --------------------------------------------------------------------------- #
_ITERS = 200_000


def hash_password(pw: str) -> str:
    import os
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, _ITERS)
    return f"pbkdf2${_ITERS}${_b64(salt)}${_b64(dk)}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, iters, salt_b, dk_b = stored.split("$")
        if algo != "pbkdf2":
            return False
        salt = _unb64(salt_b)
        expected = _unb64(dk_b)
        test = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, int(iters))
        return hmac.compare_digest(test, expected)
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 令牌(HMAC 签名,自包含)
# --------------------------------------------------------------------------- #
def make_token(username: str, role: str) -> str:
    payload = {
        "sub": username,
        "role": role,
        "exp": int(time.time()) + TOKEN_TTL_HOURS * 3600,
    }
    body = _b64u(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{_sign(body)}"


def parse_token(token: Optional[str]) -> Optional[dict]:
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        payload = json.loads(_unb64u(body))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < time.time():
        return None
    return payload


def _sign(body: str) -> str:
    mac = hmac.new(AUTH_SECRET.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()
    return _b64u(mac)


# --------------------------------------------------------------------------- #
# 用户记录 / 默认管理员
# --------------------------------------------------------------------------- #
def make_user(username: str, password: str, role: str, display_name: str = "") -> dict:
    if role not in ROLES:
        raise ValueError(f"非法角色: {role}")
    return {
        "username": username,
        "role": role,
        "display_name": display_name or username,
        "password_hash": hash_password(password),
        "created_at": now_cst_str(),
    }


def public_user(u: dict) -> dict:
    """剔除散列等敏感字段后的用户视图。"""
    return {
        "username": u.get("username"),
        "role": u.get("role"),
        "display_name": u.get("display_name") or u.get("username"),
        "requested_role": u.get("requested_role") or u.get("role"),
        "created_at": u.get("created_at"),
        "is_system": bool(u.get("is_system")),
    }


def ensure_default_admin(store) -> None:
    """首次启动且无任何用户时,自动建一个管理员。"""
    if store.list_users():
        return
    u = make_user(DEFAULT_ADMIN_USER, DEFAULT_ADMIN_PASSWORD, "admin", "默认管理员")
    store.save_user(u["username"], u)


def ensure_system_user(store) -> None:
    """为历史导入项目保留一个不可登录的归档归属账号。"""
    if store.get_user("system"):
        return
    u = make_user("system", secrets.token_urlsafe(32), "viewer", "system")
    u["is_system"] = True
    store.save_user("system", u)


def can_edit_project(user: dict, project_meta: dict) -> bool:
    """项目写入权：经理/管理员可管理全部；工程师只能管理本人创建的项目。"""
    role = (user or {}).get("role")
    if role in {"admin", "process_manager"}:
        return True
    if role != "engineer":
        return False
    return bool(user.get("username")) and user.get("username") == (project_meta or {}).get("owner", "system")


# --------------------------------------------------------------------------- #
def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s)


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

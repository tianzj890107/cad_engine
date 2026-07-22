"""
元数据存储后端(可插拔),把"项目/IR/几何结果/图纸结果/审计"等结构化元数据
与具体存储介质解耦:
  - JsonMetaBackend: 沿用原文件方案(data/<id>/*.json)，默认,零依赖,行为不变;
  - SqlMetaBackend : SQLAlchemy,默认 SQLite(无需外部服务)，生产可切 Postgres。

二进制大文件(原图/附件/STEP/STL/SVG/DXF)不在这里,仍由 store.py 落盘到
DATA_DIR/<id>/ 下,两后端通用 —— 即"结构化元数据进 DB,二进制进 blob"。

这是产品化"工程化底座"的第一步:版本、审计、多用户、私有化部署的地基。
"""
from __future__ import annotations

import json
import os
import threading
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..time_utils import now_cst_str

_DOC_KINDS = ("ir", "geometry", "drawings", "versions")


def _now() -> str:
    return now_cst_str()


# --------------------------------------------------------------------------- #
# 接口
# --------------------------------------------------------------------------- #
class MetaBackend:
    def get_meta(self, pid: str) -> Optional[dict]: ...
    def put_meta(self, pid: str, meta: dict) -> None: ...
    def get_doc(self, pid: str, kind: str) -> Optional[dict]: ...
    def put_doc(self, pid: str, kind: str, data: dict) -> None: ...
    def list_metas(self) -> List[dict]: ...
    def append_audit(self, pid: str, entry: dict) -> None: ...
    def list_audit(self, pid: str) -> List[dict]: ...
    def get_user(self, username: str) -> Optional[dict]: ...
    def put_user(self, username: str, data: dict) -> None: ...
    def list_users(self) -> List[dict]: ...


# --------------------------------------------------------------------------- #
# 文件后端(默认)
# --------------------------------------------------------------------------- #
class JsonMetaBackend(MetaBackend):
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        # 文件后端常用于单机部署；请求线程与异步任务会同时读写项目文档。
        # RLock 既避免审计追加丢失，也允许同一高层操作内部复用 _read/_write。
        self._lock = threading.RLock()

    def _dir(self, pid: str) -> Path:
        d = self.data_dir / pid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _read(self, path: Path) -> Optional[dict]:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"本地数据文件损坏，无法读取：{path.name}") from exc

    def _write(self, path: Path, data) -> None:
        """原子替换 JSON，防止进程中断留下半个文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary_name, path)
        finally:
            # os.replace 成功后临时文件已不存在；异常时才需要清理。
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def get_meta(self, pid: str) -> Optional[dict]:
        with self._lock:
            return self._read(self.data_dir / pid / "meta.json")

    def put_meta(self, pid: str, meta: dict) -> None:
        with self._lock:
            self._write(self._dir(pid) / "meta.json", meta)

    def get_doc(self, pid: str, kind: str) -> Optional[dict]:
        with self._lock:
            return self._read(self.data_dir / pid / f"{kind}.json")

    def put_doc(self, pid: str, kind: str, data: dict) -> None:
        with self._lock:
            self._write(self._dir(pid) / f"{kind}.json", data)

    def list_metas(self) -> List[dict]:
        with self._lock:
            out: List[dict] = []
            if not self.data_dir.exists():
                return out
            for d in sorted(self.data_dir.iterdir(), reverse=True):
                if d.is_dir():
                    meta = self._read(d / "meta.json")
                    if meta:
                        out.append(meta)
            return out

    def append_audit(self, pid: str, entry: dict) -> None:
        with self._lock:
            path = self._dir(pid) / "audit.json"
            log = self._read(path) or []
            log.append(entry)
            self._write(path, log)

    def list_audit(self, pid: str) -> List[dict]:
        with self._lock:
            return self._read(self.data_dir / pid / "audit.json") or []

    # 用户(集中存于 data_dir/_auth_users.json: {username: record})
    def _users_path(self) -> Path:
        return self.data_dir / "_auth_users.json"

    def get_user(self, username: str) -> Optional[dict]:
        with self._lock:
            return (self._read(self._users_path()) or {}).get(username)

    def put_user(self, username: str, data: dict) -> None:
        with self._lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            users = self._read(self._users_path()) or {}
            users[username] = data
            self._write(self._users_path(), users)

    def list_users(self) -> List[dict]:
        with self._lock:
            return list((self._read(self._users_path()) or {}).values())


# --------------------------------------------------------------------------- #
# SQL 后端(SQLite 默认 / Postgres 可选)
# --------------------------------------------------------------------------- #
class SqlMetaBackend(MetaBackend):
    def __init__(self, database_url: str):
        from sqlalchemy import (
            JSON, Column, Integer, MetaData, String, Table, Text, create_engine,
        )

        connect_args = {}
        if database_url.startswith("sqlite"):
            connect_args = {"check_same_thread": False}
        self.engine = create_engine(database_url, connect_args=connect_args, future=True)

        md = MetaData()
        self.projects = Table(
            "projects", md,
            Column("id", String(64), primary_key=True),
            Column("meta", JSON, nullable=False),
            Column("created_at", String(32)),
        )
        self.docs = Table(
            "docs", md,
            Column("project_id", String(64), primary_key=True),
            Column("kind", String(32), primary_key=True),
            Column("data", JSON, nullable=False),
            Column("updated_at", String(32)),
        )
        self.audit = Table(
            "audit_log", md,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("project_id", String(64), index=True),
            Column("ts", String(32)),
            Column("action", String(64)),
            Column("detail", Text, nullable=True),
        )
        self.users = Table(
            "users", md,
            Column("username", String(64), primary_key=True),
            Column("data", JSON, nullable=False),
            Column("created_at", String(32)),
        )
        md.create_all(self.engine)
        self._lock = threading.Lock()

    def get_meta(self, pid: str) -> Optional[dict]:
        from sqlalchemy import select
        with self.engine.connect() as c:
            row = c.execute(
                select(self.projects.c.meta).where(self.projects.c.id == pid)
            ).first()
        return dict(row[0]) if row else None

    def put_meta(self, pid: str, meta: dict) -> None:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        from sqlalchemy import insert, update, select
        with self._lock, self.engine.begin() as c:
            exists = c.execute(
                select(self.projects.c.id).where(self.projects.c.id == pid)
            ).first()
            if exists:
                c.execute(
                    update(self.projects).where(self.projects.c.id == pid)
                    .values(meta=meta)
                )
            else:
                c.execute(insert(self.projects).values(
                    id=pid, meta=meta, created_at=meta.get("created_at") or _now()
                ))

    def get_doc(self, pid: str, kind: str) -> Optional[dict]:
        from sqlalchemy import select
        with self.engine.connect() as c:
            row = c.execute(
                select(self.docs.c.data).where(
                    (self.docs.c.project_id == pid) & (self.docs.c.kind == kind)
                )
            ).first()
        return dict(row[0]) if row else None

    def put_doc(self, pid: str, kind: str, data: dict) -> None:
        from sqlalchemy import insert, update, select
        with self._lock, self.engine.begin() as c:
            exists = c.execute(
                select(self.docs.c.project_id).where(
                    (self.docs.c.project_id == pid) & (self.docs.c.kind == kind)
                )
            ).first()
            if exists:
                c.execute(
                    update(self.docs).where(
                        (self.docs.c.project_id == pid) & (self.docs.c.kind == kind)
                    ).values(data=data, updated_at=_now())
                )
            else:
                c.execute(insert(self.docs).values(
                    project_id=pid, kind=kind, data=data, updated_at=_now()
                ))

    def list_metas(self) -> List[dict]:
        from sqlalchemy import select
        with self.engine.connect() as c:
            rows = c.execute(
                select(self.projects.c.meta).order_by(self.projects.c.created_at.desc())
            ).all()
        return [dict(r[0]) for r in rows]

    def append_audit(self, pid: str, entry: dict) -> None:
        from sqlalchemy import insert
        with self._lock, self.engine.begin() as c:
            c.execute(insert(self.audit).values(
                project_id=pid,
                ts=entry.get("ts") or _now(),
                action=entry.get("action", ""),
                detail=json.dumps(entry.get("detail"), ensure_ascii=False)
                if entry.get("detail") is not None else None,
            ))

    def list_audit(self, pid: str) -> List[dict]:
        from sqlalchemy import select
        with self.engine.connect() as c:
            rows = c.execute(
                select(self.audit.c.ts, self.audit.c.action, self.audit.c.detail)
                .where(self.audit.c.project_id == pid)
                .order_by(self.audit.c.id)
            ).all()
        out = []
        for ts, action, detail in rows:
            out.append({
                "ts": ts, "action": action,
                "detail": json.loads(detail) if detail else None,
            })
        return out

    def get_user(self, username: str) -> Optional[dict]:
        from sqlalchemy import select
        with self.engine.connect() as c:
            row = c.execute(
                select(self.users.c.data).where(self.users.c.username == username)
            ).first()
        return dict(row[0]) if row else None

    def put_user(self, username: str, data: dict) -> None:
        from sqlalchemy import insert, select, update
        with self._lock, self.engine.begin() as c:
            exists = c.execute(
                select(self.users.c.username).where(self.users.c.username == username)
            ).first()
            if exists:
                c.execute(update(self.users).where(
                    self.users.c.username == username).values(data=data))
            else:
                c.execute(insert(self.users).values(
                    username=username, data=data,
                    created_at=data.get("created_at") or _now()))

    def list_users(self) -> List[dict]:
        from sqlalchemy import select
        with self.engine.connect() as c:
            rows = c.execute(select(self.users.c.data)).all()
        return [dict(r[0]) for r in rows]


# --------------------------------------------------------------------------- #
# 工厂(单例)
# --------------------------------------------------------------------------- #
_backend: Optional[MetaBackend] = None


def get_backend() -> MetaBackend:
    global _backend
    if _backend is not None:
        return _backend
    from ..config import DATA_DIR, DATABASE_URL, STORAGE_BACKEND

    if STORAGE_BACKEND.lower() == "sql":
        _backend = SqlMetaBackend(DATABASE_URL)
    else:
        _backend = JsonMetaBackend(DATA_DIR)
    return _backend

"""DA 数据库(SQLite)的连接、建库与通用访问helper。

对应 docs/数据架构设计_DA.md 的落地实现:
  - 结构化数据 -> SQLite 单文件库(默认 DATA_DIR/da.db)
  - 图纸/模型/文档 -> 文件夹(见 kb_library.py),库里只存路径与哈希

为什么用标准库 sqlite3 而不是 SQLAlchemy:
  DA 的表是宽而稳定的业务表,DDL 已由 da_schema.sql 明确定义;直接用 sqlite3 可以
  让"文档里的建表语句"与"库里的建表语句"是同一份文本,不存在 ORM 映射漂移。
  既有的 SqlMetaBackend(通用文档存储)不受影响,两者互不替代。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from ..time_utils import now_cst_str

SCHEMA_FILE = Path(__file__).with_name("da_schema.sql")
SCHEMA_VERSION = "2"

# 已建库的实例上,CREATE TABLE IF NOT EXISTS 不会补新增的列,需要显式 ALTER。
# 每项为 (表, 列, 列定义)。只做加列,不改类型、不删列 —— 那类变更须走单独的迁移脚本。
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # v2: 需求单记录选用的行业模板(半导体/电池/电器)。
    ("src_requirement", "industry", "TEXT NOT NULL DEFAULT 'semiconductor'"),
)

_local = threading.local()
_init_lock = threading.RLock()
_initialized: set[str] = set()


def now() -> str:
    return now_cst_str()


def db_path() -> Path:
    from ..config import DA_DB_PATH

    return Path(DA_DB_PATH)


# --------------------------------------------------------------------------- #
# 连接
# --------------------------------------------------------------------------- #
def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    # 外键必须显式开启,否则 SQLite 只把 REFERENCES 当注释,DA 的回指约束会形同虚设。
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL 让读写并发(FastAPI 请求线程 + 后台任务线程)不互相阻塞。
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    """返回一个新连接(调用方负责关闭)。常规读写请用 get_conn()。"""
    target = Path(path) if path else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, check_same_thread=False)
    _configure(conn)
    return conn


def get_conn() -> sqlite3.Connection:
    """线程内复用的连接。sqlite3 连接非线程安全,故按线程各持一个。"""
    target = str(db_path())
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == target:
        return conn
    init_db(Path(target))
    conn = connect(Path(target))
    _local.conn = conn
    _local.path = target
    return conn


def close_conn() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
        _local.path = None


# --------------------------------------------------------------------------- #
# 建库
# --------------------------------------------------------------------------- #
def init_db(path: Optional[Path] = None, *, force: bool = False) -> Path:
    """执行 da_schema.sql 建表(幂等)。返回库文件路径。"""
    target = Path(path) if path else db_path()
    key = str(target)
    with _init_lock:
        if key in _initialized and not force:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        ddl = SCHEMA_FILE.read_text(encoding="utf-8")
        conn = connect(target)
        try:
            conn.executescript(ddl)
            _add_missing_columns(conn)
            conn.execute(
                "INSERT INTO schema_meta(key, value, updated_at) VALUES('schema_version', ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (SCHEMA_VERSION, now()),
            )
            conn.commit()
        finally:
            conn.close()
        _initialized.add(key)
    return target


def _add_missing_columns(conn: sqlite3.Connection) -> list[str]:
    """给已存在的表补上新版本新增的列(幂等)。返回本次实际新增的列。

    SQLite 的 ALTER TABLE ADD COLUMN 不支持带 CHECK 约束,因此迁移出来的列
    只带默认值;新建库仍由 da_schema.sql 附上完整 CHECK。约束差异由业务层
    (industry_templates.normalize)兜住,不会写进非法值。
    """
    added: list[str] = []
    for table, column, definition in _ADDED_COLUMNS:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column in columns:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        added.append(f"{table}.{column}")
    return added


def table_names(conn: Optional[sqlite3.Connection] = None) -> list[str]:
    c = conn or get_conn()
    rows = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]


# --------------------------------------------------------------------------- #
# 通用访问 helper
# --------------------------------------------------------------------------- #
def query(sql: str, params: Sequence[Any] = (), *, conn: Optional[sqlite3.Connection] = None) -> list[dict]:
    c = conn or get_conn()
    return [dict(r) for r in c.execute(sql, tuple(params)).fetchall()]


def query_one(sql: str, params: Sequence[Any] = (), *, conn: Optional[sqlite3.Connection] = None) -> Optional[dict]:
    rows = query(sql, params, conn=conn)
    return rows[0] if rows else None


def execute(sql: str, params: Sequence[Any] = (), *, conn: Optional[sqlite3.Connection] = None) -> sqlite3.Cursor:
    c = conn or get_conn()
    cur = c.execute(sql, tuple(params))
    c.commit()
    return cur


def execute_many(sql: str, seq: Iterable[Sequence[Any]], *, conn: Optional[sqlite3.Connection] = None) -> None:
    c = conn or get_conn()
    c.executemany(sql, [tuple(p) for p in seq])
    c.commit()


def upsert(
    table: str,
    row: dict,
    *,
    keys: Sequence[str],
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """按主键/唯一键 upsert。JSON 值(list/dict)自动序列化。"""
    data = {k: _encode(v) for k, v in row.items() if v is not None or k in keys}
    cols = list(data)
    placeholders = ", ".join("?" for _ in cols)
    updates = [c for c in cols if c not in keys]
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    if updates:
        assignments = ", ".join(f"{c}=excluded.{c}" for c in updates)
        sql += f" ON CONFLICT({', '.join(keys)}) DO UPDATE SET {assignments}"
    else:
        sql += f" ON CONFLICT({', '.join(keys)}) DO NOTHING"
    execute(sql, [data[c] for c in cols], conn=conn)


def insert(table: str, row: dict, *, conn: Optional[sqlite3.Connection] = None) -> int:
    data = {k: _encode(v) for k, v in row.items() if v is not None}
    cols = list(data)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' for _ in cols)})"
    )
    return int(execute(sql, [data[c] for c in cols], conn=conn).lastrowid or 0)


def _encode(value: Any) -> Any:
    if isinstance(value, (list, dict, tuple)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return 1 if value else 0
    return value


def decode_json(value: Any, default: Any = None) -> Any:
    """读出 JSON 列。库里存的是文本,业务层拿到的应是 list/dict。"""
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default

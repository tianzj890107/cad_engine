"""
轻量级进程内异步任务队列(私有化/单机优先,零外部依赖)。

为什么不是 Celery/Redis: 本平台主打私有化/内网,尽量少引入需要独立运维的中间件。
这里用标准库 ThreadPoolExecutor 把耗时操作(Claude 解析/校验/拆解、CAD 几何/2D/3D
导入)从请求线程剥离,任务状态/进度/结果持久化到 store(可跨请求轮询、可追溯)。
接口刻意做成"提交 fn -> 拿 task_id -> 轮询"这种与具体执行器无关的形态,日后要扩到
多机,只需把 _executor 换成分布式 broker,上层与前端不动。

注意: OCCT/CadQuery 并非线程安全,故所有 CAD 任务经 _cad_lock 串行化;Claude 调用
是 IO 密集,可并发。
"""
from __future__ import annotations

import os
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from ..storage import store

_WORKERS = int(os.getenv("TASK_WORKERS", "4"))
_executor = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="task")
_cad_lock = threading.Lock()  # 串行化 CAD(OCCT 非线程安全)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def submit(project_id: str, kind: str, fn: Callable[[], dict], cad: bool = False) -> str:
    """提交一个任务(fn 为零参可调用,返回 JSON 可序列化的结果),立即返回 task_id。"""
    task_id = uuid.uuid4().hex[:12]
    store.save_task(project_id, {
        "task_id": task_id,
        "project_id": project_id,
        "kind": kind,
        "status": "queued",
        "progress": "排队中",
        "created_at": _now(),
        "started_at": None,
        "finished_at": None,
        "result": None,
        "error": None,
    })
    _executor.submit(_run, project_id, task_id, fn, cad)
    return task_id


def _run(project_id: str, task_id: str, fn: Callable[[], dict], cad: bool) -> None:
    _update(project_id, task_id, status="running", progress="处理中", started_at=_now())
    try:
        if cad:
            with _cad_lock:
                result = fn()
        else:
            result = fn()
        _update(project_id, task_id, status="succeeded", progress="完成",
                finished_at=_now(), result=result)
    except Exception as e:  # noqa: BLE001 — 任务内任何异常都转成失败态
        traceback.print_exc()
        _update(project_id, task_id, status="failed", progress="失败",
                finished_at=_now(), error=str(e))


def _update(project_id: str, task_id: str, **fields) -> None:
    rec = store.get_task(project_id, task_id)
    if not rec:
        return
    rec.update(fields)
    store.save_task(project_id, rec)

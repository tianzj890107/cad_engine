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
import traceback
import uuid
from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from ..storage import store
from ..time_utils import now_cst_str
from . import ai_governance

try:
    _WORKERS = max(1, min(32, int(os.getenv("TASK_WORKERS", "4"))))
except (TypeError, ValueError):
    _WORKERS = 4
_executor = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="task")
_cad_lock = threading.Lock()  # 串行化 CAD(OCCT 非线程安全)
_submit_lock = threading.Lock()  # 防止双击/多标签页重复提交同一种昂贵任务
_MAX_ERROR_CHARS = 4000
_CURRENT_TASK: ContextVar[tuple[str, str, str, int] | None] = ContextVar("current_task", default=None)

_SOP_NAMES = {
    "parse": ("图纸解析 SOP", 3), "verify": ("图纸校核 SOP", 3),
    "model_lookup": ("型号核验 SOP", 3), "decompose": ("零件拆解 SOP", 3),
    "process": ("工艺路线 SOP", 3), "cost": ("零件成本 SOP", 3),
    "material_recommend": ("材料方案 SOP", 3),
    "manufacturing_recommend": ("制造方案 SOP", 3),
    "cleaning_recommend": ("洁净方案 SOP", 3),
    "assembly_recommend": ("组装检测 SOP", 3),
    "production_recommend": ("产能评估 SOP", 3),
    "summary_recommend": ("工艺汇总 SOP", 3),
    "costest_recommend": ("成本测算 SOP", 3),
    "pricing_recommend": ("定价 SOP", 3),
    "negotiation_recommend": ("商务谈判 SOP", 3),
    "pricenego_recommend": ("价格协商 SOP", 3),
    "approval_recommend": ("审批定级 SOP", 3),
    "requirement_document_extract": ("需求字段提取 SOP", 3),
}

_TASK_START_PROGRESS = {
    "parse": "准备图纸与附件",
    "verify": "准备校核输入",
    "model_lookup": "准备型号核验输入",
    "decompose": "准备零件拆解输入",
    "process": "准备工艺路线输入",
    "cost": "准备成本分析输入",
    "material_recommend": "准备材料方案输入",
    "manufacturing_recommend": "准备制造方案输入",
    "cleaning_recommend": "准备洁净方案输入",
    "assembly_recommend": "准备组装检测输入",
    "production_recommend": "准备产能评估输入",
    "summary_recommend": "准备工艺汇总输入",
    "costest_recommend": "准备成本测算输入",
    "pricing_recommend": "准备定价输入",
    "negotiation_recommend": "准备谈判策略输入",
    "pricenego_recommend": "准备价格协商输入",
    "approval_recommend": "准备审批建议输入",
    "requirement_document_extract": "准备需求文档输入",
}


def _now() -> str:
    return now_cst_str()


def submit(
    project_id: str,
    kind: str,
    fn: Callable[[], dict],
    cad: bool = False,
    *,
    dedup_key: str | None = None,
) -> str:
    """提交一个任务(fn 为零参可调用,返回 JSON 可序列化的结果),立即返回 task_id。"""
    with _submit_lock:
        # 只有业务输入完全相同的任务才复用。零件级任务、不同批量或不同补充
        # 说明不能仅因 kind 相同就被错误合并。
        effective_key = dedup_key or kind
        for task in store.list_tasks(project_id):
            task_key = task.get("dedup_key") or task.get("kind")
            if task_key == effective_key and task.get("status") in {"queued", "running"}:
                return str(task["task_id"])

        task_id = uuid.uuid4().hex[:12]
        sop_name, sop_total = _SOP_NAMES.get(kind, ("任务处理 SOP", 3))
        store.save_task(project_id, {
            "task_id": task_id,
            "project_id": project_id,
            "kind": kind,
            "dedup_key": effective_key,
            "status": "queued",
            "progress": _TASK_START_PROGRESS.get(kind, "排队中"),
            "sop_name": sop_name,
            "sop_step": 0,
            "sop_total": sop_total,
            "created_at": _now(),
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
        })
        _executor.submit(_run, project_id, task_id, kind, fn, cad)
        return task_id


def _run(project_id: str, task_id: str, kind: str, fn: Callable[[], dict], cad: bool) -> None:
    token = _CURRENT_TASK.set((project_id, task_id, kind, 0))
    sop_name, sop_total = _SOP_NAMES.get(kind, ("任务处理 SOP", 3))
    _update(project_id, task_id, status="running",
            progress=_TASK_START_PROGRESS.get(kind, "开始处理"), sop_name=sop_name,
            sop_step=0, sop_total=sop_total, started_at=_now())
    try:
        if cad:
            with _cad_lock:
                result = fn()
        else:
            result = fn()
        if kind in ai_governance.AI_TASK_KINDS:
            store.save_ai_result_metadata(
                project_id, task_id, kind, ai_governance.metadata(kind, result)
            )
        _update(project_id, task_id, status="succeeded", progress="完成",
                sop_step=sop_total,
                finished_at=_now(), result=result)
    except Exception as e:  # noqa: BLE001 — 任务内任何异常都转成失败态
        traceback.print_exc()
        _update(project_id, task_id, status="failed", progress="失败",
                finished_at=_now(), error=_safe_error(e))
    finally:
        _CURRENT_TASK.reset(token)


def _update(project_id: str, task_id: str, **fields) -> None:
    store.update_task(project_id, task_id, **fields)


def report_progress(progress: str) -> None:
    """更新当前异步任务的真实阶段；任务函数内可直接调用。"""
    current = _CURRENT_TASK.get()
    if not current or not progress:
        return
    project_id, task_id, kind, step = current
    next_step = min(step + 1, _SOP_NAMES.get(kind, ("任务处理 SOP", 3))[1] - 1)
    _CURRENT_TASK.set((project_id, task_id, kind, next_step))
    _update(project_id, task_id, progress=str(progress)[:240], sop_step=next_step)


def _safe_error(exc: Exception) -> str:
    """任务状态会被持久化并展示，限制异常文本，避免响应/模型输出无限膨胀。"""
    text = str(exc).strip() or type(exc).__name__
    return text[:_MAX_ERROR_CHARS] + ("…（错误详情已截断）" if len(text) > _MAX_ERROR_CHARS else "")


def recover_interrupted_tasks() -> int:
    """标记服务重启前遗留的内存任务，避免轮询端永久显示“处理中”。

    当前执行器是进程内 ThreadPoolExecutor，进程退出后无法恢复函数闭包；因此将
    queued/running 任务明确置为失败，比保留一个永远不会完成的状态更可追溯也更安全。
    """
    recovered = 0
    for project in store.list_projects():
        project_id = project.get("project_id")
        if not project_id:
            continue
        for task in store.list_tasks(project_id):
            if task.get("status") not in {"queued", "running"}:
                continue
            task.update({
                "status": "failed",
                "progress": "服务重启中断",
                "finished_at": _now(),
                "error": "服务在任务执行期间重启，任务未完成；请确认输入后重新发起。",
            })
            store.save_task(project_id, task)
            recovered += 1
    return recovered

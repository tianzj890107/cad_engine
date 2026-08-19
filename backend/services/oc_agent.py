"""2.1 图纸解析的 Agent —— 直接使用 open-claude 的 Conversation 与工具循环。

不复制、不改写 open-claude：本模块只做三件事
  1. 把仓库内的 open-claude 加进 sys.path，实例化它的 Conversation（每个项目一个会话）；
  2. 在它的工具表上追加本平台的工具（项目状态、零件清单、待澄清、请求解析），
     并在 open_claude.repl 的 execute_tool 调用点前置一层分派；
  3. 把 Conversation 的流式输出转成 SSE 事件，喂给前端对话框。

关于工具分派的实现方式：open-claude 的 Conversation._execute_pending_tools 里，
非 Agent / 非 MCP 的工具统一走模块级的 execute_tool()。这里用一个包装函数替换
open_claude.repl.execute_tool，命中平台工具就自己处理，否则原样转交。
这样权限检查、PreToolUse/PostToolUse 钩子、流式与压缩逻辑全部保持 open-claude 原生行为，
代价是一处受控的模块级替换 —— 相比 fork 一份 repl.py，这是更小的耦合面。

文件系统只读：与 open-claude 自带的 web 桥一致，Write / Edit / Bash 既从模型可见的
工具表里摘掉，也在执行层由 OC_READONLY_FS 拦住（子代理同样受限）。CLI 不受影响。

**唯一的例外是 `UpdatePartParameters`**：它不碰文件系统，只按白名单改 IR 里的零件
参数，逻辑与老的 workbench-chat 共用 `services.part_edit`，每次改写都留版本快照和
审计。文件系统只读这条约束没有放宽。

工作目录被限定在该项目的数据目录（DATA_DIR/<project_id>），因此 Read/Glob/Grep
看到的是这份图纸、附件与生成的几何文件，而不是整个代码仓库。
"""
from __future__ import annotations

import contextvars
import json
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from ..config import DATA_DIR, ROOT_DIR
from ..storage import store

OPEN_CLAUDE_DIR = Path(os.getenv("OPEN_CLAUDE_DIR", ROOT_DIR / "open-claude"))

# 与 open-claude web 桥一致：网页会话不得改动本地文件或执行命令。
READONLY_DISABLED_TOOLS = ("Write", "Edit", "Bash")

_lock = threading.RLock()
_sessions: dict[str, "ProjectAgent"] = {}
_patched = False


class AgentUnavailable(RuntimeError):
    """open-claude 不可用（未安装、缺密钥、导入失败）时抛出，由接口层转成可读提示。"""


# 发起本轮对话的用户。Agent 跑在 worker 线程里，拿不到请求上下文，但改零件参数
# 必须记「是谁改的」—— 审计里写 system 等于没记。接口层在 stream_sse 时注入。
_ACTOR: contextvars.ContextVar[str] = contextvars.ContextVar("oc_agent_actor",
                                                            default="system")


def current_actor() -> str:
    return _ACTOR.get() or "system"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# --------------------------------------------------------------------------- #
# open-claude 装载
# --------------------------------------------------------------------------- #
def _ensure_path() -> None:
    if not OPEN_CLAUDE_DIR.is_dir():
        raise AgentUnavailable(f"未找到 open-claude 目录：{OPEN_CLAUDE_DIR}")
    path = str(OPEN_CLAUDE_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)


def _import_open_claude():
    _ensure_path()
    try:
        from open_claude import repl as oc_repl                     # noqa: WPS433
        from open_claude.api import stream_message                  # noqa: WPS433
        from open_claude.config import AVAILABLE_MODELS             # noqa: WPS433
        from open_claude.profile import load_profile                # noqa: WPS433
        from open_claude.sessions import SessionStore               # noqa: WPS433
        from open_claude.skills.registry import get_registry        # noqa: WPS433
    except Exception as exc:                                        # pragma: no cover - 环境相关
        raise AgentUnavailable(f"open-claude 导入失败：{exc}") from exc
    return {
        "repl": oc_repl, "stream_message": stream_message,
        "AVAILABLE_MODELS": AVAILABLE_MODELS, "load_profile": load_profile,
        "SessionStore": SessionStore, "get_registry": get_registry,
    }


def available() -> tuple[bool, str]:
    """探测 Agent 是否可用。用于页面加载时给出明确原因而不是静默失败。"""
    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        return (False, "未配置 ANTHROPIC_API_KEY，Agent 无法启动")
    try:
        _import_open_claude()
    except AgentUnavailable as exc:
        return (False, str(exc))
    return (True, "")


# --------------------------------------------------------------------------- #
# 平台工具
# --------------------------------------------------------------------------- #
PLATFORM_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "GetProjectState",
        "description": "读取当前工艺评估项目的状态：设备名称、设计意图、是否已解析、"
                       "零件与标准件数量、平均置信度、已上传的图纸与技术文档清单。"
                       "回答任何与本项目有关的问题前都应先调用它。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "ListParts",
        "description": "列出图纸解析得到的零件清单（编号、名称、材料、数量、置信度、所属总成）。"
                       "尚未解析时返回空列表。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "GetPartDetail",
        "description": "读取单个零件的完整信息：特征列表与尺寸、材料、公差、溯源说明、拆解建议。",
        "input_schema": {
            "type": "object",
            "properties": {"part_id": {"type": "string", "description": "零件编号，如 P-001"}},
            "required": ["part_id"],
        },
    },
    {
        "name": "GetOpenQuestions",
        "description": "读取本次解析的待澄清问题（缺尺寸/公差/材料等需人工确认的项）。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "LookupComponentLibrary",
        "description": "在企业零部件库里检索某个零件有没有现成件：命中的零部件编码与名称、"
                       "匹配度、可复用/可改制/未匹配的判定、用于比对的查询条件（尺寸、材料），"
                       "以及全部候选件与它们的差异说明。"
                       "回答「这个零件库里有没有」「能不能复用现成件」「要不要新开模」时先调用它；"
                       "零部件编码必须来自这里，不得自行编造。不传 part_id 则返回整份检索汇总。",
        "input_schema": {
            "type": "object",
            "properties": {"part_id": {"type": "string",
                                       "description": "零件编号，如 P-001；留空则返回全部零件的汇总"}},
            "required": [],
        },
    },
    {
        "name": "LookupProcessLibrary",
        "description": "在企业工艺库里检索某个零件能用哪条工艺路线：命中的路线模板及其工序序列"
                       "（工序编号、标准准备工时、单件工时模型、默认设备类）、路线未覆盖但特征"
                       "需要的补充工序、以及库内根本没有对应工序的空白项。"
                       "回答「这个零件怎么做」「要几道工序」「有没有现成路线」时先调用它，"
                       "答案里的工序编号必须来自这里，不得自行编造。",
        "input_schema": {
            "type": "object",
            "properties": {"part_id": {"type": "string", "description": "零件编号，如 P-001"}},
            "required": ["part_id"],
        },
    },
    {
        "name": "LookupCostLibrary",
        "description": "在企业成本库里检索某个零件的计价依据：物料现价（含 price_id、价格类型、"
                       "生效日期）、人工/折旧/能耗/制造费用等费率、良率与毛利等计价系数，"
                       "以及库内缺失需要询价或补录的项。"
                       "回答「这个零件多少钱」「材料什么价」「费率多少」时先调用它；"
                       "库里有的价格必须原值引用，库里没有的要明确说是估算。",
        "input_schema": {
            "type": "object",
            "properties": {
                "part_id": {"type": "string", "description": "零件编号，如 P-001"},
                "quantity": {"type": "integer", "description": "核算批量，默认 1"},
            },
            "required": ["part_id"],
        },
    },
    {
        "name": "UpdatePartParameters",
        "description": "修改某个零件的参数。**这是唯一会写业务数据的工具**，只在用户"
                       "明确要求「改成/设为/调整为」并给出了具体目标值时调用。\n"
                       "可改：name、quantity、material_spec，以及该零件**已有**特征的数值字段"
                       "（先用 GetPartDetail 确认特征序号与字段名）。\n"
                       "不可改：不能新增或删除特征、不能改特征 type、一次只能改一个零件。\n"
                       "用户只是问「该怎么改」、没给具体数值、或你需要靠常识补全尺寸时，"
                       "**不要调用本工具** —— 先把缺的信息问清楚。改写会留版本快照与审计，"
                       "但错误的改写仍然要人工回滚，代价不小。\n"
                       "改了特征尺寸后返回 requires_regeneration=true，平台会在界面上重生成"
                       "该零件的 3D 与工程图；如实说明是平台在做，不要自称是你生成的。",
        "input_schema": {
            "type": "object",
            "properties": {
                "part_id": {"type": "string", "description": "零件编号，如 P-001"},
                "name": {"type": "string", "description": "新的零件名称；不改就不要传"},
                "quantity": {"type": "integer", "description": "新的数量；不改就不要传"},
                "material_spec": {"type": "string",
                                  "description": "新的材料牌号，如 Q235 / 6061-T6；不改就不要传"},
                "feature_updates": {
                    "type": "array",
                    "description": "要改的特征数值，逐项给出",
                    "items": {
                        "type": "object",
                        "properties": {
                            "feature_index": {"type": "integer",
                                              "description": "特征序号，从 0 开始"},
                            "field": {"type": "string",
                                      "description": "字段名，如 thickness / diameter / length"},
                            "value": {"type": "number", "description": "新数值（mm）"},
                        },
                        "required": ["feature_index", "field", "value"],
                    },
                },
                "reason": {"type": "string", "description": "本次修改的依据，会写进版本说明"},
            },
            "required": ["part_id"],
        },
    },
    {
        "name": "RequestParse",
        "description": "请求平台开始（或重新）解析当前图纸。用户说「开始解析」「重新解析」"
                       "「帮我拆解这张图」等意图时调用。"
                       "注意：本工具只发出请求，真正的解析由平台既有流水线执行并在界面上回显进度；"
                       "调用后不要虚构解析结果，等平台把结果写回后再基于 ListParts 回答。",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string", "description": "触发解析的简短理由"}},
            "required": [],
        },
    },
]
PLATFORM_TOOL_NAMES = {schema["name"] for schema in PLATFORM_TOOL_SCHEMAS}

# 前端据此把工具调用翻译成界面动作（开始解析、改完刷新 IR…）。
# refresh-ir 由前端**在本轮结束时**执行 —— 这个事件是在工具跑之前发出的，
# 当场刷新会读到改写前的旧值。
UI_ACTION_TOOLS = {"RequestParse": "parse", "UpdatePartParameters": "refresh-ir"}


def _project_of(cwd: str) -> str:
    """工作目录就是项目数据目录，因此项目号可由 cwd 反推。"""
    return Path(cwd).name


def _ir_of(project_id: str) -> Optional[dict]:
    return store.load_ir(project_id)


def _run_platform_tool(name: str, params: dict, cwd: str) -> str:
    project_id = _project_of(cwd)
    if name == "GetProjectState":
        return json.dumps(_project_state(project_id), ensure_ascii=False, indent=2)
    if name == "ListParts":
        return json.dumps(_parts(project_id), ensure_ascii=False, indent=2)
    if name == "GetPartDetail":
        return json.dumps(_part_detail(project_id, str(params.get("part_id") or "")),
                          ensure_ascii=False, indent=2)
    if name == "GetOpenQuestions":
        return json.dumps(_open_questions(project_id), ensure_ascii=False, indent=2)
    if name == "LookupComponentLibrary":
        return json.dumps(_component_library(project_id, str(params.get("part_id") or "")),
                          ensure_ascii=False, indent=2)
    if name == "LookupProcessLibrary":
        return json.dumps(_process_library(project_id, str(params.get("part_id") or "")),
                          ensure_ascii=False, indent=2)
    if name == "LookupCostLibrary":
        return json.dumps(
            _cost_library(project_id, str(params.get("part_id") or ""),
                          int(params.get("quantity") or 1)),
            ensure_ascii=False, indent=2)
    if name == "UpdatePartParameters":
        return json.dumps(_update_part(project_id, params), ensure_ascii=False, indent=2)
    if name == "RequestParse":
        return json.dumps({
            "requested": True,
            "reason": str(params.get("reason") or "")[:200],
            "note": "已向界面发出解析请求。解析由平台流水线异步执行，"
                    "进度与结果会显示在对话与右侧工作区；请勿自行编造零件或尺寸。",
        }, ensure_ascii=False)
    return f"未知的平台工具：{name}"


def _project_state(project_id: str) -> dict:
    meta = store.load_meta(project_id) or {}
    ir = _ir_of(project_id)
    parts = (ir or {}).get("parts") or []
    confidences = [float(part.get("confidence") or 0) for part in parts]
    attachments = [name for name, _ in store.load_attachments(project_id)]
    return {
        "project_id": project_id,
        "project_name": meta.get("name") or "",
        "source_drawing": meta.get("source_filename") or "",
        "supplementary_note": store.get_note(project_id),
        "attachments": attachments,
        "parsed": bool(ir),
        "device_name": (ir or {}).get("device_name") or "",
        "design_intent": (ir or {}).get("design_intent") or "",
        "overall_dims": (ir or {}).get("overall_dims") or "",
        "assembly_notes": (ir or {}).get("assembly_notes") or "",
        "part_count": len(parts),
        "assembly_count": len((ir or {}).get("assemblies") or []),
        "standard_part_count": len((ir or {}).get("standard_parts") or []),
        "open_question_count": len((ir or {}).get("open_questions") or []),
        "average_confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
    }


def _parts(project_id: str) -> list[dict]:
    ir = _ir_of(project_id) or {}
    out = []
    for part in ir.get("parts") or []:
        material = part.get("material") or {}
        out.append({
            "part_id": part.get("part_id"), "name": part.get("name"),
            "parent_id": part.get("parent_id"), "role": part.get("role"),
            "material": material.get("spec") if isinstance(material, dict) else material,
            "quantity": part.get("quantity"), "confidence": part.get("confidence"),
            "feature_count": len(part.get("features") or []),
        })
    return out


def _part_detail(project_id: str, part_id: str) -> dict:
    ir = _ir_of(project_id) or {}
    for part in ir.get("parts") or []:
        if str(part.get("part_id")) == part_id:
            return part
    return {"error": f"未找到零件 {part_id}",
            "available": [part.get("part_id") for part in ir.get("parts") or []]}


def _open_questions(project_id: str) -> list[dict]:
    ir = _ir_of(project_id) or {}
    return list(ir.get("open_questions") or [])


def _match_of(project_id: str, part_id: str) -> Optional[dict]:
    report = store.load_component_match(project_id) or {}
    return next((item for item in report.get("items") or []
                 if item.get("part_id") == part_id), None)


def _update_part(project_id: str, params: dict) -> dict:
    """按白名单改零件参数，存版本快照并写审计。

    校验逻辑不在这里 —— 与老的 workbench-chat 共用 `part_edit`，两条会话入口
    必须是同一套规则。这里只负责：取 IR → 交给它改 → 落盘 → 如实回报改了什么。

    失败一律返回 `{"error": …}` 而不是抛异常：工具报错应该变成模型看得懂的
    工具结果，让它把原因转述给用户，而不是中断整轮对话。
    """
    from . import part_edit                                # 延迟导入避免环依赖
    from ..models.ir import DesignIR

    part_id = str(params.get("part_id") or "").strip()
    if not part_id:
        return {"error": "必须指定 part_id"}

    blocked = part_edit.blocks_feature_edit(store.load_meta(project_id))
    if blocked:
        return {"error": blocked, "applied": False}

    raw = _ir_of(project_id)
    if not raw or not raw.get("parts"):
        return {"error": "尚未完成图纸解析，没有可修改的零件"}
    try:
        ir = DesignIR.model_validate(raw)
    except Exception as exc:                                # pragma: no cover - IR 损坏
        return {"error": f"当前 IR 无法解析，未做任何修改：{exc}"}

    part = next((item for item in ir.parts if str(item.part_id) == part_id), None)
    if part is None:
        return {"error": f"未找到零件 {part_id}",
                "available": [item.part_id for item in ir.parts]}

    try:
        changes, geometry_changed = part_edit.apply_edit(
            part,
            name=params.get("name"),
            quantity=params.get("quantity"),
            material_spec=params.get("material_spec"),
            feature_updates=params.get("feature_updates") or (),
        )
    except part_edit.PartEditError as exc:
        return {"error": str(exc), "applied": False}

    if not changes:
        return {"applied": False, "part_id": part_id, "changes": [],
                "note": "给出的值与当前值相同，未做修改。"}

    actor = current_actor()
    reason = str(params.get("reason") or "").strip()[:200]
    note = (f"Agent 对话修改 {part_id}：" + "、".join(c["field"] for c in changes)
            + (f"（依据：{reason}）" if reason else ""))
    store.save_ir(project_id, ir.model_dump(), stage="agent_edited",
                  author=actor, note=note)
    store.audit(project_id, "agent_part_edit", {
        "by": actor, "part_id": part_id, "changes": changes, "reason": reason,
    })
    return {
        "applied": True,
        "part_id": part_id,
        "changes": changes,
        "requires_regeneration": geometry_changed,
        "note": ("已改写并留下可回溯版本。"
                 + ("特征尺寸变了，平台会在界面上重新生成该零件的 3D 与工程图；"
                    "如实说明是平台在做，不要自称是你生成的。"
                    if geometry_changed else "")),
    }


def _component_library(project_id: str, part_id: str) -> dict:
    """现查零部件库。与工艺/成本库一样不读缓存 —— 库里新录了件就该看到。

    不传 part_id 时返回整份汇总：用户常问的是"整台设备有多少能复用"，
    逐件问一遍既慢又容易漏。
    """
    from . import component_match                                # 延迟导入避免环依赖

    ir = _ir_of(project_id)
    if not ir or not ir.get("parts"):
        return {"error": "尚未完成图纸解析，零部件库无可检索的零件清单"}
    if not part_id:
        report = component_match.match_project(project_id, ir)
        component_match.save_report(project_id, report)
        return report
    part = _part_detail(project_id, part_id)
    if part.get("error"):
        return part
    return component_match.match_part(part)


def _process_library(project_id: str, part_id: str) -> dict:
    """现查工艺库，不读缓存 —— 库改了就该看到新结果。"""
    from . import process_lookup                                  # 延迟导入避免环依赖

    part = _part_detail(project_id, part_id)
    if part.get("error"):
        return part
    report = process_lookup.lookup_part(part, match=_match_of(project_id, part_id))
    process_lookup.save_report(project_id, part_id, report)
    return report


def _cost_library(project_id: str, part_id: str, quantity: int) -> dict:
    from . import cost_lookup                                     # 延迟导入避免环依赖

    part = _part_detail(project_id, part_id)
    if part.get("error"):
        return part
    report = cost_lookup.lookup_part(
        part, quantity=max(1, quantity), match=_match_of(project_id, part_id),
        process_report=store.load_process_lookup(project_id, part_id),
    )
    cost_lookup.save_report(project_id, part_id, report)
    return report


def _install_tool_dispatch(oc_repl) -> None:
    """在 open-claude 的 execute_tool 调用点前置平台工具分派（幂等）。"""
    global _patched
    if _patched:
        return
    original = oc_repl.execute_tool

    def dispatch(name: str, params: dict, cwd: str) -> str:
        if name in PLATFORM_TOOL_NAMES:
            try:
                return _run_platform_tool(name, params or {}, cwd)
            except Exception as exc:                     # 工具异常不应打断整轮对话
                return f"平台工具 {name} 执行失败：{exc}"
        return original(name, params, cwd)

    dispatch.__wrapped__ = original                      # 便于排查与还原
    oc_repl.execute_tool = dispatch
    _patched = True


SYSTEM_APPENDIX = """
你现在是「AI 工艺评估平台」2.1 图纸解析步骤的工艺助手，服务对象是工艺工程师。

工作范围：
- 依据平台里已有的图纸、技术文档与解析结果（IR）回答问题，先用 GetProjectState 取状态；
- 讨论零件结构、材料、公差、可制造性与待澄清风险；
- 用户要求开始/重新解析时调用 RequestParse，由平台执行，你不要自行编造解析结果。

硬性要求：
- 尺寸、材料、公差、数量只能来自工具返回的数据。资料里没有的，明确说"图纸未给出"并列为待确认，
  绝不按行业惯例填补数值 —— 这些数字会进入工艺方案与报价。
- 回答用中文，简洁、结论先行；涉及数值时标注它来自哪个零件或哪份资料。
- 当前会话的文件系统是只读的，你不能修改项目文件。
"""


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #
class ProjectAgent:
    """一个项目一个 open-claude Conversation。"""

    def __init__(self, project_id: str, profile_name: Optional[str] = None):
        modules = _import_open_claude()
        self._oc = modules
        oc_repl = modules["repl"]
        _install_tool_dispatch(oc_repl)
        # 只读兜底同时覆盖子代理；CLI 从不设置该标志，因此不受影响。
        os.environ.setdefault("OC_READONLY_FS", "1")

        self.project_id = project_id
        self.cwd = str(_project_workdir(project_id))
        profile = modules["load_profile"](profile_name, self.cwd) if profile_name else None
        self.conv = oc_repl.Conversation(self.cwd, permission_mode="always_allow", profile=profile)
        # 没有终端可以回答 y/n，任何询问都直接放行（只读限制仍然生效）。
        self.conv.permissions._prompt_user = lambda *args, **kwargs: (True, "")

        for tool_name in READONLY_DISABLED_TOOLS:
            if tool_name not in self.conv.profile.disabled_tools:
                self.conv.profile.disabled_tools.append(tool_name)
        self._rebuild_tools()
        self.conv.system_prompt = f"{self.conv.system_prompt}\n{SYSTEM_APPENDIX}"
        self.lock = threading.Lock()

    def _rebuild_tools(self) -> None:
        self.conv._build_tool_schemas()
        existing = {schema.get("name") for schema in self.conv.tool_schemas}
        for schema in PLATFORM_TOOL_SCHEMAS:
            if schema["name"] not in existing:
                self.conv.tool_schemas.append(schema)

    # -- 元信息（供左侧对话设置工具栏展示）--------------------------------- #
    def meta(self) -> dict:
        conv = self.conv
        return {
            "model": conv.model,
            "profile": conv.profile.name,
            "cwd": self.cwd,
            "project_id": self.project_id,
            "models": [{"id": item["id"], "label": item["label"]}
                       for item in self._oc["AVAILABLE_MODELS"]],
            "tools": [{"name": schema.get("name", ""),
                       "description": (schema.get("description", "") or "")[:140],
                       "platform": schema.get("name") in PLATFORM_TOOL_NAMES}
                      for schema in conv.tool_schemas],
            "skills": [{"name": skill.name, "description": skill.description or ""}
                       for skill in self._oc["get_registry"]().get_user_invocable()],
            "message_count": len(conv.messages),
        }

    def reset(self) -> None:
        with self.lock:
            self.conv.messages.clear()
            self.conv.session = self._oc["SessionStore"](self.cwd)
            self.conv.cost_tracker.__init__()

    def set_model(self, model_id: str) -> None:
        with self.lock:
            self.conv.model = model_id
            os.environ["CLAUDE_MODEL"] = model_id

    # -- 全局配置的落地 ---------------------------------------------------- #
    def apply(self, params: dict, *, rebuild_client: bool = False) -> None:
        """把全局配置（llm_settings）落到这个已经建好的会话上。

        取值范围由 llm_settings 统一夹住，这里只负责赋值 —— 校验放两处就会分叉。
        """
        with self.lock:
            profile = self.conv.profile
            if params.get("agent_model"):
                self.conv.model = str(params["agent_model"])
            profile.temperature = params.get("temperature")
            profile.thinking = bool(params.get("thinking"))
            if params.get("thinking_budget"):
                profile.thinking_budget = int(params["thinking_budget"])
            profile.max_tokens = params.get("max_tokens")
            if params.get("max_iterations"):
                profile.max_iterations = int(params["max_iterations"])
            if rebuild_client:
                # 换密钥必须重建 client：open-claude 在构造时就把 key 读进去了。
                self.conv.client = self._oc["repl"].create_client()

    # -- 一轮对话（镜像 Conversation.run_turn，改为产出事件）--------------- #
    def stream_turn(self, text: str, emit: Callable[[dict], None]) -> None:
        with self.lock:
            conv = self.conv
            conv.add_user_message(text)
            try:
                for _ in range(max(1, conv.profile.max_iterations)):
                    conv._maybe_compact()
                    stop_reason = self._stream_once(conv, emit)
                    if stop_reason != "tool_use":
                        break
                    # 复用 open-claude 自己的执行路径（权限、钩子、Agent/MCP 分派）。
                    conv._execute_pending_tools()
                    last = conv.messages[-1] if conv.messages else None
                    if last and last.get("role") == "user" and isinstance(last.get("content"), list):
                        for block in last["content"]:
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                emit({
                                    "type": "tool_result",
                                    "tool_use_id": block.get("tool_use_id", ""),
                                    "content": _stringify(block.get("content", "")),
                                    "is_error": bool(block.get("is_error", False)),
                                })
            except Exception as exc:                     # pragma: no cover - 依赖外部服务
                traceback.print_exc()
                emit({"type": "error", "error": str(exc)})
            finally:
                emit({
                    "type": "done", "model": conv.model,
                    "cost": round(getattr(conv.cost_tracker, "total_cost_usd", 0.0), 5),
                })

    def _stream_once(self, conv, emit: Callable[[dict], None]) -> str:
        text_buffer: list[str] = []
        tool_uses: list[dict] = []
        stop_reason = "end_turn"

        stream = self._oc["stream_message"](
            conv.client, conv.messages, conv.system_prompt,
            model=conv.model, tools=conv.tool_schemas,
            max_tokens=conv.profile.max_tokens,
            temperature=conv.profile.temperature,
            thinking_budget=conv.profile.thinking_budget if conv.profile.thinking else None,
        )
        for event in stream:
            kind = event["type"]
            if kind == "text_delta":
                text_buffer.append(event["text"])
                emit({"type": "text", "text": event["text"]})
            elif kind == "tool_use_end":
                tool_uses.append({"type": "tool_use", "id": event["id"],
                                  "name": event["name"], "input": event["input"]})
                emit({"type": "tool_use", "id": event["id"], "name": event["name"],
                      "input": event["input"],
                      "ui_action": UI_ACTION_TOOLS.get(event["name"], "")})
            elif kind == "message_end":
                stop_reason = event.get("stop_reason", "end_turn")
                usage = event.get("usage", {})
                conv.cost_tracker.add_usage(
                    conv.model,
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    cache_read=usage.get("cache_read_input_tokens", 0),
                    cache_creation=usage.get("cache_creation_input_tokens", 0),
                )
            elif kind == "error":
                emit({"type": "error", "error": event["error"]})
                stop_reason = "error"
                break

        content: list[dict] = []
        full_text = "".join(text_buffer)
        if full_text:
            content.append({"type": "text", "text": full_text})
        content.extend(tool_uses)
        if content:
            message = {"role": "assistant", "content": content}
            conv.messages.append(message)
            conv.session.append_message(message)
        return stop_reason


def _project_workdir(project_id: str) -> Path:
    """Agent 的工作目录：该项目的数据目录（图纸、附件、几何文件都在这里）。"""
    directory = Path(DATA_DIR) / project_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _stringify(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("content") or ""))
            else:
                parts.append(str(block))
        return "\n".join(part for part in parts if part)
    return json.dumps(content, ensure_ascii=False, default=str)


# --------------------------------------------------------------------------- #
# 会话池
# --------------------------------------------------------------------------- #
def get_agent(project_id: str) -> ProjectAgent:
    with _lock:
        agent = _sessions.get(project_id)
        if agent is None:
            agent = ProjectAgent(project_id)
            # 新会话立刻套用全局配置，否则「在别处改过的设置」要等重启才生效。
            _apply_global(agent)
            _sessions[project_id] = agent
        return agent


def _apply_global(agent: ProjectAgent) -> None:
    try:
        from . import llm_settings

        agent.apply(llm_settings.agent_params())
    except Exception:                                   # pragma: no cover - 依赖环境
        pass


def apply_settings(params: dict, *, rebuild_client: bool = False) -> None:
    """把全局配置推给**所有已经建好的会话**。

    没有这一步，改设置就只对之后新建的会话生效，用户会看到"我明明改了"。
    """
    with _lock:
        agents = list(_sessions.values())
    for agent in agents:
        try:
            agent.apply(params, rebuild_client=rebuild_client)
        except Exception:                               # pragma: no cover - 单个会话失败不影响其他
            continue


def drop_agent(project_id: str) -> None:
    with _lock:
        _sessions.pop(project_id, None)


def sse(event: dict) -> str:
    """把一个事件编码成 SSE 帧。"""
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def stream_sse(project_id: str, message: str, actor: str = "system") -> Iterator[str]:
    """把一轮对话产出为 SSE 帧序列。事件通过队列跨线程传递，保证边生成边下发。"""
    import queue

    agent = get_agent(project_id)
    channel: "queue.Queue[Optional[dict]]" = queue.Queue()

    def worker() -> None:
        # ContextVar 不会自动跨线程传播，必须在 worker 内部再设一次，
        # 否则 UpdatePartParameters 的审计只会记到 system。
        _ACTOR.set(actor or "system")
        try:
            agent.stream_turn(message, channel.put)
        except Exception as exc:                         # pragma: no cover - 依赖外部服务
            channel.put({"type": "error", "error": str(exc)})
            channel.put({"type": "done", "model": "", "cost": 0})
        finally:
            channel.put(None)

    thread = threading.Thread(target=worker, name=f"oc-agent-{project_id}", daemon=True)
    thread.start()
    while True:
        event = channel.get()
        if event is None:
            break
        yield sse(event)

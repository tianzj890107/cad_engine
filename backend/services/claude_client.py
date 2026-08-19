"""共享的 Anthropic / Claude 客户端封装。"""
from __future__ import annotations

import contextvars

import base64
import functools
from typing import Any, Dict, List, Type, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from ..config import ANTHROPIC_API_KEY, CLAUDE_MODEL

T = TypeVar("T", bound=BaseModel)
_MAX_TOKENS = 16000
_TOOL_NAME = "emit_design_ir"


def _route(vision: bool = False) -> dict:
    """当前该用哪个 Anthropic 模型、哪把 Key。延迟导入避免循环依赖。

    **不能缓存**：缓存住就等于把模型冻在进程启动时的那一个，界面上改了不生效。
    """
    from . import llm_settings

    return llm_settings.resolve(vision=vision)


def get_client(vision: bool = False) -> anthropic.Anthropic:
    """按「模型设置」里的 Key 建客户端，走 Anthropic 官方网关。

    以前固定读 .env 的 ANTHROPIC_API_KEY 和 CLAUDE_MODEL，界面上改了也不生效。
    """
    route = _route(vision)
    api_key = route["api_key"] or ANTHROPIC_API_KEY
    if not api_key:
        raise RuntimeError("未配置 Anthropic 的 API Key，请在「模型设置」中填写。")
    return anthropic.Anthropic(api_key=api_key, base_url=route["base_url"])


def _media_type_for(filename: str) -> str:
    f = filename.lower()
    if f.endswith(".png"):
        return "image/png"
    if f.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if f.endswith(".webp"):
        return "image/webp"
    if f.endswith(".gif"):
        return "image/gif"
    return "image/png"


def image_block(image_bytes: bytes, filename: str, detail: str | None = None) -> Dict[str, Any]:
    # detail 仅供 OpenAI 客户端使用；保留此可选参数以让调用方跨 provider 一致。
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    return {"type": "image", "source": {"type": "base64", "media_type": _media_type_for(filename), "data": b64}}


def text_block(text: str) -> Dict[str, Any]:
    return {"type": "text", "text": text}


_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
_TEXT_EXTS = (".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml", ".ini")


def attachment_blocks(attachments) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    for name, data in attachments or []:
        lower = (name or "").lower()
        if lower.endswith(_IMAGE_EXTS):
            blocks.extend([text_block(f"【附件图片: {name}】"), image_block(data, name)])
        elif lower.endswith(_TEXT_EXTS):
            try:
                text = data.decode("utf-8", errors="replace")
            except Exception:
                text = "(无法解码为文本)"
            blocks.append(text_block(f"【附件: {name}】\n{text}"))
        else:
            blocks.append(text_block(f"【附件 {name} 类型暂不支持解析，已忽略】"))
    return blocks


def _collect_web_sources(resp, acc: Dict[str, str]) -> None:
    for block in getattr(resp, "content", None) or []:
        if getattr(block, "type", None) == "web_search_tool_result":
            content = getattr(block, "content", None)
            if not isinstance(content, list):
                continue
            for result in content:
                url = getattr(result, "url", None)
                if url and url not in acc:
                    acc[url] = getattr(result, "title", None) or url


def _build_tool(output_model: Type[T]) -> Dict[str, Any]:
    return {
        "name": _TOOL_NAME,
        "description": "输出解析/拆解得到的结构化设计意图 IR。必须调用本工具一次，把结果作为工具输入提交。",
        "input_schema": output_model.model_json_schema(),
    }


def _extract_tool_input(response, tool_name: str):
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return block.input
    return None


WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}
_MAX_PAUSE_RESUMES = 4


def _tuning() -> Dict[str, Any]:
    """读「模型设置」里的推理参数。"""
    try:
        from . import llm_settings

        return llm_settings.inference_params()
    except Exception:                                   # pragma: no cover - 依赖导入顺序
        return {}


# 实际用掉的模型，供审计留痕。审计必须记"真正跑过的那个"，不能拿配置值顶替。
_last_used_model: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "claude_last_used_model", default=None
)


def last_used_model() -> str | None:
    return _last_used_model.get()


def _create_until_done(client, system, tools, messages, max_tokens, sources=None,
                       model: str | None = None, thinking: bool = True):
    model = model or CLAUDE_MODEL
    think = {"type": "adaptive"} if thinking else {"type": "disabled"}
    _last_used_model.set(model)
    resp = client.messages.create(
        model=model, max_tokens=max_tokens, thinking=think,
        system=system, tools=tools, messages=messages,
        extra_body={"output_config": {"effort": "high"}},
    )
    if sources is not None:
        _collect_web_sources(resp, sources)
    resumes = 0
    while resp.stop_reason == "pause_turn" and resumes < _MAX_PAUSE_RESUMES:
        messages = messages + [{"role": "assistant", "content": resp.content}]
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, thinking=think,
            system=system, tools=tools, messages=messages,
            extra_body={"output_config": {"effort": "high"}},
        )
        if sources is not None:
            _collect_web_sources(resp, sources)
        resumes += 1
    return resp


def run(system_prompt: str, user_content: List[Dict[str, Any]], output_model: Type[T],
        extra_tools: List[Dict[str, Any]] | None = None, max_tokens: int | None = None,
        sources_out: list | None = None) -> T:
    vision = any(block.get("type") == "image" for block in user_content)
    route = _route(vision)
    client = get_client(vision)
    tools = [_build_tool(output_model)] + list(extra_tools or [])
    # 「模型设置」里的最大 token / 是否思考在这里落地，与 OpenAI 兼容路径一致。
    tuning = _tuning()
    max_tokens = max_tokens or tuning.get("max_tokens") or _MAX_TOKENS
    system = [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
    acc: Dict[str, str] | None = {} if sources_out is not None else None

    def finish(model: T) -> T:
        if sources_out is not None and acc:
            sources_out.extend({"title": acc[url], "url": url} for url in acc)
        return model

    response = _create_until_done(
        client, system, tools, [{"role": "user", "content": user_content}], max_tokens, acc,
        model=route["model"], thinking=bool(tuning.get("thinking", True)))
    data = _extract_tool_input(response, _TOOL_NAME)
    error = None
    if data is not None:
        try:
            return finish(output_model.model_validate(data))
        except ValidationError as exc:
            error = str(exc)
    else:
        error = "未调用 emit_design_ir 工具。"

    repair = user_content + [{"type": "text", "text": (
        f"上一次未能得到合规结果({error})。请务必调用 {_TOOL_NAME} 工具，"
        "严格按其 input_schema 输出，确保所有必填字段齐全、类型正确。"
    )}]
    # 修复重试必须用同一个模型与同样的思考设置 —— 漏传就会悄悄退回 .env 里的
    # CLAUDE_MODEL，等于"第一次用你选的模型，重试时换成别的"。
    response = _create_until_done(
        client, system, tools, [{"role": "user", "content": repair}], max_tokens, acc,
        model=route["model"], thinking=bool(tuning.get("thinking", True)))
    data = _extract_tool_input(response, _TOOL_NAME)
    if data is None:
        raise RuntimeError(
            f"{route['model']} 未能产出结构化结果 (stop_reason={response.stop_reason})。")
    try:
        return finish(output_model.model_validate(data))
    except ValidationError as exc:
        # 报出实际跑的那个模型，不要写死"Claude" —— 这条路径也可能跑的是别的
        # Anthropic 模型，用户看到的名字必须和「模型设置」里选的对得上。
        raise RuntimeError(f"{route['model']} 输出未通过 schema 校验: {exc}") from exc


def parse_image_to_model(image_bytes: bytes, filename: str, system_prompt: str,
                         user_instruction: str, output_model: Type[T]) -> T:
    return run(system_prompt, [image_block(image_bytes, filename), text_block(user_instruction)], output_model)


def complete_to_model(system_prompt: str, user_prompt: str, output_model: Type[T],
                      extra_tools: List[Dict[str, Any]] | None = None,
                      max_tokens: int | None = None) -> T:
    return run(system_prompt, [text_block(user_prompt)], output_model,
               extra_tools=extra_tools, max_tokens=max_tokens)

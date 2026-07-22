"""共享的 Anthropic / Claude 客户端封装。"""
from __future__ import annotations

import base64
import functools
from typing import Any, Dict, List, Type, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from ..config import ANTHROPIC_API_KEY, CLAUDE_MODEL

T = TypeVar("T", bound=BaseModel)
_MAX_TOKENS = 16000
_TOOL_NAME = "emit_design_ir"


@functools.lru_cache(maxsize=1)
def get_client() -> anthropic.Anthropic:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "未配置 ANTHROPIC_API_KEY。请复制 .env.example 为 .env 并填入你的 API Key。"
        )
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


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


def _create_until_done(client, system, tools, messages, max_tokens, sources=None):
    resp = client.messages.create(
        model=CLAUDE_MODEL, max_tokens=max_tokens, thinking={"type": "adaptive"},
        system=system, tools=tools, messages=messages,
        extra_body={"output_config": {"effort": "high"}},
    )
    if sources is not None:
        _collect_web_sources(resp, sources)
    resumes = 0
    while resp.stop_reason == "pause_turn" and resumes < _MAX_PAUSE_RESUMES:
        messages = messages + [{"role": "assistant", "content": resp.content}]
        resp = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=max_tokens, thinking={"type": "adaptive"},
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
    client = get_client()
    tools = [_build_tool(output_model)] + list(extra_tools or [])
    max_tokens = max_tokens or _MAX_TOKENS
    system = [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
    acc: Dict[str, str] | None = {} if sources_out is not None else None

    def finish(model: T) -> T:
        if sources_out is not None and acc:
            sources_out.extend({"title": acc[url], "url": url} for url in acc)
        return model

    response = _create_until_done(client, system, tools, [{"role": "user", "content": user_content}], max_tokens, acc)
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
    response = _create_until_done(client, system, tools, [{"role": "user", "content": repair}], max_tokens, acc)
    data = _extract_tool_input(response, _TOOL_NAME)
    if data is None:
        raise RuntimeError(f"Claude 未能产出结构化结果 (stop_reason={response.stop_reason})。")
    try:
        return finish(output_model.model_validate(data))
    except ValidationError as exc:
        raise RuntimeError(f"Claude 输出未通过 schema 校验: {exc}") from exc


def parse_image_to_model(image_bytes: bytes, filename: str, system_prompt: str,
                         user_instruction: str, output_model: Type[T]) -> T:
    return run(system_prompt, [image_block(image_bytes, filename), text_block(user_instruction)], output_model)


def complete_to_model(system_prompt: str, user_prompt: str, output_model: Type[T],
                      extra_tools: List[Dict[str, Any]] | None = None,
                      max_tokens: int | None = None) -> T:
    return run(system_prompt, [text_block(user_prompt)], output_model,
               extra_tools=extra_tools, max_tokens=max_tokens)

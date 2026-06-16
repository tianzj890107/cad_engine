"""
共享的 Anthropic / Claude 客户端封装。

设计要点:
  - 单例客户端。
  - 用 prompt caching 缓存稳定的大段 system prompt(降低重复成本)。
  - 复杂图纸理解任务开启 extended thinking。
  - 用"工具调用(tool use)"承载结构化输出: Claude 调用一个 input_schema 即 IR
    的工具，我们再用 Pydantic 校验。

为什么不用 structured outputs(messages.parse / output_format):
  那条路径会把 JSON Schema 编译成受约束解码的 grammar，对我们这种较大的嵌套
  IR schema 会触发 "compiled grammar is too large" 400 错误。
  非严格(non-strict)的工具调用不编译 grammar，可承载任意复杂 schema，
  再配合 Pydantic 校验 + 一次修复重试，既稳又可保留 thinking。
"""
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


def image_block(image_bytes: bytes, filename: str) -> Dict[str, Any]:
    """构造一个图片内容块(base64)。"""
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": _media_type_for(filename),
            "data": b64,
        },
    }


def text_block(text: str) -> Dict[str, Any]:
    """构造一个文本内容块。"""
    return {"type": "text", "text": text}


_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
_TEXT_EXTS = (".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml", ".ini")


def attachment_blocks(attachments) -> List[Dict[str, Any]]:
    """把附件 [(filename, bytes)] 转成内容块: 图片→image 块; 文本→text 块。"""
    blocks: List[Dict[str, Any]] = []
    for (name, data) in attachments or []:
        lower = (name or "").lower()
        if lower.endswith(_IMAGE_EXTS):
            blocks.append(text_block(f"【附件图片: {name}】"))
            blocks.append(image_block(data, name))
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
    """从响应中收集 web_search 检索到的来源 {url: title},作为可追溯证据。"""
    for block in getattr(resp, "content", None) or []:
        if getattr(block, "type", None) == "web_search_tool_result":
            content = getattr(block, "content", None)
            if not isinstance(content, list):
                continue  # 错误结果(web_search_tool_result_error)跳过
            for r in content:
                url = getattr(r, "url", None)
                if url and url not in acc:
                    acc[url] = getattr(r, "title", None) or url


def _build_tool(output_model: Type[T]) -> Dict[str, Any]:
    return {
        "name": _TOOL_NAME,
        "description": (
            "输出解析/拆解得到的结构化设计意图 IR。"
            "必须调用本工具一次，把结果作为工具输入提交。"
        ),
        "input_schema": output_model.model_json_schema(),
    }


def _extract_tool_input(response, tool_name: str):
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return block.input
    return None


# 服务端联网检索工具(Opus 4.8 支持;内置 dynamic filtering)。用于成本分析获取实时行情。
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}

_MAX_PAUSE_RESUMES = 4  # 服务端工具(web_search)循环达上限会 pause_turn,续跑次数上限


def _create_until_done(client, system, tools, messages, max_tokens, sources=None):
    """调用 messages.create;若服务端工具循环 pause_turn,自动续跑直到产出最终结果。"""
    resp = client.messages.create(
        model=CLAUDE_MODEL, max_tokens=max_tokens, thinking={"type": "adaptive"},
        system=system, tools=tools, messages=messages,
        extra_body={"output_config": {"effort": "high"}},
    )
    if sources is not None:
        _collect_web_sources(resp, sources)
    resumes = 0
    while resp.stop_reason == "pause_turn" and resumes < _MAX_PAUSE_RESUMES:
        # 把助手已产出的内容(含 server_tool_use / 检索结果 / thinking)原样回传以续跑
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


def run(
    system_prompt: str,
    user_content: List[Dict[str, Any]],
    output_model: Type[T],
    extra_tools: List[Dict[str, Any]] | None = None,
    max_tokens: int | None = None,
    sources_out: list | None = None,
) -> T:
    """
    用工具调用承载结构化输出。user_content 是任意多模态内容块列表
    (可含多张图片 + 文本)，便于附带补充说明 / 佐证文件。
    extra_tools: 额外工具(如服务端 web_search),Claude 可先用它们再调 emit_design_ir。
    sources_out: 传入一个 list,函数会把 web_search 检索到的 {title,url} 来源填入(可追溯证据)。
    第 1 次: 带 thinking + tool_choice=auto;若未产出或校验失败，第 2 次带修复提示重试。
    """
    client = get_client()
    tool = _build_tool(output_model)
    tools = [tool] + list(extra_tools or [])
    max_tokens = max_tokens or _MAX_TOKENS
    system = [
        {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
    ]
    acc: Dict[str, str] | None = {} if sources_out is not None else None

    def _finish(model: T) -> T:
        if sources_out is not None and acc:
            sources_out.extend({"title": acc[u], "url": u} for u in acc)
        return model

    # ---- 第 1 次: 带 thinking ----
    # Opus 4.8 使用 adaptive thinking + output_config.effort 控制思考强度
    # (不支持 thinking.type.enabled)。effort 经 extra_body 透传。
    resp = _create_until_done(
        client, system, tools, [{"role": "user", "content": user_content}], max_tokens, acc
    )
    data = _extract_tool_input(resp, _TOOL_NAME)
    err = None
    if data is not None:
        try:
            return _finish(output_model.model_validate(data))
        except ValidationError as e:
            err = str(e)
    else:
        err = "未调用 emit_design_ir 工具。"

    # ---- 第 2 次: 修复提示重试 ----
    # (Opus 4.8 强制 tool_choice 与 thinking 不兼容，故仍用 auto，靠提示+工具描述
    #  确保调用)
    repair = user_content + [
        {
            "type": "text",
            "text": (
                f"上一次未能得到合规结果({err})。"
                f"请务必调用 {_TOOL_NAME} 工具，严格按其 input_schema 输出，"
                f"确保所有必填字段齐全、类型正确。"
            ),
        }
    ]
    resp2 = _create_until_done(
        client, system, tools, [{"role": "user", "content": repair}], max_tokens, acc
    )
    data2 = _extract_tool_input(resp2, _TOOL_NAME)
    if data2 is None:
        raise RuntimeError(
            f"Claude 未能产出结构化结果 (stop_reason={resp2.stop_reason})。"
        )
    try:
        return _finish(output_model.model_validate(data2))
    except ValidationError as e:
        raise RuntimeError(f"Claude 输出未通过 schema 校验: {e}") from e


def parse_image_to_model(
    image_bytes: bytes,
    filename: str,
    system_prompt: str,
    user_instruction: str,
    output_model: Type[T],
) -> T:
    """给定一张图 + 提示词 + 目标模型，用 Claude 视觉 + 工具调用解析成该模型实例。"""
    content = [image_block(image_bytes, filename), text_block(user_instruction)]
    return run(system_prompt, content, output_model)


def complete_to_model(
    system_prompt: str,
    user_prompt: str,
    output_model: Type[T],
    extra_tools: List[Dict[str, Any]] | None = None,
    max_tokens: int | None = None,
) -> T:
    """纯文本(无图)的结构化调用，用于拆解推理等。
    extra_tools 可传 [WEB_SEARCH_TOOL] 让 Claude 先联网检索再产出结构化结果。"""
    return run(system_prompt, [text_block(user_prompt)], output_model,
               extra_tools=extra_tools, max_tokens=max_tokens)

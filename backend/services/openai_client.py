"""OpenAI Responses API 客户端封装，负责多模态、结构化输出与联网检索。"""
from __future__ import annotations

import base64
import functools
import json
import time
from typing import Any, Dict, List, Type, TypeVar

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import BaseModel, ValidationError

from ..config import (
    LLM_MAX_ATTACHMENT_IMAGE_BYTES, LLM_MAX_ATTACHMENT_TEXT_CHARS,
    LLM_MAX_ATTACHMENTS,
    OPENAI_API_KEY, OPENAI_BACKGROUND_TIMEOUT_SECONDS, OPENAI_MAX_OUTPUT_TOKENS,
    OPENAI_MAX_RETRIES, OPENAI_MAX_TOOL_CALLS, OPENAI_MODEL, OPENAI_PROXY_URL,
    OPENAI_REASONING_EFFORT,
    OPENAI_SCHEMA_REPAIR_RETRIES, OPENAI_TEXT_MAX_OUTPUT_TOKENS,
    OPENAI_TEXT_MODEL, OPENAI_TIMEOUT_SECONDS, OPENAI_VISION_MAX_OUTPUT_TOKENS,
)

T = TypeVar("T", bound=BaseModel)
_MAX_OUTPUT_TOKENS = OPENAI_MAX_OUTPUT_TOKENS
_BACKGROUND_POLL_INTERVAL_SECONDS = 2.0


def _json_output_rule(has_tools: bool) -> str:
    """生成与本次工具配置一致的最终 JSON 规则。"""
    tool_rule = (
        "本次可使用请求中实际提供的工具获取必要信息；"
        "最终回答仍只能输出一个 JSON 对象，不要把工具调用或解释写入最终结果。"
        if has_tools
        else "本次没有可调用的输出工具；忽略前文任何要求调用不存在的 tool 或 function 的旧版指令。"
    )
    return (
        "\n\n【本次运行的最高优先级输出规则】"
        + tool_rule
        + "只输出一个合法 JSON 对象，不要使用 Markdown 代码块、解释文字或注释。"
        "字段必须满足用户要求的数据结构；没有可靠信息时使用相应的空值或 open_questions，"
        "不得虚构尺寸。"
    )


def _api_error_detail(exc: Exception) -> str:
    """提取服务端可公开的错误文本，不输出 API Key 等本地配置。"""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict):
            message = error.get("message") or error.get("type") or error.get("code")
            if message:
                return str(message)
    message = str(exc).strip()
    cause = exc.__cause__ or exc.__context__
    if cause:
        cause_message = str(cause).strip()
        cause_desc = f"{type(cause).__name__}: {cause_message}" if cause_message else type(cause).__name__
        return f"{message or type(exc).__name__}（底层异常: {cause_desc}）"
    return message if message else "未提供服务端错误详情"


def format_openai_exception(exc: Exception, *, submission_state_unknown: bool = False) -> str:
    """把 OpenAI SDK 异常转换为前端可直接展示的中文诊断。"""
    status_code = getattr(exc, "status_code", None)
    request_id = getattr(exc, "request_id", None)
    detail = _api_error_detail(exc)

    if status_code == 401:
        diagnosis = "API Key 错误、失效，或未被 API 平台识别。"
        action = "检查 .env 中 OPENAI_API_KEY；确认使用的是 API Key，而非 ChatGPT 登录令牌。"
    elif status_code == 403:
        diagnosis = "权限、项目组织、地区，或代理/网关策略拒绝了请求。"
        action = "检查 API 项目权限、模型访问资格、网络代理与地区限制。"
    elif status_code == 404:
        diagnosis = "API 地址、模型名称，或所选端点不存在。"
        action = "检查 OPENAI_MODEL 和服务端 API Base URL（如配置了代理）。"
    elif status_code == 429:
        diagnosis = "额度不足、账户限额已到，或请求频率过高。"
        action = "检查 API 账单/额度和速率限制；稍后重试或降低并发 TASK_WORKERS。"
    elif status_code in (500, 502, 503, 504):
        diagnosis = "OpenAI 服务端或中转服务暂时异常。"
        action = "稍后重试；若持续发生，检查所使用的代理/中转服务状态。"
    elif status_code is not None:
        diagnosis = "API 请求被拒绝或未能完成。"
        action = "根据下方服务端详情检查请求参数、模型配置和账户状态。"
    else:
        diagnosis = "DNS、代理、TLS、网络连接或请求超时导致未收到 HTTP 状态码。"
        if submission_state_unknown:
            action = (
                "本次提交是否已被服务端接收无法确认；请不要立刻重复提交图纸。"
                "先等待片刻并查看用量/任务记录，再决定是否重试。"
            )
        else:
            action = "检查网络、代理变量（HTTP_PROXY/HTTPS_PROXY）、DNS 与 TLS 证书；然后重试。"

    lines = [
        "OpenAI 调用失败",
        f"HTTP 状态码: {status_code if status_code is not None else '无（未连接到 API）'}",
        f"原因判断: {diagnosis}",
        f"服务端详情: {detail}",
        f"建议处理: {action}",
    ]
    if request_id:
        lines.append(f"请求 ID: {request_id}")
    return "\n".join(lines)


@functools.lru_cache(maxsize=1)
def get_client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "未配置 OPENAI_API_KEY。请复制 .env.example 为 .env 并填入你的 OpenAI API Key。"
        )
    # 显式指定代理，避免后台服务在启动后丢失系统代理环境变量。
    # 默认不自动重试：对带图请求，代理已断开时重发会带来额外费用。
    http_client = httpx.Client(
        proxy=OPENAI_PROXY_URL or None,
        timeout=httpx.Timeout(OPENAI_TIMEOUT_SECONDS, connect=30.0),
    )
    return OpenAI(
        api_key=OPENAI_API_KEY,
        http_client=http_client,
        max_retries=OPENAI_MAX_RETRIES,
        timeout=OPENAI_TIMEOUT_SECONDS,
    )


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
    if f.endswith(".bmp"):
        # 不再把 BMP 伪装成 PNG；若服务端不支持，会返回明确的格式错误。
        return "image/bmp"
    return "image/png"


def image_block(image_bytes: bytes, filename: str, detail: str = "high") -> Dict[str, Any]:
    """构造 Responses API 的 base64 图片内容块。"""
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    return {
        "type": "input_image",
        "image_url": f"data:{_media_type_for(filename)};base64,{b64}",
        "detail": detail,
    }


def text_block(text: str) -> Dict[str, Any]:
    return {"type": "input_text", "text": text}


_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
_TEXT_EXTS = (".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml", ".ini")


def attachment_blocks(attachments) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    attachments = list(attachments or [])
    for index, (name, data) in enumerate(attachments):
        if index >= LLM_MAX_ATTACHMENTS:
            blocks.append(text_block(
                f"【其余 {len(attachments) - index} 个佐证附件因上下文预算未发送给模型】"
            ))
            break
        lower = (name or "").lower()
        if lower.endswith(_IMAGE_EXTS):
            if len(data) > LLM_MAX_ATTACHMENT_IMAGE_BYTES:
                blocks.append(text_block(
                    f"【附件图片 {name} 过大，未发送给模型（上限 {LLM_MAX_ATTACHMENT_IMAGE_BYTES} bytes）】"
                ))
                continue
            # 原图保持 high；佐证图通常只需辅助上下文，low 可显著降低视觉 token 消耗。
            blocks.extend([text_block(f"【附件图片: {name}】"), image_block(data, name, detail="low")])
        elif lower.endswith(_TEXT_EXTS):
            text = data.decode("utf-8", errors="replace")
            if len(text) > LLM_MAX_ATTACHMENT_TEXT_CHARS:
                text = (
                    text[:LLM_MAX_ATTACHMENT_TEXT_CHARS]
                    + "\n【附件内容已按上下文预算截断】"
                )
            blocks.append(text_block(f"【附件: {name}】\n{text}"))
        else:
            blocks.append(text_block(f"【附件 {name} 类型暂不支持解析，已忽略】"))
    return blocks


# OpenAI 托管的联网检索工具。保持原模块常量名，调用方无需变更。
WEB_SEARCH_TOOL = {"type": "web_search"}


def _collect_web_sources(response, sources_out: list | None) -> None:
    """从 Responses 输出的 URL citation 注解中提取可追溯来源。"""
    if sources_out is None:
        return
    seen = {item.get("url") for item in sources_out if isinstance(item, dict)}
    payload = response.model_dump() if hasattr(response, "model_dump") else response
    for item in (payload.get("output", []) if isinstance(payload, dict) else []):
        for content in item.get("content", []) or []:
            for annotation in content.get("annotations", []) or []:
                citation = annotation.get("url_citation", {}) if isinstance(annotation, dict) else {}
                url = citation.get("url")
                if url and url not in seen:
                    sources_out.append({"title": citation.get("title") or url, "url": url})
                    seen.add(url)


def _value_from(value: Any, key: str) -> Any:
    """同时兼容 SDK 对象和离线测试中使用的 dict。"""
    return value.get(key) if isinstance(value, dict) else getattr(value, key, None)


def _background_usage_summary(response) -> str:
    """仅提取可计量字段，不把提示词、图片或完整响应写入前端/任务记录。"""
    usage = _value_from(response, "usage")
    if not usage:
        return ""

    def number(name: str) -> str | None:
        value = _value_from(usage, name)
        return str(value) if isinstance(value, (int, float)) else None

    parts: List[str] = []
    labels = (("input_tokens", "输入"), ("output_tokens", "输出"), ("total_tokens", "总计"))
    for field, label in labels:
        value = number(field)
        if value is not None:
            parts.append(f"{label} {value}")

    output_details = _value_from(usage, "output_tokens_details")
    reasoning = _value_from(output_details, "reasoning_tokens") if output_details else None
    if isinstance(reasoning, (int, float)):
        parts.append(f"其中推理 {reasoning}")
    return f"\n本次 token 用量: {'，'.join(parts)}" if parts else ""


def _background_error_detail(response) -> str:
    """将后台任务的最终失败状态转成可读、低风险的中文诊断。"""
    error = _value_from(response, "error")
    if error:
        if hasattr(error, "model_dump"):
            error = error.model_dump()
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or error)
        return str(error)

    status = _value_from(response, "status")
    if status == "incomplete":
        details = _value_from(response, "incomplete_details")
        reason = _value_from(details, "reason") if details else None
        max_tokens = _value_from(response, "max_output_tokens")
        if reason in {"max_output_tokens", "max_tokens"}:
            limit = f"（本次上限 {max_tokens}）" if isinstance(max_tokens, int) else ""
            return (
                f"模型达到 max_output_tokens 上限{limit}，输出或推理未完成。"
                "系统未自动重试，避免再次发送图纸产生额外费用。"
            )
        if reason == "content_filter":
            return "模型输出触发内容安全过滤而未完成；提高 token 上限或延长等待均无效。"
        return "模型输出未完成，服务端未提供可识别原因；系统未自动重试。"
    return f"状态为 {status or 'unknown'}"


def _json_object_format() -> Dict[str, str]:
    """仅约束 JSON 对象，避免大型 strict Schema 强制输出全部可选 CAD 字段。"""
    return {"type": "json_object"}


def _reasoning_params(model: str) -> Dict[str, str] | None:
    """只向已知的 reasoning 模型传递 effort，避免文本模型因不支持该参数而报 400。"""
    if not OPENAI_REASONING_EFFORT:
        return None
    normalized_model = (model or "").strip().lower()
    if not normalized_model.startswith(("gpt-5", "o1", "o3", "o4")):
        return None
    return {"effort": OPENAI_REASONING_EFFORT}


def _openai_instruction_text(text: str) -> str:
    """旧模块面向 Claude 工具调用的措辞，切到 OpenAI 时改为直接 JSON 输出。"""
    return text.replace("只通过调用工具输出", "只输出").replace("调用工具输出", "输出")


def _openai_content(content: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """不改变图片或附件，仅消除自动生成提示词中的工具调用冲突。"""
    normalized: List[Dict[str, Any]] = []
    for block in content:
        if block.get("type") == "input_text" and isinstance(block.get("text"), str):
            normalized.append({**block, "text": _openai_instruction_text(block["text"])})
        else:
            normalized.append(block)
    return normalized


def _is_vision_request(content: List[Dict[str, Any]]) -> bool:
    return any(block.get("type") == "input_image" for block in content)


def _cancel_background_response(response) -> None:
    """本地等待上限到达时尽力取消远端任务，避免后台继续消耗额度。"""
    response_id = getattr(response, "id", None)
    if not response_id:
        return
    try:
        get_client().responses.cancel(response_id)
    except Exception:  # noqa: BLE001 - 取消失败不覆盖原始超时提示
        pass


def _format_output_validation_error(exc: Exception, response=None) -> str:
    """把本地 JSON/schema 错误转为可操作且明确说明未重试的提示。"""
    if isinstance(exc, json.JSONDecodeError):
        detail = f"JSON 语法错误: {exc.msg}（位置 {exc.pos}）"
    elif isinstance(exc, ValidationError):
        items = []
        for error in exc.errors()[:8]:
            path = ".".join(str(part) for part in error.get("loc", ()))
            items.append(f"{path}: {error.get('msg', '字段无效')}")
        detail = "；".join(items) or str(exc)
    else:
        detail = str(exc)
    return (
        "OpenAI 已返回结果，但字段未通过本地数据校验。"
        "为避免再次发送图纸并产生额外费用，系统未自动重试。\n"
        f"校验详情: {detail}"
        + (_background_usage_summary(response) if response is not None else "")
    )


def _response_id_note(response) -> str:
    """只显示短响应 ID，便于排查未知状态且不暴露输入内容。"""
    response_id = _value_from(response, "id")
    if isinstance(response_id, str) and 0 < len(response_id) <= 128:
        return f"（响应 ID: {response_id}）"
    return ""


def _is_retryable_poll_exception(exc: Exception) -> bool:
    """轮询失败时只能重查同一 response ID，绝不重新创建图纸任务。"""
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True
    if isinstance(exc, APIStatusError):
        return getattr(exc, "status_code", None) in {408, 429, 500, 502, 503, 504}
    return False


def _wait_for_background_response(response):
    """短连接轮询同一后台任务；查询失败不重复提交原图。"""
    deadline = time.monotonic() + OPENAI_BACKGROUND_TIMEOUT_SECONDS
    last_poll_error: Exception | None = None
    while getattr(response, "status", None) in {"queued", "in_progress"}:
        if time.monotonic() >= deadline:
            _cancel_background_response(response)
            if last_poll_error is not None:
                raise RuntimeError(
                    "OpenAI 后台任务在轮询网络异常后仍无法确认完成状态"
                    + _response_id_note(response)
                    + "；已请求取消远端任务。请不要立即重复提交图纸，以免产生重复费用。\n"
                    + format_openai_exception(last_poll_error)
                ) from last_poll_error
            raise RuntimeError(
                "OpenAI 后台任务等待超时，已请求取消远端任务 "
                f"({int(OPENAI_BACKGROUND_TIMEOUT_SECONDS)} 秒)。"
            )
        time.sleep(_BACKGROUND_POLL_INTERVAL_SECONDS)
        try:
            response = get_client().responses.retrieve(response.id)
            last_poll_error = None
        except (APIStatusError, APIConnectionError, APITimeoutError) as exc:
            if _is_retryable_poll_exception(exc):
                last_poll_error = exc
                continue
            _cancel_background_response(response)
            raise RuntimeError(
                "OpenAI 后台任务状态查询失败"
                + _response_id_note(response)
                + "；已请求取消远端任务。请不要立即重复提交图纸，以免产生重复费用。\n"
                + format_openai_exception(exc)
            ) from exc

    if getattr(response, "status", None) != "completed":
        raise RuntimeError(
            "OpenAI 后台任务未完成："
            + _background_error_detail(response)
            + _background_usage_summary(response)
        )
    return response


def run(
    system_prompt: str,
    user_content: List[Dict[str, Any]],
    output_model: Type[T],
    extra_tools: List[Dict[str, Any]] | None = None,
    max_tokens: int | None = None,
    sources_out: list | None = None,
) -> T:
    """执行多模态 JSON 输出请求，再在本地用 Pydantic 严格校验。

    请求端只要求 JSON 对象；完整 DesignIR 约束仍在本地 Pydantic 和归一化器中执行。
    这样不会因为 strict Schema 强制模型输出所有可选字段而耗尽图纸任务的 token 预算。
    """
    # Responses API 的 json_object 模式要求 *input message* 中也出现 “json”。
    # instructions 中已有约束，但它不计入这一项服务端校验；放在用户内容首块
    # 可避免请求在发送后立刻收到 400。
    json_contract = text_block(
        "请按上方要求只返回一个完整、合法的 JSON 对象；不要输出任何其他文字。"
    )
    is_vision = _is_vision_request(user_content)
    selected_model = OPENAI_MODEL if is_vision else OPENAI_TEXT_MODEL
    output_limit = max_tokens or (
        OPENAI_VISION_MAX_OUTPUT_TOKENS if is_vision else OPENAI_TEXT_MAX_OUTPUT_TOKENS
    )
    output_limit = min(output_limit, _MAX_OUTPUT_TOKENS)
    tools = list(extra_tools or [])
    reasoning = _reasoning_params(selected_model)

    def request(content: List[Dict[str, Any]], request_tools: List[Dict[str, Any]] | None = None):
        active_tools = tools if request_tools is None else list(request_tools)
        request_args: Dict[str, Any] = {
            "model": selected_model,
            "instructions": _openai_instruction_text(system_prompt) + _json_output_rule(bool(active_tools)),
            "input": [{"role": "user", "content": [json_contract, *_openai_content(content)]}],
            "tools": active_tools,
            # 普通 JSON 模式让模型只返回对象；字段契约由本地 Pydantic 负责。
            # 这保留可选字段的省略能力，避免大量 null 使视觉任务打满 token 上限。
            "text": {"format": _json_object_format()},
            "max_output_tokens": output_limit,
            "max_tool_calls": OPENAI_MAX_TOOL_CALLS if active_tools else None,
            # 首个请求很快返回 queued；随后以短 GET 轮询。这样不会让 Clash
            # 在模型推理期间维持约三分钟无数据的长连接。
            "background": True,
        }
        if reasoning:
            request_args["reasoning"] = reasoning
        try:
            response = get_client().responses.create(**request_args)
        except (APIStatusError, APIConnectionError, APITimeoutError) as exc:
            raise RuntimeError(
                format_openai_exception(exc, submission_state_unknown=True)
            ) from exc
        return _wait_for_background_response(response)

    response = request(user_content)
    _collect_web_sources(response, sources_out)
    if getattr(response, "refusal", None):
        raise RuntimeError(f"OpenAI 拒绝了该请求: {response.refusal}")

    try:
        return output_model.model_validate(json.loads(response.output_text))
    except (json.JSONDecodeError, ValidationError) as exc:
        if OPENAI_SCHEMA_REPAIR_RETRIES <= 0:
            raise RuntimeError(_format_output_validation_error(exc, response)) from exc

        # 若用户显式开启修复，只传回模型刚产出的 JSON，不再重复上传原图。
        repair = [text_block(
            "下面这份 JSON 未通过数据校验。只修复结构/缺失字段，"
            "不得补造图纸上没有的尺寸或材料信息；只输出完整 JSON。\n\n"
            f"校验错误: {exc}\n\n原 JSON:\n{response.output_text}"
        )]
        last_exc: Exception = exc
        for _ in range(OPENAI_SCHEMA_REPAIR_RETRIES):
            # 修复只处理已有 JSON，禁止再次上传图纸或调用联网工具。
            response = request(repair, request_tools=[])
            _collect_web_sources(response, sources_out)
            try:
                return output_model.model_validate(json.loads(response.output_text))
            except (json.JSONDecodeError, ValidationError) as repair_exc:
                last_exc = repair_exc
                repair = [text_block(
                    "上一份修复后的 JSON 仍未通过校验。只修复下列错误并输出完整 JSON，"
                    "不要添加解释。\n\n"
                    f"校验错误: {repair_exc}\n\n原 JSON:\n{response.output_text}"
                )]
        raise RuntimeError(_format_output_validation_error(last_exc, response)) from last_exc


def parse_image_to_model(
    image_bytes: bytes,
    filename: str,
    system_prompt: str,
    user_instruction: str,
    output_model: Type[T],
) -> T:
    return run(system_prompt, [image_block(image_bytes, filename), text_block(user_instruction)], output_model)


def complete_to_model(
    system_prompt: str,
    user_prompt: str,
    output_model: Type[T],
    extra_tools: List[Dict[str, Any]] | None = None,
    max_tokens: int | None = None,
) -> T:
    return run(system_prompt, [text_block(user_prompt)], output_model, extra_tools, max_tokens)

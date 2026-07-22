"""阿里云百炼 Qwen 的 OpenAI Chat Completions 兼容客户端。

Qwen 的视觉兼容接口是 /chat/completions，不是 OpenAI Responses API；因此独立实现，
不改动现有 OpenAI / Anthropic 调用路径。默认 qwen3-vl-flash、关闭思考模式。
"""
from __future__ import annotations

import base64
import contextvars
import functools
import json
import os
import threading
from typing import Any, Dict, List, Type, TypeVar

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import BaseModel, ValidationError

from ..config import (
    LLM_MAX_ATTACHMENT_IMAGE_BYTES, LLM_MAX_ATTACHMENT_TEXT_CHARS, LLM_MAX_ATTACHMENTS,
    QWEN_API_KEY, QWEN_BASE_URL, QWEN_ENABLE_THINKING, QWEN_MAX_OUTPUT_TOKENS,
    QWEN_MAX_RETRIES, QWEN_MODEL, QWEN_PROXY_URL, QWEN_TEXT_MAX_OUTPUT_TOKENS,
    DATA_DIR, QWEN_TEXT_MODEL, QWEN_TEXT_MODELS, QWEN_TIMEOUT_SECONDS, QWEN_VISION_MAX_OUTPUT_TOKENS,
    QWEN_VISION_MODELS, QWEN_DASHSCOPE_BASE_URL, QWEN_WEB_SEARCH_MODELS,
)
from ..models.cost import WebSource

T = TypeVar("T", bound=BaseModel)
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
_TEXT_EXTS = (".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml", ".ini")

# 百炼的 OpenAI 兼容 Chat 接口只支持 function tools，不提供 OpenAI hosted web_search。
WEB_SEARCH_TOOL = {"type": "qwen_web_search_not_supported"}

# 额度耗尽的模型在当前服务进程内会被暂时跳过。重启后会重新尝试主模型，便于
# 百炼免费额度刷新后自动恢复使用；不会写入或覆盖用户的 .env 配置。
_unavailable_models: dict[str, set[str]] = {"vision": set(), "text": set(), "web": set()}
_pool_lock = threading.Lock()
_last_used_model: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "qwen_last_used_model", default=None
)

# 管理员可在首页「模型设置」中调整的 Qwen 运行时配置。密钥不会通过任何
# API 返回；仅保存在 data/ 内的权限收紧文件中，重启后依然生效。
_runtime_path = DATA_DIR / "llm_runtime.json"
_runtime_lock = threading.RLock()


def _clean_models(value: Any, fallback: tuple[str, ...]) -> tuple[str, ...]:
    raw = value if isinstance(value, list) else fallback
    unique: list[str] = []
    for item in raw:
        model = str(item or "").strip()
        if model and model not in unique:
            unique.append(model)
    return tuple(unique) or fallback


def _load_runtime() -> dict[str, Any]:
    base = {
        "api_key": QWEN_API_KEY,
        "base_url": QWEN_BASE_URL,
        "vision_models": tuple(QWEN_VISION_MODELS),
        "text_models": tuple(QWEN_TEXT_MODELS),
        "web_search_models": tuple(QWEN_WEB_SEARCH_MODELS),
    }
    try:
        saved = json.loads(_runtime_path.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            base["api_key"] = str(saved.get("api_key") or base["api_key"])
            base["base_url"] = str(saved.get("base_url") or base["base_url"]).rstrip("/")
            base["vision_models"] = _clean_models(saved.get("vision_models"), base["vision_models"])
            base["text_models"] = _clean_models(saved.get("text_models"), base["text_models"])
            base["web_search_models"] = _clean_models(saved.get("web_search_models"), base["web_search_models"])
    except (OSError, json.JSONDecodeError):
        pass
    return base


_runtime = _load_runtime()


def runtime_settings() -> dict[str, Any]:
    """返回可安全展示给前端的当前设置，绝不暴露 API Key。"""
    with _runtime_lock:
        return {
            "provider": "qwen",
            "base_url": _runtime["base_url"],
            "api_key_configured": bool(_runtime["api_key"]),
            "vision_models": list(_runtime["vision_models"]),
            "text_models": list(_runtime["text_models"]),
            "web_search_models": list(_runtime["web_search_models"]),
        }


def configure_runtime_settings(*, api_key: str | None = None, base_url: str | None = None,
                               vision_models: list[str] | None = None,
                               text_models: list[str] | None = None,
                               web_search_models: list[str] | None = None) -> dict[str, Any]:
    """保存全局 Qwen 配置。空 API Key 表示保持现有密钥，避免前端回显秘密。"""
    with _runtime_lock:
        if api_key and api_key.strip():
            _runtime["api_key"] = api_key.strip()
        if base_url and base_url.strip():
            url = base_url.strip().rstrip("/")
            if not url.startswith(("https://", "http://")):
                raise ValueError("API Base URL 必须以 http:// 或 https:// 开头")
            _runtime["base_url"] = url
        if vision_models is not None:
            _runtime["vision_models"] = _clean_models(vision_models, tuple(QWEN_VISION_MODELS))
        if text_models is not None:
            _runtime["text_models"] = _clean_models(text_models, tuple(QWEN_TEXT_MODELS))
        if web_search_models is not None:
            _runtime["web_search_models"] = _clean_models(web_search_models, tuple(QWEN_WEB_SEARCH_MODELS))
        payload = {
            "api_key": _runtime["api_key"], "base_url": _runtime["base_url"],
            "vision_models": list(_runtime["vision_models"]), "text_models": list(_runtime["text_models"]),
            "web_search_models": list(_runtime["web_search_models"]),
        }
        _runtime_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(_runtime_path, 0o600)
        except OSError:
            pass
        _unavailable_models["vision"].clear()
        _unavailable_models["text"].clear()
        _unavailable_models["web"].clear()
    return runtime_settings()


def get_client() -> OpenAI:
    with _runtime_lock:
        api_key = _runtime["api_key"]
        base_url = _runtime["base_url"]
    if not api_key:
        raise RuntimeError("未配置 QWEN_API_KEY。请在 .env 填入百炼 API Key 后重启服务。")
    http_client = httpx.Client(
        proxy=QWEN_PROXY_URL or None,
        timeout=httpx.Timeout(QWEN_TIMEOUT_SECONDS, connect=30.0),
        # httpx 默认会读取 HTTPS_PROXY；Qwen 需要直连时必须禁用它，
        # 否则仍会误走仅为 OpenAI 配置的 Clash。
        trust_env=False,
    )
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        http_client=http_client,
        max_retries=QWEN_MAX_RETRIES,
        timeout=QWEN_TIMEOUT_SECONDS,
    )


def _media_type_for(filename: str) -> str:
    lower = (filename or "").lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    return "image/bmp" if lower.endswith(".bmp") else "image/png"


def image_block(image_bytes: bytes, filename: str, detail: str | None = None) -> Dict[str, Any]:
    """百炼视觉兼容接口使用 OpenAI Chat 的 image_url 内容块。"""
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{_media_type_for(filename)};base64,{b64}"},
    }


def text_block(text: str) -> Dict[str, Any]:
    return {"type": "text", "text": text}


def attachment_blocks(attachments) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    attachments = list(attachments or [])
    for index, (name, data) in enumerate(attachments):
        if index >= LLM_MAX_ATTACHMENTS:
            blocks.append(text_block(f"【其余 {len(attachments) - index} 个佐证附件因上下文预算未发送给模型】"))
            break
        lower = (name or "").lower()
        if lower.endswith(_IMAGE_EXTS):
            if len(data) > LLM_MAX_ATTACHMENT_IMAGE_BYTES:
                blocks.append(text_block(f"【附件图片 {name} 过大，未发送给模型】"))
                continue
            blocks.extend([text_block(f"【附件图片: {name}】"), image_block(data, name, detail="low")])
        elif lower.endswith(_TEXT_EXTS):
            value = data.decode("utf-8", errors="replace")
            if len(value) > LLM_MAX_ATTACHMENT_TEXT_CHARS:
                value = value[:LLM_MAX_ATTACHMENT_TEXT_CHARS] + "\n【附件内容已按上下文预算截断】"
            blocks.append(text_block(f"【附件: {name}】\n{value}"))
        else:
            blocks.append(text_block(f"【附件 {name} 类型暂不支持解析，已忽略】"))
    return blocks


def _is_vision(content: List[Dict[str, Any]]) -> bool:
    return any(block.get("type") == "image_url" for block in content)


def _usage_summary(response) -> str:
    usage = getattr(response, "usage", None)
    if usage is None:
        return ""
    parts = []
    for key, label in (("prompt_tokens", "输入"), ("completion_tokens", "输出"), ("total_tokens", "总计")):
        value = getattr(usage, key, None)
        if isinstance(value, int):
            parts.append(f"{label} {value}")
    return "\n本次 token 用量: " + "，".join(parts) if parts else ""


def _error_message(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    detail = str(exc).strip() or type(exc).__name__
    mapping = {
        401: "API Key 无效、失效或与当前地域不匹配。",
        403: "模型未开通、免费额度用尽或账号权限被拒绝。",
        404: "QWEN_BASE_URL、业务空间 ID 或模型名称不正确。",
        429: "百炼额度不足或请求过于频繁。",
    }
    diagnosis = mapping.get(status, "网络、代理、TLS 或百炼服务异常。" if status is None else "请求未能完成。")
    return f"Qwen 调用失败 HTTP 状态码: {status if status is not None else '无'}；原因判断: {diagnosis} 服务端详情: {detail}"


def _model_candidates(vision: bool) -> tuple[str, ...]:
    kind = "vision" if vision else "text"
    with _runtime_lock:
        configured = tuple(_runtime["vision_models"] if vision else _runtime["text_models"])
    with _pool_lock:
        available = tuple(model for model in configured if model not in _unavailable_models[kind])
    # 若全部模型都在本进程内被判定不可用，保留原始候选列表用于给出清晰错误，
    # 而不是悄悄请求一个未配置的模型。
    return available or configured


def _web_search_model_candidates() -> tuple[str, ...]:
    with _runtime_lock:
        configured = tuple(_runtime["web_search_models"])
    with _pool_lock:
        available = tuple(model for model in configured if model not in _unavailable_models["web"])
    return available or configured


def _should_switch_model(exc: Exception) -> bool:
    """只对服务端确认的额度、限流或当前模型不可用做同类型切换。"""
    # 404 通常表示某个候选模型不在当前业务空间/API 兼容端点中；主模型已可用时，
    # 跳过该候选比中断整条业务流程更合适。
    return getattr(exc, "status_code", None) in {403, 404, 429}


def _mark_model_unavailable(model: str, vision: bool) -> None:
    with _pool_lock:
        _unavailable_models["vision" if vision else "text"].add(model)


def model_pool_status() -> dict[str, dict[str, list[str]]]:
    """给健康检查和排障页使用，不暴露任何 Key。"""
    with _runtime_lock, _pool_lock:
        return {
            "vision": {
                "configured": list(_runtime["vision_models"]),
                "temporarily_unavailable": sorted(_unavailable_models["vision"]),
            },
            "text": {
                "configured": list(_runtime["text_models"]),
                "temporarily_unavailable": sorted(_unavailable_models["text"]),
            },
            "web_search": {
                "configured": list(_runtime["web_search_models"]),
                "temporarily_unavailable": sorted(_unavailable_models["web"]),
            },
        }


def last_used_model() -> str | None:
    """返回当前请求实际成功使用的模型，供留痕使用。"""
    return _last_used_model.get()


def _instruction(system_prompt: str) -> str:
    # Qwen 文档要求 json_object 模式在提示词中显式提及 JSON。
    return (
        system_prompt.replace("只通过调用工具输出", "只输出").replace("调用工具输出", "输出")
        + "\n\n【最高优先级】只返回一个完整、合法的 JSON 对象；不要 Markdown、解释或代码块。"
    )


def _load_json_object(content: str) -> dict:
    """兼容原生 DashScope 偶尔附带 Markdown 围栏的 JSON 输出。"""
    value = (content or "").strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[1] if "\n" in value else ""
        value = value.rsplit("```", 1)[0].strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        start, end = value.find("{"), value.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(value[start:end + 1])


def _native_search_sources(payload: dict) -> list[WebSource]:
    """从 DashScope 原生响应提取来源；OpenAI 兼容 Chat 接口不会返回该字段。"""
    raw = ((payload.get("output") or {}).get("search_info") or {}).get("search_results") or []
    result: list[WebSource] = []
    seen: set[str] = set()
    for item in raw:
        url = str((item or {}).get("url") or "").strip()
        if not url or url in seen or not url.startswith(("https://", "http://")):
            continue
        seen.add(url)
        result.append(WebSource(title=str((item or {}).get("title") or url)[:240], url=url[:2000]))
    return result[:20]


def complete_to_model_with_web_search(
    system_prompt: str,
    user_payload: dict,
    output_model: Type[T],
    *,
    max_tokens: int = 2600,
) -> tuple[T, dict]:
    """使用百炼原生搜索 API 得到结构化结果及真实来源。

    原生 DashScope 协议是有意选择：官方说明 OpenAI 兼容 Chat 的联网搜索不返回来源，
    而项目需要把型号识别证据保存并供人工核验。
    """
    with _runtime_lock:
        api_key = _runtime["api_key"]
    if not api_key:
        raise RuntimeError("未配置 QWEN_API_KEY，无法执行型号联网核验。")
    body = {
        "model": None,
        "input": {
            "messages": [
                {"role": "system", "content": _instruction(system_prompt)},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ]
        },
        "parameters": {
            "result_format": "message",
            "max_tokens": min(max_tokens, QWEN_MAX_OUTPUT_TOKENS),
            "enable_search": True,
            "search_options": {
                "forced_search": True,
                "search_strategy": "turbo",
                "enable_source": True,
            },
        },
    }
    attempted: list[str] = []
    last_error: Exception | None = None
    proxy = QWEN_PROXY_URL or None
    with httpx.Client(proxy=proxy, timeout=httpx.Timeout(QWEN_TIMEOUT_SECONDS, connect=30.0), trust_env=False) as client:
        for model in _web_search_model_candidates():
            attempted.append(model)
            body["model"] = model
            try:
                response = client.post(
                    QWEN_DASHSCOPE_BASE_URL,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=body,
                )
                if response.status_code >= 400:
                    error = RuntimeError(f"HTTP {response.status_code}: {response.text[:600]}")
                    setattr(error, "status_code", response.status_code)
                    raise error
                payload = response.json()
                if payload.get("code"):
                    error = RuntimeError(str(payload.get("message") or payload.get("code")))
                    setattr(error, "status_code", payload.get("status_code") or 400)
                    raise error
                message = (((payload.get("output") or {}).get("choices") or [{}])[0].get("message") or {})
                content = message.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise RuntimeError("百炼联网搜索未返回可解析内容")
                result = output_model.model_validate(_load_json_object(content))
                _last_used_model.set(model)
                usage = payload.get("usage") or {}
                search_count = int((((usage.get("plugins") or {}).get("search") or {}).get("count")) or 0)
                return result, {"model": model, "sources": _native_search_sources(payload), "search_count": search_count}
            except (httpx.HTTPError, ValueError, ValidationError, json.JSONDecodeError, RuntimeError) as exc:
                last_error = exc
                status = getattr(exc, "status_code", None)
                if status in {403, 404, 429}:
                    with _pool_lock:
                        _unavailable_models["web"].add(model)
                    continue
                break
    suffix = f" 已按顺序尝试：{', '.join(attempted)}。" if attempted else ""
    if last_error:
        raise RuntimeError("Qwen 型号联网核验失败：" + str(last_error) + suffix) from last_error
    raise RuntimeError("Qwen 未配置可用的联网搜索模型。")


def run(
    system_prompt: str,
    user_content: List[Dict[str, Any]],
    output_model: Type[T],
    extra_tools: List[Dict[str, Any]] | None = None,
    max_tokens: int | None = None,
    sources_out: list | None = None,
) -> T:
    """以单次同步 Chat Completions 调用得到 JSON；从不自动重发图纸。"""
    if extra_tools:
        raise RuntimeError("Qwen 路径暂不支持本项目的联网检索；请关闭“联网检索”后重试，或切回 OpenAI。")
    if sources_out is not None:
        sources_out.clear()
    vision = _is_vision(user_content)
    limit = max_tokens or (QWEN_VISION_MAX_OUTPUT_TOKENS if vision else QWEN_TEXT_MAX_OUTPUT_TOKENS)
    request_args: Dict[str, Any] = {
        "messages": [
            {"role": "system", "content": _instruction(system_prompt)},
            {"role": "user", "content": [text_block("请按要求输出 JSON。"), *user_content]},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": min(limit, QWEN_MAX_OUTPUT_TOKENS),
        "extra_body": {"enable_thinking": QWEN_ENABLE_THINKING},
    }
    attempted: list[str] = []
    response = None
    last_error: Exception | None = None
    for model in _model_candidates(vision):
        attempted.append(model)
        try:
            response = get_client().chat.completions.create(model=model, **request_args)
            _last_used_model.set(model)
            break
        except (APIConnectionError, APIStatusError, APITimeoutError) as exc:
            last_error = exc
            if _should_switch_model(exc):
                _mark_model_unavailable(model, vision)
                continue
            raise RuntimeError(_error_message(exc)) from exc
    if response is None:
        suffix = f" 已按顺序尝试：{', '.join(attempted)}。" if attempted else ""
        if last_error is not None:
            raise RuntimeError(_error_message(last_error) + suffix) from last_error
        raise RuntimeError("Qwen 未配置可用模型候选池。")
    choice = response.choices[0] if getattr(response, "choices", None) else None
    content = getattr(getattr(choice, "message", None), "content", None)
    if not isinstance(content, str) or not content.strip():
        finish_reason = getattr(choice, "finish_reason", None)
        raise RuntimeError(f"Qwen 未返回可解析内容（finish_reason={finish_reason or 'unknown'}）。" + _usage_summary(response))
    try:
        return output_model.model_validate(json.loads(content))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise RuntimeError(
            "Qwen 已返回结果，但字段未通过本地数据校验；系统未自动重试，避免再次发送图纸产生额外费用。"
            f" 校验详情: {exc}" + _usage_summary(response)
        ) from exc


def parse_image_to_model(image_bytes: bytes, filename: str, system_prompt: str, user_instruction: str, output_model: Type[T]) -> T:
    return run(system_prompt, [image_block(image_bytes, filename), text_block(user_instruction)], output_model)


def complete_to_model(system_prompt: str, user_prompt: str, output_model: Type[T], extra_tools=None, max_tokens=None) -> T:
    return run(system_prompt, [text_block(user_prompt)], output_model, extra_tools, max_tokens)

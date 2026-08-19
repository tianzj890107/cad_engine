"""阿里云百炼 Qwen 的 OpenAI Chat Completions 兼容客户端。

Qwen 的视觉兼容接口是 /chat/completions，不是 OpenAI Responses API；因此独立实现，
不改动现有 OpenAI / Anthropic 调用路径。默认 qwen3-vl-plus；视觉 JSON 关闭思考，文本任务开启思考。
"""
from __future__ import annotations

import base64
import contextvars
import json
import os
import threading
from enum import Enum
from types import UnionType
from typing import Any, Dict, List, Type, TypeVar, Union, get_args, get_origin

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import BaseModel, ValidationError

from ..config import (
    LLM_MAX_ATTACHMENT_IMAGE_BYTES, LLM_MAX_ATTACHMENT_TEXT_CHARS, LLM_MAX_ATTACHMENTS,
    QWEN_API_KEY, QWEN_BASE_URL, QWEN_MAX_OUTPUT_TOKENS,
    QWEN_MAX_RETRIES, QWEN_SCHEMA_REPAIR_RETRIES, QWEN_MODEL, QWEN_PROXY_URL, QWEN_TEXT_MAX_OUTPUT_TOKENS,
    DATA_DIR, QWEN_TEXT_MODEL, QWEN_TEXT_MODELS, QWEN_TIMEOUT_SECONDS, QWEN_VISION_MAX_OUTPUT_TOKENS,
    QWEN_VISION_MODELS, QWEN_DASHSCOPE_BASE_URL, QWEN_WEB_SEARCH_MODELS,
    QWEN_VISION_ENABLE_THINKING, QWEN_TEXT_ENABLE_THINKING,
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
_client_lock = threading.RLock()
_client: OpenAI | None = None
_client_signature: tuple[str, str, str, float] | None = None


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
        _close_cached_client()
    return runtime_settings()


def get_client() -> OpenAI:
    """Return a shared HTTP client instead of creating a connection pool per call."""
    global _client, _client_signature
    with _runtime_lock:
        api_key = _runtime["api_key"]
        base_url = _runtime["base_url"]
    if not api_key:
        raise RuntimeError("未配置 QWEN_API_KEY。请在 .env 填入百炼 API Key 后重启服务。")
    signature = (api_key, base_url, QWEN_PROXY_URL, float(QWEN_TIMEOUT_SECONDS))
    with _client_lock:
        if _client is not None and _client_signature == signature:
            return _client
        _close_cached_client()
        http_client = httpx.Client(
            proxy=QWEN_PROXY_URL or None,
            timeout=httpx.Timeout(QWEN_TIMEOUT_SECONDS, connect=30.0),
            # httpx 默认会读取 HTTPS_PROXY；Qwen 需要直连时必须禁用它，
            # 否则仍会误走仅为 OpenAI 配置的 Clash。
            trust_env=False,
        )
        _client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=http_client,
            max_retries=QWEN_MAX_RETRIES,
            timeout=QWEN_TIMEOUT_SECONDS,
        )
        _client_signature = signature
        return _client


def _close_cached_client() -> None:
    global _client, _client_signature
    with _client_lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
        _client = None
        _client_signature = None


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
        if LLM_MAX_ATTACHMENTS > 0 and index >= LLM_MAX_ATTACHMENTS:
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


def _message_content(choice) -> str:
    """兼容代理把 message.content 返回为字符串或内容块数组。"""
    content = getattr(getattr(choice, "message", None), "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                chunks.append(item["text"])
            elif isinstance(getattr(item, "text", None), str):
                chunks.append(item.text)
        return "".join(chunks)
    return ""


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


def _number_value(value: Any, *, integer: bool = False) -> Any:
    """宽容读取模型偶尔返回的“约 12 分钟”“85%”等数值表达。"""
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    import re
    matched = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    if not matched:
        return value
    number = float(matched.group())
    if "%" in str(value):
        number /= 100
    return int(number) if integer else number


def _unwrap_optional(annotation: Any) -> Any:
    """取 Optional[T] 的 T；用于对 Pydantic 模型的已知字段做保守归一。"""
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        non_none = [item for item in get_args(annotation) if item is not type(None)]
        return non_none[0] if len(non_none) == 1 else annotation
    return annotation


def _normalise_model_payload(value: Any, model: Type[BaseModel]) -> Any:
    """在 Pydantic 前处理无语义歧义的模型输出差异。

    仅做确定性转换，不猜测缺失的工程数据：JSON 字符串转对象、单项转列表、
    数值/布尔文本转标量、嵌套对象递归处理。各业务模型仍负责自己的字段别名和
    专业枚举映射。
    """
    if isinstance(value, str):
        try:
            value = _load_json_object(value)
        except (json.JSONDecodeError, ValueError):
            return value
    if not isinstance(value, dict):
        return value
    data = dict(value)
    for field_name, field in model.model_fields.items():
        if field_name not in data or data[field_name] is None:
            continue
        item = data[field_name]
        annotation = _unwrap_optional(field.annotation)
        origin = get_origin(annotation)
        args = get_args(annotation)

        if origin is list:
            if isinstance(item, str):
                try:
                    decoded = json.loads(item)
                    item = decoded if isinstance(decoded, list) else [decoded]
                except json.JSONDecodeError:
                    item = [item]
            elif not isinstance(item, list):
                item = [item]
            nested = _unwrap_optional(args[0]) if args else Any
            if isinstance(nested, type) and issubclass(nested, BaseModel):
                # Qwen 偶尔会在结构化数组中混入 "无"、0、空对象或一行说明。
                # 这类项没有可安全推断的业务含义；过滤单项比整次已付费调用失败更合理。
                # 对仍保留的对象递归做数值/枚举/JSON 字符串归一，严格字段语义仍交给 Pydantic。
                cleaned = []
                required_fields = {
                    name for name, nested_field in nested.model_fields.items()
                    if nested_field.is_required()
                }
                for entry in item:
                    normalized_entry = _normalise_model_payload(entry, nested)
                    if not isinstance(normalized_entry, dict):
                        # 型号联网核验的两个“推演建议”字段允许自然语言单项，
                        # 其父模型的 field_validator 会把它规整为对象；不能在此提前丢弃。
                        if (
                            isinstance(normalized_entry, str)
                            and model.__name__ == "ModelLookupResult"
                            and field_name in {"proposed_components", "process_designs"}
                        ):
                            cleaned.append(normalized_entry)
                        continue
                    if any(name not in normalized_entry or normalized_entry[name] is None for name in required_fields):
                        continue
                    cleaned.append(normalized_entry)
                item = cleaned
            data[field_name] = item
        elif origin is dict:
            if isinstance(item, str):
                try:
                    decoded = json.loads(item)
                    if isinstance(decoded, dict):
                        data[field_name] = decoded
                except json.JSONDecodeError:
                    pass
        elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
            data[field_name] = _normalise_model_payload(item, annotation)
        elif annotation is float:
            data[field_name] = _number_value(item)
        elif annotation is int:
            data[field_name] = _number_value(item, integer=True)
        elif annotation is bool and isinstance(item, str):
            normalized = item.strip().lower()
            if normalized in {"true", "yes", "y", "1", "是", "有", "需要", "推荐"}:
                data[field_name] = True
            elif normalized in {"false", "no", "n", "0", "否", "无", "不需要", "不推荐"}:
                data[field_name] = False
        elif isinstance(annotation, type) and issubclass(annotation, Enum) and isinstance(item, str):
            # 只接受恰好匹配的 enum 值；中文业务别名交由对应业务模型处理，避免误猜。
            for candidate in annotation:
                if item.strip().lower() == str(candidate.value).lower():
                    data[field_name] = candidate.value
                    break
    return data


def _validate_model_payload(output_model: Type[T], value: Any) -> T:
    return output_model.model_validate(_normalise_model_payload(value, output_model))


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
    base_web_user_content = json.dumps(user_payload, ensure_ascii=False)
    attempted: list[str] = []
    last_error: Exception | None = None
    proxy = QWEN_PROXY_URL or None
    with httpx.Client(proxy=proxy, timeout=httpx.Timeout(QWEN_TIMEOUT_SECONDS, connect=30.0), trust_env=False) as client:
        for model in _web_search_model_candidates():
            attempted.append(model)
            body["model"] = model
            body["input"]["messages"][1]["content"] = base_web_user_content
            for repair_index in range(QWEN_SCHEMA_REPAIR_RETRIES + 1):
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
                    result = _validate_model_payload(output_model, _load_json_object(content))
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
                        break
                    if repair_index < QWEN_SCHEMA_REPAIR_RETRIES:
                        body["input"]["messages"][1]["content"] = (
                            json.dumps(user_payload, ensure_ascii=False)
                            + "\n\n上一次输出未通过本地结构校验，请重新完整输出合法 JSON；不要输出 Markdown。"
                            + f"校验错误：{exc}"
                        )
                        body["parameters"]["max_tokens"] = min(QWEN_MAX_OUTPUT_TOKENS, max(max_tokens, 12000))
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
    """调用多模态/文本模型得到 JSON，并自动进行网络与结构化修复重试。

    业务 schema 仍然严格校验，防止不完整尺寸进入 CAD；这不是成本限制。
    团队 API 模式下网络失败、模型切换和 JSON/Pydantic 修复都在这里完成，
    调用方不需要让用户重新上传图纸。
    """
    if extra_tools:
        raise RuntimeError("Qwen 路径暂不支持本项目的联网检索；请关闭“联网检索”后重试，或切回 OpenAI。")
    if sources_out is not None:
        sources_out.clear()
    vision = _is_vision(user_content)
    limit = max_tokens or (QWEN_VISION_MAX_OUTPUT_TOKENS if vision else QWEN_TEXT_MAX_OUTPUT_TOKENS)
    base_user_content = [text_block("请按要求输出 JSON。"), *user_content]
    request_args: Dict[str, Any] = {
        "messages": [
            {"role": "system", "content": _instruction(system_prompt)},
            {"role": "user", "content": base_user_content},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": min(limit, QWEN_MAX_OUTPUT_TOKENS),
        "extra_body": {
            "enable_thinking": QWEN_VISION_ENABLE_THINKING if vision else QWEN_TEXT_ENABLE_THINKING
        },
    }
    attempted: list[str] = []
    response = None
    last_error: Exception | None = None
    validation_errors: list[str] = []
    for model in _model_candidates(vision):
        attempted.append(model)
        for repair_index in range(QWEN_SCHEMA_REPAIR_RETRIES + 1):
            try:
                response = get_client().chat.completions.create(model=model, **request_args)
                _last_used_model.set(model)
            except (APIConnectionError, APIStatusError, APITimeoutError) as exc:
                last_error = exc
                if _should_switch_model(exc):
                    _mark_model_unavailable(model, vision)
                    break
                # 网络超时/连接异常允许 SDK 重试；重试耗尽后继续下一个同能力模型。
                if repair_index < QWEN_SCHEMA_REPAIR_RETRIES:
                    continue
                break

            choice = response.choices[0] if getattr(response, "choices", None) else None
            content = _message_content(choice)
            finish_reason = getattr(choice, "finish_reason", None)
            if not content.strip() or finish_reason in {"length", "max_tokens"}:
                last_error = RuntimeError(
                    f"Qwen 输出未完整结束（finish_reason={finish_reason or 'unknown'}）。"
                    + _usage_summary(response)
                )
                if repair_index < QWEN_SCHEMA_REPAIR_RETRIES:
                    request_args["messages"] = [
                        {"role": "system", "content": _instruction(system_prompt)},
                        {"role": "user", "content": [
                            *base_user_content,
                            text_block("上一次没有返回完整 JSON。请重新完整输出，不要省略任何必填字段。"),
                        ]},
                    ]
                    # 截断时同样放大输出预算，避免修复调用再次被 max_tokens 截断；
                    # 与下方“未通过本地字段校验”分支保持一致的修复策略。
                    request_args["max_tokens"] = min(
                        QWEN_MAX_OUTPUT_TOKENS,
                        max(int(request_args["max_tokens"]), 24000 if vision else 12000),
                    )
                    continue
                break
            try:
                return _validate_model_payload(output_model, _load_json_object(content))
            except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
                validation_errors.append(f"{model}: {exc}{_usage_summary(response)}")
                last_error = exc
                if repair_index >= QWEN_SCHEMA_REPAIR_RETRIES:
                    break
                request_args["messages"] = [
                    {"role": "system", "content": _instruction(system_prompt)},
                    {"role": "user", "content": [
                        *base_user_content,
                        text_block(
                            "上一次输出未通过本地字段校验。请保留原图事实，重新输出完整 JSON；"
                            f"不要输出 Markdown。校验错误如下：{exc}"
                        ),
                    ]},
                ]
                # 修复调用给视觉结果更大的输出空间，避免上一轮被截断。
                request_args["max_tokens"] = min(
                    QWEN_MAX_OUTPUT_TOKENS,
                    max(int(request_args["max_tokens"]), 24000 if vision else 12000),
                )
                continue
        request_args["messages"] = [
            {"role": "system", "content": _instruction(system_prompt)},
            {"role": "user", "content": base_user_content},
        ]
    if response is None:
        suffix = f" 已按顺序尝试：{', '.join(attempted)}。" if attempted else ""
        if last_error is not None:
            raise RuntimeError(_error_message(last_error) + suffix) from last_error
        raise RuntimeError("Qwen 未配置可用模型候选池。")
    # 某个模型可能已经返回过内容，随后候选模型全部网络失败；不能因为 response
    # 不是 None 就丢掉最后一次 HTTP 状态码和代理诊断。
    if last_error is not None and (
        isinstance(last_error, (APIConnectionError, APIStatusError, APITimeoutError))
        or getattr(last_error, "status_code", None) is not None
    ):
        suffix = f" 已按顺序尝试：{', '.join(attempted)}。" if attempted else ""
        raise RuntimeError(_error_message(last_error) + suffix) from last_error
    detail = "；".join(validation_errors[-3:])
    if detail:
        raise RuntimeError(
            "Qwen 已按视觉/文本能力池及结构化修复策略重试，但仍未通过本地字段校验。"
            f" 校验详情: {detail}"
        ) from last_error
    if last_error:
        raise RuntimeError(str(last_error)) from last_error
    raise RuntimeError("Qwen 未返回可解析结果。")


def parse_image_to_model(image_bytes: bytes, filename: str, system_prompt: str, user_instruction: str, output_model: Type[T]) -> T:
    return run(system_prompt, [image_block(image_bytes, filename), text_block(user_instruction)], output_model)


def complete_to_model(system_prompt: str, user_prompt: str, output_model: Type[T], extra_tools=None, max_tokens=None) -> T:
    return run(system_prompt, [text_block(user_prompt)], output_model, extra_tools, max_tokens)

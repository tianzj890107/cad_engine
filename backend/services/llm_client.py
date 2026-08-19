"""模型调用分派层：按「模型设置」里选中的模型，走该提供商的**官方网关**。

以前这里在 import 时按 .env 的 LLM_PROVIDER 星号导入某一个客户端模块，于是：

  · 所有请求都发往部署时配的那一个兼容端点（MaaS 网关），
  · 选了 opus5 也只是拿这个 id 去问一个不认识它的网关，
  · 失败后还会静默降级到旧模型池 —— 用户看到的"AI 解析"出自他从没选过的型号。

现在改成**每次调用**按模型解析提供商：

    模型 → llm_settings.resolve() → (provider, 官方 base_url, 该商的 Key)
         → anthropic 走 claude_client（原生 SDK），其余走 OpenAI 兼容协议

内容块因此必须是**中立**的：调用方先构造块、后发起调用，而这两步之间才知道
最终由谁来跑。所以 text_block / image_block 产出中立结构，在 run() 里再翻译成
目标提供商的方言。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel

from . import llm_settings

T = TypeVar("T", bound=BaseModel)

# 中立图片块的标记。文本块两家格式一致，直接沿用。
_IMAGE = "_neutral_image"


# --------------------------------------------------------------------------- #
# 中立内容块
# --------------------------------------------------------------------------- #
def text_block(text: str) -> Dict[str, Any]:
    return {"type": "text", "text": text}


def image_block(image_bytes: bytes, filename: str, detail: str | None = None) -> Dict[str, Any]:
    return {"type": _IMAGE, "data": image_bytes, "filename": filename, "detail": detail}


def attachment_blocks(attachments) -> List[Dict[str, Any]]:
    """附件转中立块。裁剪策略沿用既有实现，只是产出中立结构。"""
    from .qwen_client import attachment_blocks as _build

    blocks = _build(attachments)
    out: List[Dict[str, Any]] = []
    for block in blocks:
        if block.get("type") == "image_url":
            # qwen 版已经编成 data URL，这里还原不了原始字节，因此直接透传标记，
            # 由 _translate 按目标方言处理。
            out.append({"type": _IMAGE, "data_url": block["image_url"]["url"]})
        else:
            out.append(block)
    return out


def _translate(content: List[Dict[str, Any]], module) -> List[Dict[str, Any]]:
    """把中立块翻成目标提供商的方言。"""
    out: List[Dict[str, Any]] = []
    for block in content:
        if block.get("type") != _IMAGE:
            out.append(block)
            continue
        if block.get("data") is not None:
            out.append(module.image_block(block["data"], block.get("filename") or "image.png",
                                          detail=block.get("detail")))
            continue
        # 只剩 data URL 的附件图：OpenAI 兼容协议能直接吃，Anthropic 不行。
        url = block.get("data_url") or ""
        if getattr(module, "__name__", "").endswith("claude_client"):
            import base64

            header, _, payload = url.partition(",")
            media = header.split(":", 1)[-1].split(";", 1)[0] or "image/png"
            out.append({"type": "image", "source": {
                "type": "base64", "media_type": media, "data": payload}})
        else:
            out.append({"type": "image_url", "image_url": {"url": url}})
    return out


# --------------------------------------------------------------------------- #
# 分派
# --------------------------------------------------------------------------- #
def _has_image(content: List[Dict[str, Any]]) -> bool:
    return any(block.get("type") in (_IMAGE, "image", "image_url") for block in content)


def _module_for(route: dict):
    if route["native"]:
        from . import claude_client

        return claude_client
    from . import qwen_client

    return qwen_client


def resolve_route(content: List[Dict[str, Any]]) -> dict:
    """带图走多模态模型，否则走语言模型。"""
    return llm_settings.resolve(vision=_has_image(content))


def run(system_prompt: str, user_content: List[Dict[str, Any]], output_model: Type[T],
        extra_tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: Optional[int] = None,
        sources_out: Optional[list] = None) -> T:
    route = resolve_route(user_content)
    if not route["api_key"]:
        raise RuntimeError(
            f"未配置 {route['provider_label']} 的 API Key，无法调用 "
            f"{llm_settings.label_of(route['model'])}。请在「模型设置」中填写。")
    module = _module_for(route)
    content = _translate(user_content, module)
    return module.run(system_prompt, content, output_model,
                      extra_tools=extra_tools, max_tokens=max_tokens,
                      sources_out=sources_out)


def parse_image_to_model(image_bytes: bytes, filename: str, system_prompt: str,
                         user_instruction: str, output_model: Type[T]) -> T:
    return run(system_prompt,
               [image_block(image_bytes, filename), text_block(user_instruction)],
               output_model)


def complete_to_model(system_prompt: str, user_prompt: str, output_model: Type[T],
                      **kwargs) -> T:
    return run(system_prompt, [text_block(user_prompt)], output_model, **kwargs)


def last_used_model() -> str | None:
    """本次请求**实际用掉**的模型，供留痕与审计。

    两条路径各自记录。没有记录就返回 None —— 审计宁可写"不可用"，
    也不能拿配置值顶替：那会让留痕看起来言之凿凿，实际并没有跑过。
    """
    from . import claude_client, qwen_client

    return qwen_client.last_used_model() or claude_client.last_used_model()


# --------------------------------------------------------------------------- #
# 能力差异
# --------------------------------------------------------------------------- #
# 只有 Anthropic / OpenAI 官方网关提供本项目使用的 hosted web search。
WEB_SEARCH_TOOL: Dict[str, Any] = {"type": "web_search_20260209", "name": "web_search",
                                   "max_uses": 5}


def web_search_available() -> bool:
    try:
        return llm_settings.resolve(vision=False)["provider"] in {"anthropic", "openai"}
    except Exception:                                   # pragma: no cover - 配置异常
        return False


# 兼容旧调用点：过去这是个常量。现在按当前选中的语言模型动态判断。
class _WebSearchAvailable:
    def __bool__(self) -> bool:
        return web_search_available()


WEB_SEARCH_AVAILABLE = _WebSearchAvailable()


def web_search_tools(enabled: bool):
    """返回当前提供商实际可用的联网工具，不能用时安全地返回空。

    这里读模块级的 WEB_SEARCH_AVAILABLE 而不是直接调 web_search_available()：
    调用点和测试历来把它当常量覆盖，绕过它就会出现"改了没用"。
    """
    return [WEB_SEARCH_TOOL] if enabled and bool(WEB_SEARCH_AVAILABLE) else None


def web_search_notice(enabled: bool) -> str:
    """返回给模型的能力边界说明，防止离线路径虚构检索来源。"""
    if enabled and bool(WEB_SEARCH_AVAILABLE):
        return "本次请求已启用联网检索；仅可引用实际检索到的公开来源。"
    return (
        "本次请求未启用联网检索。只能依据项目输入和通用工程知识给出建议；"
        "不得声称访问过网站、不得编造 URL、价格或外部标准出处；不确定项请写入待澄清项。"
    )

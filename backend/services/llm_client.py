"""模型提供商选择层；保持 Anthropic 原实现不变，按配置切换。

除统一导出各模型客户端外，这里还集中声明能力差异。调用方不能只因
``web=True`` 就把 hosted web-search 工具传给所有提供商：百炼的
Chat Completions 兼容接口并不支持该工具。统一在此处降级，可以避免
Qwen 用户在请求尚未发出时就得到失败任务。
"""
from __future__ import annotations

from ..config import LLM_PROVIDER

if LLM_PROVIDER == "anthropic":
    from .claude_client import *  # noqa: F401,F403
elif LLM_PROVIDER == "openai":
    from .openai_client import *  # noqa: F401,F403
elif LLM_PROVIDER == "qwen":
    from .qwen_client import *  # noqa: F401,F403
else:
    raise RuntimeError("LLM_PROVIDER 仅支持 anthropic、openai 或 qwen")


# Claude / OpenAI 路径提供本项目使用的 hosted web search；Qwen 当前的
# 百炼 Chat Completions 兼容接口没有这个能力。这个常量也让服务层能在
# 提示词中明确要求模型不要伪造“已联网”的来源。
WEB_SEARCH_AVAILABLE = LLM_PROVIDER in {"anthropic", "openai"}


def web_search_tools(enabled: bool):
    """返回当前提供商实际可用的联网工具，不能用时安全地返回空。"""
    return [WEB_SEARCH_TOOL] if enabled and WEB_SEARCH_AVAILABLE else None


def web_search_notice(enabled: bool) -> str:
    """返回给模型的能力边界说明，防止离线路径虚构检索来源。"""
    if enabled and WEB_SEARCH_AVAILABLE:
        return "本次请求已启用联网检索；仅可引用实际检索到的公开来源。"
    return (
        "本次请求未启用联网检索。只能依据项目输入和通用工程知识给出建议；"
        "不得声称访问过网站、不得编造 URL、价格或外部标准出处；不确定项请写入待澄清项。"
    )

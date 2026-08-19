"""模型输出的容错归一化。

为什么需要它：`assumptions`、`risks`、`open_questions` 这类字段声明成 `List[str]`，
但模型只想说一条时，经常直接给一个字符串：

    "assumptions": "材料按家电常规选…文档未给出牌号"      ← 而不是 [ "…" ]

Pydantic 会判 `list_type` 失败，于是**整份 IR 作废**。图纸解析里这尤其亏：零件、
尺寸、证据台账可能全都解析对了，只因为一句附注的容器类型不对就全丢，还白花一次
模型钱（`claude_client.run` 会带着报错重试一次，模型往往照旧给字符串）。

一条字符串等价于「一项」，语义上没有歧义，因此在校验前归一化，而不是让它失败。
注意这里**不做无中生有的补全**：`None` 归一成空列表，其余一律保留原信息。

用法：把字段类型从 `List[str]` 换成 `StrList`，其余不动。
`model_json_schema()` 仍然产出 `{"type": "array", "items": {"type": "string"}}`，
给模型看的 schema 没有变 —— 这是容错，不是放宽约定。
"""
from __future__ import annotations

from typing import Annotated, Any, List

from pydantic import BeforeValidator


def _item_to_str(item: Any) -> str:
    """把列表里的单项转成字符串。"""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        # 模型偶尔给 [{"assumption": "…"}] 这种包了一层的写法。字段类型是
        # List[str]，信息没有别处可放，因此摊平成可读文本而不是丢弃。
        pairs = [(key, value) for key, value in item.items()
                 if value not in (None, "", [], {})]
        if len(pairs) == 1:
            return str(pairs[0][1]).strip()
        return "；".join(f"{key}={value}" for key, value in pairs)
    if item is None:
        return ""
    return str(item).strip()


def as_str_list(value: Any) -> Any:
    """把常见的非列表写法归一成 List[str]；无法识别的原样放行让校验报错。"""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        return [text for text in (_item_to_str(item) for item in value) if text]
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    # dict 等其余类型不猜：原样交给 pydantic，让真正的结构错误照常暴露。
    return value


#: `List[str]` 的容错版本。语义、JSON schema 与 `List[str]` 完全一致。
StrList = Annotated[List[str], BeforeValidator(as_str_list)]

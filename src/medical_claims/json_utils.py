"""大模型结构化输出的轻量清洗工具。"""

from __future__ import annotations

import re


_OPENING_FENCE = re.compile(r"^\s*`{1,3}(?:json)?\s*", re.IGNORECASE)
_CLOSING_FENCE = re.compile(r"\s*`{1,3}\s*$")


def normalize_json_output(text: str) -> str:
    """把模型返回的 Markdown 包装清理为可交给 JSON 解析器的文本。

    DeepSeek 等模型偶尔会在 JSON Schema 输出外再包一层 `````json``
    代码围栏，甚至追加一句说明。这里仅截取最外层 JSON 对象；真正的字段和
    类型合法性仍由 Pydantic Schema 负责验证。
    """

    value = text.lstrip("\ufeff").strip()
    value = _OPENING_FENCE.sub("", value, count=1).strip()
    value = _CLOSING_FENCE.sub("", value, count=1).strip()

    object_start = value.find("{")
    object_end = value.rfind("}")
    if object_start == -1 or object_end < object_start:
        raise ValueError("DeepSeek 响应中没有找到 JSON 对象")
    return value[object_start : object_end + 1]

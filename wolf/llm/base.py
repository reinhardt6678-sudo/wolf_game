"""LLM 客户端的统一接口。"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMResult:
    text: str
    model: str
    usage: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    attempts: int = 1
    error: str | None = None
    stop_reason: str | None = None


class LLMError(RuntimeError):
    pass


class LLMClient(ABC):
    """一次请求 = 一个 system + 一个 user + 一个 JSON Schema。"""

    key: str
    model: str

    @abstractmethod
    def generate_json(
        self, *, system: str, user: str, schema: dict, max_tokens: int = 8000
    ) -> LLMResult: ...

    def describe(self) -> str:
        return f"{type(self).__name__}({self.model})"


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict:
    """从模型输出里抠出第一个 JSON 对象。

    结构化输出正常时 ``text`` 本身就是合法 JSON；这个函数是给
    不支持 json_schema 的本地端点兜底用的。
    """
    text = (text or "").strip()
    if not text:
        raise LLMError("模型返回了空字符串")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 扫描第一个配平的 {...}
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    start = -1
    raise LLMError(f"无法从模型输出中解析出 JSON：{text[:300]}")

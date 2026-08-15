"""LLM 客户端工厂。"""

from __future__ import annotations

from typing import Any

from .base import LLMClient, LLMError, LLMResult, extract_json

__all__ = ["LLMClient", "LLMError", "LLMResult", "extract_json", "build_client"]


def build_client(key: str, spec: dict[str, Any]) -> LLMClient:
    """按配置构造一个客户端。

    ``spec['provider']`` 取值：

    - ``anthropic``  → 官方 anthropic SDK（Claude）
    - ``openai``     → 任意 OpenAI 兼容端点（Ollama / vLLM / LM Studio / 第三方）
    """
    spec = dict(spec)
    provider = spec.pop("provider", "anthropic")
    if provider == "anthropic":
        from .anthropic_client import AnthropicClient

        return AnthropicClient(key=key, **spec)
    if provider in ("openai", "openai_compat", "ollama", "vllm", "lmstudio"):
        from .openai_client import OpenAICompatClient

        return OpenAICompatClient(key=key, **spec)
    raise LLMError(f"未知的 provider: {provider}")

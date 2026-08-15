"""Anthropic Claude 客户端（使用官方 anthropic SDK）。"""

from __future__ import annotations

import os
import random
import time
from typing import Any

from .base import LLMClient, LLMError, LLMResult

try:  # pragma: no cover - 取决于是否安装
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None  # type: ignore[assignment]

#: 不接受 temperature / top_p / top_k、且必须用自适应思考的模型前缀。
_ADAPTIVE_ONLY = ("claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-sonnet-5", "claude-fable-5")


class AnthropicClient(LLMClient):
    def __init__(
        self,
        *,
        key: str,
        model: str = "claude-opus-5",
        effort: str = "high",
        thinking: bool = True,
        max_tokens: int = 16000,
        api_key: str | None = None,
        base_url: str | None = None,
        max_retries: int = 4,
        fallbacks: bool = True,
        **_ignored: Any,
    ) -> None:
        if anthropic is None:
            raise LLMError("未安装 anthropic SDK，请先执行：pip install anthropic")
        self.key = key
        self.model = model
        self.effort = effort
        self.thinking = thinking
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self._use_fallbacks = fallbacks and model in ("claude-opus-5", "claude-fable-5")
        kwargs: dict[str, Any] = {}
        if api_key or os.environ.get("ANTHROPIC_API_KEY"):
            kwargs["api_key"] = api_key or os.environ["ANTHROPIC_API_KEY"]
        if base_url:
            kwargs["base_url"] = base_url
        # 不传 api_key 时 SDK 会自动读取环境变量或 `ant auth login` 的 profile。
        self.client = anthropic.Anthropic(**kwargs)

        if not self.thinking and self.effort in ("xhigh", "max"):
            # 关闭思考时 effort 上限是 high，否则 Claude Opus 5 会返回 400。
            self.effort = "high"

    # ------------------------------------------------------------------
    def _build_kwargs(self, system: str, user: str, schema: dict, max_tokens: int) -> dict:
        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": schema}
        }
        if self.effort:
            output_config["effort"] = self.effort
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            # 系统提示每局每人固定，打上缓存断点可以显著降低多轮对局的成本。
            "system": [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": [{"role": "user", "content": user}],
            "output_config": output_config,
        }
        if self.model.startswith(_ADAPTIVE_ONLY):
            kwargs["thinking"] = (
                {"type": "adaptive"} if self.thinking else {"type": "disabled"}
            )
        elif self.thinking:
            kwargs["thinking"] = {"type": "adaptive"}
        return kwargs

    def generate_json(
        self, *, system: str, user: str, schema: dict, max_tokens: int | None = None
    ) -> LLMResult:
        max_tokens = max_tokens or self.max_tokens
        kwargs = self._build_kwargs(system, user, schema, max_tokens)
        started = time.monotonic()
        last_err: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                msg = self._call(kwargs)
            except anthropic.RateLimitError as exc:  # 可重试
                last_err = exc
                delay = float(exc.response.headers.get("retry-after", 0) or 0)
                self._sleep(attempt, delay)
                continue
            except anthropic.APIConnectionError as exc:  # 网络问题，可重试
                last_err = exc
                self._sleep(attempt)
                continue
            except anthropic.APIStatusError as exc:
                if exc.status_code >= 500:  # 服务端问题，可重试
                    last_err = exc
                    self._sleep(attempt)
                    continue
                if self._use_fallbacks and "fallback" in str(exc).lower():
                    # 该账号/端点还不支持 server-side fallback，降级到标准通道重试。
                    self._use_fallbacks = False
                    continue
                raise LLMError(f"Anthropic 请求失败 [{exc.status_code}]: {exc}") from exc

            latency = int((time.monotonic() - started) * 1000)
            if msg.stop_reason == "refusal":
                cat = getattr(msg.stop_details, "category", None) if msg.stop_details else None
                raise LLMError(f"模型拒绝了这次请求（category={cat}）")
            text = "".join(b.text for b in msg.content if b.type == "text")
            usage = msg.usage
            return LLMResult(
                text=text,
                model=msg.model,
                usage={
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
                    "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
                },
                latency_ms=latency,
                attempts=attempt,
                stop_reason=msg.stop_reason,
            )

        raise LLMError(f"Anthropic 重试 {self.max_retries} 次后仍失败：{last_err}")

    def _call(self, kwargs: dict):
        """默认走流式，避免思考较长时触发 HTTP 超时。"""
        if self._use_fallbacks:
            # 安全分类器偶尔会误伤（狼人杀里的「刀」「杀」「毒」等词），
            # 开启服务端兜底后被拒的请求会自动改由备选模型完成。
            with self.client.beta.messages.stream(
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                **kwargs,
            ) as stream:
                return stream.get_final_message()
        with self.client.messages.stream(**kwargs) as stream:
            return stream.get_final_message()

    @staticmethod
    def _sleep(attempt: int, hint: float = 0.0) -> None:
        delay = hint or min(2 ** attempt, 30) + random.random()
        time.sleep(delay)

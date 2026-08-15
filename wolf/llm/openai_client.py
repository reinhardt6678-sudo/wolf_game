"""OpenAI 兼容端点客户端。

用于本地推理服务（Ollama / vLLM / LM Studio / llama.cpp server）以及
任何暴露 ``/v1/chat/completions`` 的第三方服务。

注意：这个文件刻意不引入 anthropic SDK —— Claude 走
:mod:`wolf.llm.anthropic_client`，两条通路互不混用。
"""

from __future__ import annotations

import json
import os
import random
import time
from typing import Any

import requests

from .base import LLMClient, LLMError, LLMResult


class OpenAICompatClient(LLMClient):
    def __init__(
        self,
        *,
        key: str,
        model: str,
        base_url: str = "http://localhost:11434/v1",
        api_key: str | None = None,
        api_key_env: str | None = None,
        max_tokens: int = 4096,
        temperature: float | None = 0.8,
        structured: str = "json_schema",  # json_schema | json_object | prompt
        timeout: int = 300,
        max_retries: int = 4,
        extra_body: dict | None = None,
        **_ignored: Any,
    ) -> None:
        self.key = key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or (os.environ.get(api_key_env) if api_key_env else None) or "sk-noauth"
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.structured = structured
        self.timeout = timeout
        self.max_retries = max_retries
        self.extra_body = extra_body or {}

    def _payload(self, system: str, user: str, schema: dict, max_tokens: int, mode: str) -> dict:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if mode == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.get("title", "action"),
                    "schema": schema,
                    "strict": True,
                },
            }
        elif mode == "json_object":
            body["response_format"] = {"type": "json_object"}
            body["messages"][0]["content"] += "\n\n必须严格按照以下 JSON Schema 输出：\n" + json.dumps(
                schema, ensure_ascii=False
            )
        else:  # prompt-only
            body["messages"][0]["content"] += "\n\n必须严格按照以下 JSON Schema 输出，只输出 JSON：\n" + json.dumps(
                schema, ensure_ascii=False
            )
        body.update(self.extra_body)
        return body

    def generate_json(
        self, *, system: str, user: str, schema: dict, max_tokens: int | None = None
    ) -> LLMResult:
        max_tokens = max_tokens or self.max_tokens
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        modes = [self.structured]
        # 很多本地端点不支持 json_schema，逐级降级。
        for fallback in ("json_object", "prompt"):
            if fallback not in modes:
                modes.append(fallback)

        started = time.monotonic()
        last_err: str = ""
        attempt = 0
        for mode in modes:
            for _ in range(self.max_retries):
                attempt += 1
                try:
                    resp = requests.post(
                        url,
                        headers=headers,
                        json=self._payload(system, user, schema, max_tokens, mode),
                        timeout=self.timeout,
                    )
                except requests.RequestException as exc:
                    last_err = f"网络错误: {exc}"
                    time.sleep(min(2**attempt, 20) + random.random())
                    continue
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    time.sleep(min(2**attempt, 20) + random.random())
                    continue
                if resp.status_code >= 400:
                    last_err = f"HTTP {resp.status_code}: {resp.text[:300]}"
                    break  # 该 mode 不被支持，换下一个 mode
                data = resp.json()
                choice = data["choices"][0]
                text = choice["message"].get("content") or ""
                usage = data.get("usage") or {}
                return LLMResult(
                    text=text,
                    model=data.get("model", self.model),
                    usage={
                        "input_tokens": usage.get("prompt_tokens", 0),
                        "output_tokens": usage.get("completion_tokens", 0),
                    },
                    latency_ms=int((time.monotonic() - started) * 1000),
                    attempts=attempt,
                    stop_reason=choice.get("finish_reason"),
                )
        raise LLMError(f"OpenAI 兼容端点请求失败（{self.base_url}, model={self.model}）：{last_err}")

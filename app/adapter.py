"""Model adapter boundary.

The service owns prompts, validation, retries and quotas. This module only
performs one model request and normalizes transport/provider failures.
"""

from __future__ import annotations

import json
import os
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import error as urllib_error
from urllib import request as urllib_request

from . import config
from .models import (
    AuthError,
    BadRequestError,
    ContentFilterError,
    ContextLengthError,
    InvalidJSONError,
    InvalidResponseError,
    ModelConfigError,
    ModelError,
    ModelRequest,
    ModelResponse,
    ModelTimeoutError,
    ModelUsage,
    RateLimitError,
    ServerError,
)


class ModelAdapter(Protocol):
    def generate_json(self, request: ModelRequest) -> ModelResponse: ...

    def healthcheck(self) -> bool: ...


class MockModelAdapter:
    """测试替身：按 mock_key 返回固定 JSON，不访问网络。"""

    def __init__(
        self,
        responses: Mapping[str, Mapping[str, Any] | ModelResponse] | None = None,
        errors: Mapping[str, BaseException] | None = None,
        *,
        model_name: str = "mock",
    ):
        self._responses = dict(responses or {})
        self._errors = dict(errors or {})
        self._model_name = model_name

    def generate_json(self, request: ModelRequest) -> ModelResponse:
        key = request.metadata.get("mock_key", request.request_id)
        if key in self._errors:
            raise self._errors[key]
        if key not in self._responses:
            raise InvalidResponseError(f"Mock response not found: {key}")

        payload = self._responses[key]
        if isinstance(payload, ModelResponse):
            return payload

        if not isinstance(payload, Mapping):
            raise InvalidResponseError(f"Mock response must be an object: {key}")

        content = json.dumps(payload, ensure_ascii=False)
        return ModelResponse(
            request_id=request.request_id,
            provider_request_id=None,
            model=self._model_name,
            content=content,
            parsed=payload,
            usage=ModelUsage(),
            finish_reason="stop",
            latency_ms=0,
        )

    def healthcheck(self) -> bool:
        return True


@dataclass(frozen=True)
class QwenConfig:
    base_url: str
    model: str
    api_key: str | None = None
    timeout_s: float = 60.0
    max_tokens: int = 4096
    enable_thinking: bool | None = None

    @classmethod
    def from_env(cls, *, require_api_key: bool = True) -> "QwenConfig":
        base_url = os.getenv(config.QWEN_BASE_URL_ENV, "").strip()
        model = os.getenv(config.QWEN_MODEL_ENV, "").strip()
        api_key = os.getenv(config.QWEN_API_KEY_ENV, "").strip() or None

        if not base_url:
            raise ModelConfigError(f"缺少环境变量 {config.QWEN_BASE_URL_ENV}")
        if not base_url.startswith(("http://", "https://")):
            raise ModelConfigError(f"{config.QWEN_BASE_URL_ENV} 必须是 http(s) URL")
        if not model:
            raise ModelConfigError(f"缺少环境变量 {config.QWEN_MODEL_ENV}")
        # DashScope 必须带 key；本地无鉴权网关可显式 require_api_key=False。
        if require_api_key and not api_key:
            raise ModelConfigError(f"缺少环境变量 {config.QWEN_API_KEY_ENV}")

        try:
            timeout_s = float(os.getenv(config.QWEN_TIMEOUT_ENV, str(60.0)))
            max_tokens = int(os.getenv(config.QWEN_MAX_TOKENS_ENV, "4096"))
        except ValueError as exc:
            raise ModelConfigError("QWEN_TIMEOUT_S / QWEN_MAX_TOKENS 必须是数字") from exc

        if timeout_s <= 0:
            raise ModelConfigError("QWEN_TIMEOUT_S 必须大于 0")
        if max_tokens <= 0:
            raise ModelConfigError("QWEN_MAX_TOKENS 必须大于 0")

        thinking_raw = os.getenv(config.QWEN_ENABLE_THINKING_ENV, "").strip()
        enable_thinking: bool | None = None
        if thinking_raw:
            lowered = thinking_raw.lower()
            if lowered in {"1", "true", "yes", "on"}:
                enable_thinking = True
            elif lowered in {"0", "false", "no", "off"}:
                enable_thinking = False
            else:
                raise ModelConfigError(
                    f"{config.QWEN_ENABLE_THINKING_ENV} 只支持 true/false"
                )

        return cls(
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout_s=timeout_s,
            max_tokens=max_tokens,
            enable_thinking=enable_thinking,
        )


class QwenAdapter:
    """Minimal OpenAI-compatible Qwen adapter using only the standard library."""

    def __init__(self, config_: QwenConfig):
        self.config = config_
        self._url = self._chat_url(config_.base_url)

    @staticmethod
    def _chat_url(base_url: str) -> str:
        base = base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def _build_payload(self, request: ModelRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.json_schema is not None:
            # 只要求 provider 返回 JSON object；真正的完整 Schema 校验在本地做，
            # 避免依赖 provider 对 JSON Schema 子集的支持。
            payload["response_format"] = {"type": "json_object"}
        if self.config.enable_thinking is not None:
            payload["enable_thinking"] = self.config.enable_thinking
        return payload

    def generate_json(self, request: ModelRequest) -> ModelResponse:
        body = json.dumps(self._build_payload(request), ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        http_request = urllib_request.Request(
            self._url,
            data=body,
            headers=headers,
            method="POST",
        )
        # 未在任务里指定超时就用 QWEN_TIMEOUT_S，避免默认 60 秒覆盖配置的 120 秒。
        timeout_s = self.config.timeout_s if request.timeout_s is None else request.timeout_s
        started = time.monotonic()

        try:
            with urllib_request.urlopen(http_request, timeout=timeout_s) as response:
                raw = response.read()
                status = getattr(response, "status", 200)
        except urllib_error.HTTPError as exc:
            raise self._map_http_error(exc) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise ModelTimeoutError("模型请求超时") from exc
        except urllib_error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise ModelTimeoutError("模型请求超时") from exc
            raise ServerError("模型网络错误", details={"reason": str(exc.reason)}) from exc
        except OSError as exc:
            raise ServerError("模型连接失败", details={"reason": str(exc)}) from exc

        latency_ms = int((time.monotonic() - started) * 1000)

        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidResponseError(
                "模型响应不是合法 JSON",
                details={"status": status},
            ) from exc

        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise InvalidResponseError("模型响应缺少 choices[0].message.content") from exc

        if not isinstance(content, str):
            raise InvalidResponseError("模型 content 不是字符串")

        usage_data = data.get("usage") or {}
        usage = ModelUsage(
            prompt_tokens=int(usage_data.get("prompt_tokens") or 0),
            completion_tokens=int(usage_data.get("completion_tokens") or 0),
            total_tokens=int(usage_data.get("total_tokens") or 0),
        )

        parsed = self._parse_json_content(content)
        return ModelResponse(
            request_id=request.request_id,
            provider_request_id=data.get("id"),
            model=data.get("model") or self.config.model,
            content=content,
            parsed=parsed,
            usage=usage,
            finish_reason=choice.get("finish_reason"),
            latency_ms=latency_ms,
        )

    @staticmethod
    def _parse_json_content(content: str) -> Mapping[str, Any]:
        text = content.strip()
        if not text:
            raise InvalidJSONError("模型返回空内容")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InvalidJSONError("模型返回内容不是合法 JSON") from exc
        if not isinstance(parsed, Mapping):
            raise InvalidJSONError("模型返回的 JSON 必须是对象")
        return parsed

    def _map_http_error(self, exc: urllib_error.HTTPError) -> ModelError:
        # 把 provider 的 HTTP 错误归一成固定异常，编排层只按异常类型决定是否重试。
        status = exc.code
        try:
            body = exc.read().decode("utf-8", errors="replace")[:2000]
        except Exception:
            body = ""
        details = {"status": status, "body": body}

        if status in (401, 403):
            return AuthError("模型鉴权失败", details=details)
        if status in (400, 404):
            return BadRequestError("模型请求参数错误", details=details)
        if status == 408:
            return ModelTimeoutError("模型请求超时", details=details)
        if status == 429:
            retry_after = None
            header = exc.headers.get("Retry-After") if exc.headers else None
            if header:
                try:
                    retry_after = float(header)
                except ValueError:
                    retry_after = None
            return RateLimitError("模型触发限流", retry_after_s=retry_after, details=details)
        if status >= 500:
            return ServerError("模型服务端错误", details=details)
        return BadRequestError("模型请求失败", details=details)

    def healthcheck(self) -> bool:
        return bool(self.config.base_url and self.config.model)

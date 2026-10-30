"""Shared dataclasses and error types.

Keep these structures dependency-free so the adapter and input layers can be
tested before FastAPI/jsonschema are installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


class InputValidationError(ValueError):
    """Request body does not match the Stage 0 input contract."""

    code = "INVALID_REQUEST"

    def __init__(self, message: str, *, field: str | None = None, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.field = field
        self.details = dict(details or {})


class ModelConfigError(ValueError):
    """Model adapter configuration is incomplete or invalid."""


class ModelError(RuntimeError):
    """Base class for normalized model-provider errors."""

    code = "MODEL_ERROR"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        retry_after_s: float | None = None,
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.retry_after_s = retry_after_s
        self.details = dict(details or {})


class AuthError(ModelError):
    code = "MODEL_AUTH"


class BadRequestError(ModelError):
    code = "MODEL_BAD_REQUEST"


class ContextLengthError(ModelError):
    code = "MODEL_CONTEXT_LENGTH"


class RateLimitError(ModelError):
    code = "MODEL_RATE_LIMIT"
    retryable = True


class ModelTimeoutError(ModelError):
    code = "MODEL_TIMEOUT"
    retryable = True


class ServerError(ModelError):
    code = "MODEL_ERROR"
    retryable = True


class ContentFilterError(ModelError):
    code = "MODEL_CONTENT_FILTER"


class InvalidJSONError(ModelError):
    code = "INVALID_JSON"
    retryable = True


class InvalidResponseError(ModelError):
    code = "INVALID_RESPONSE"
    retryable = True


@dataclass(frozen=True)
class InputDocument:
    """Validated request payload."""

    request_id: str
    template_id: str
    schema_version: str
    file_context: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n".join(self.file_context)


@dataclass(frozen=True)
class SourceRef:
    """A contiguous quote in one fileContext item."""

    file_context_index: int
    char_start: int
    char_end: int
    quote: str


@dataclass(frozen=True)
class Subject:
    """One party in the shared subject index."""

    subject_id: str
    role: str
    entity_type: str
    name: str | None
    aliases: tuple[str, ...]
    output_pointer: str
    source_refs: tuple[SourceRef, ...]
    principal_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelRequest:
    request_id: str
    system_prompt: str
    user_prompt: str
    json_schema: Mapping[str, Any] | None = None
    temperature: float = 0.0
    max_tokens: int = 4096
    # 为空时由适配层使用 QWEN_TIMEOUT_S；显式传入才覆盖 provider 超时。
    timeout_s: float | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class ModelResponse:
    request_id: str
    provider_request_id: str | None
    model: str
    content: str
    parsed: Mapping[str, Any] | None
    usage: ModelUsage
    finish_reason: str | None
    latency_ms: int

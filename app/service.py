"""应用服务层：输入校验、pipeline、错误映射和响应包装。

这一层不依赖 FastAPI，便于直接测试；API 层只负责 HTTP 适配。
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from . import config
from .adapter import ModelAdapter
from .input import parse_request
from .models import InputValidationError, ModelError
from .pipeline import ExtractionPipeline, PipelineError
from .subject_index import SubjectIndexError
from .validation import ResultValidator


@dataclass(frozen=True)
class ServiceResponse:
    http_status: int
    body: dict[str, Any]


def _empty_review() -> dict[str, Any]:
    # 错误响应也要保持 review 的固定结构，方便调用方统一处理。
    return {
        "subjectIndex": [],
        "evidence": [],
        "conflicts": [],
        "missing": [],
        "warnings": [],
    }


def _error_body(
    request_id: str | None,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: Mapping[str, Any] | None = None,
    review: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "requestId": request_id,
        "status": "error",
        "data": None,
        "review": dict(review) if review is not None else _empty_review(),
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": dict(details or {}),
        },
    }


def _http_status_for_model(error: ModelError) -> int:
    # 把适配层错误映射成 API 契约中的 HTTP 状态。
    return {
        "MODEL_AUTH": 401,
        "MODEL_RATE_LIMIT": 429,
        "MODEL_TIMEOUT": 504,
        "MODEL_ERROR": 502,
        "INVALID_JSON": 502,
        "INVALID_RESPONSE": 502,
        "MODEL_BAD_REQUEST": 400,
        "MODEL_CONTEXT_LENGTH": 422,
        "MODEL_CONTENT_FILTER": 422,
    }.get(error.code, 502)


def _http_status_for_result_error(code: str) -> int:
    if code in {"NO_PARTIES", "EMPTY_PARTY", "SCHEMA_INVALID"}:
        return 422
    return 422


class ExtractionService:
    """一次请求的编排入口，复用同一个 pipeline 的线程池和全局配额。"""

    def __init__(
        self,
        adapter: ModelAdapter,
        *,
        max_workers: int = config.WORKERS_DEFAULT,
        validator: ResultValidator | None = None,
    ):
        self._validator = validator or ResultValidator()
        self._pipeline = ExtractionPipeline(
            adapter,
            max_workers=max_workers,
            validator=self._validator,
        )
        # 有界请求槽：超出容量的请求在进入模型前等待，避免无限堆积。
        self._request_slots = threading.BoundedSemaphore(config.QUEUE_CAPACITY)

    def close(self) -> None:
        self._pipeline.close()

    def extract(self, payload: Mapping[str, Any]) -> ServiceResponse:
        request_id = payload.get("requestId") if isinstance(payload, Mapping) else None
        if not isinstance(request_id, str):
            request_id = None

        try:
            document = parse_request(payload)
        except InputValidationError as exc:
            return ServiceResponse(
                400,
                _error_body(
                    request_id,
                    exc.code,
                    exc.message,
                    details={**exc.details, **({"field": exc.field} if exc.field else {})},
                ),
            )

        try:
            # 请求槽只包住 pipeline，不包住输入校验，避免明显非法请求占坑。
            with self._request_slots:
                result = self._pipeline.run(document)
        except SubjectIndexError as exc:
            return ServiceResponse(
                422,
                _error_body(
                    document.request_id,
                    exc.code,
                    exc.message,
                    retryable=False,
                    details=exc.details,
                ),
            )
        except ModelError as exc:
            return ServiceResponse(
                _http_status_for_model(exc),
                _error_body(
                    document.request_id,
                    exc.code,
                    exc.message,
                    retryable=exc.retryable,
                    details=exc.details,
                ),
            )
        except PipelineError as exc:
            return ServiceResponse(
                422,
                _error_body(
                    document.request_id,
                    "PIPELINE_ERROR",
                    exc.message,
                    retryable=False,
                    details=exc.details,
                ),
            )
        except Exception as exc:  # 兜底：不把内部异常伪装成业务缺失
            return ServiceResponse(
                500,
                _error_body(
                    document.request_id,
                    "INTERNAL",
                    "内部错误",
                    retryable=False,
                    details={"type": type(exc).__name__},
                ),
            )

        if result.status == "error":
            error = result.error or {
                "code": "INTERNAL",
                "message": "抽取结果不可用",
                "details": {},
            }
            return ServiceResponse(
                _http_status_for_result_error(str(error.get("code", "INTERNAL"))),
                _error_body(
                    document.request_id,
                    str(error.get("code", "INTERNAL")),
                    str(error.get("message", "抽取结果不可用")),
                    retryable=bool(error.get("retryable", False)),
                    details=error.get("details") or {},
                    review=result.review,
                ),
            )

        # ok / needs_review：都返回业务 data，由调用方根据 status 决定是否人工复核。
        return ServiceResponse(
            200,
            {
                "requestId": document.request_id,
                "status": result.status,
                "data": result.data,
                "review": result.review,
                "error": None,
            },
        )

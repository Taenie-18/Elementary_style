"""FastAPI 适配层：只负责 HTTP 请求/响应，业务在 service/pipeline。"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import config
from .adapter import QwenAdapter, QwenConfig
from .service import ExtractionService, _empty_review, _error_body


def _build_default_service() -> ExtractionService:
    """按环境变量创建真实 Qwen 服务；未配置时明确报错，不偷偷使用假数据。"""

    if not (
        os.getenv(config.QWEN_BASE_URL_ENV)
        and os.getenv(config.QWEN_MODEL_ENV)
    ):
        raise RuntimeError(
            f"未配置 {config.QWEN_BASE_URL_ENV}/{config.QWEN_MODEL_ENV}；"
            "请先设置 Qwen3.8 API 环境变量"
        )
    adapter = QwenAdapter(QwenConfig.from_env())
    return ExtractionService(adapter)


def create_app(service: ExtractionService | None = None, *, shutdown_close: bool = True) -> FastAPI:
    """创建 FastAPI 应用；测试可以注入使用 MockModelAdapter 的 service。"""

    app = FastAPI(
        title="民间借贷要素式起诉状抽取服务",
        version=config.SCHEMA_VERSION,
    )
    service_holder: dict[str, ExtractionService | None] = {"service": service}
    service_lock = threading.Lock()

    def get_service() -> ExtractionService:
        # 延迟初始化：导入 app.api 时不需要 Qwen 配置，首次请求才创建。
        current = service_holder["service"]
        if current is not None:
            return current
        with service_lock:
            current = service_holder["service"]
            if current is None:
                current = _build_default_service()
                service_holder["service"] = current
            return current

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/extract")
    def extract(payload: dict[str, Any]) -> JSONResponse:
        try:
            app_service = get_service()
        except Exception as exc:
            # 配置错误不暴露密钥或内部路径，只返回稳定的错误码。
            request_id = payload.get("requestId") if isinstance(payload, Mapping) else None
            return JSONResponse(
                status_code=500,
                content=_error_body(
                    request_id if isinstance(request_id, str) else None,
                    "MODEL_CONFIG",
                    str(exc),
                    details={"type": type(exc).__name__},
                    review=_empty_review(),
                ),
            )

        # service.extract 内部已完成输入校验、抽取、合并、Schema 和业务校验。
        response = app_service.extract(payload)
        return JSONResponse(status_code=response.http_status, content=response.body)

    if shutdown_close:
        @app.on_event("shutdown")
        def _shutdown() -> None:
            current = service_holder["service"]
            if current is not None:
                current.close()

    return app


# uvicorn 入口：uvicorn app.api:app
app = create_app()

"""Input contract validation for POST /v1/extract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import config
from .models import InputDocument, InputValidationError

_ALLOWED_KEYS = {"requestId", "templateId", "schemaVersion", "fileContext"}


def parse_request(payload: Mapping[str, Any]) -> InputDocument:
    """Validate a Stage 0 request body and return an immutable document.

    Raises InputValidationError with a stable code/field for the API layer.
    """

    # 请求体只允许四个字段；多字段/缺字段都在入口拒绝，避免脏数据进入模型。
    if not isinstance(payload, Mapping):
        raise InputValidationError("请求体必须是 JSON 对象")

    extra = set(payload) - _ALLOWED_KEYS
    missing = _ALLOWED_KEYS - set(payload)
    if missing:
        raise InputValidationError(
            f"缺少必需字段: {', '.join(sorted(missing))}",
            field=sorted(missing)[0],
            details={"missing": sorted(missing)},
        )
    if extra:
        raise InputValidationError(
            f"不允许的字段: {', '.join(sorted(extra))}",
            field=sorted(extra)[0],
            details={"extra": sorted(extra)},
        )

    request_id = payload["requestId"]
    if not isinstance(request_id, str) or not request_id.strip():
        raise InputValidationError("requestId 必须是非空字符串", field="requestId")
    if len(request_id) > 128:
        raise InputValidationError("requestId 长度不能超过 128", field="requestId")

    template_id = payload["templateId"]
    if template_id != config.TEMPLATE_ID:
        raise InputValidationError(
            f"templateId 只支持 {config.TEMPLATE_ID}",
            field="templateId",
        )

    schema_version = payload["schemaVersion"]
    if schema_version != config.SCHEMA_VERSION:
        raise InputValidationError(
            f"schemaVersion 只支持 {config.SCHEMA_VERSION}",
            field="schemaVersion",
        )

    raw_context = payload["fileContext"]
    if not isinstance(raw_context, list):
        raise InputValidationError("fileContext 必须是字符串数组", field="fileContext")
    if not (1 <= len(raw_context) <= config.MAX_FILE_CONTEXT_ITEMS):
        raise InputValidationError(
            f"fileContext 项数必须在 1-{config.MAX_FILE_CONTEXT_ITEMS} 之间",
            field="fileContext",
            details={"count": len(raw_context)},
        )

    items: list[str] = []
    total = 0
    for index, item in enumerate(raw_context):
        if not isinstance(item, str):
            raise InputValidationError(
                "fileContext 的每一项都必须是字符串",
                field="fileContext",
                details={"index": index},
            )
        if not item.strip():
            raise InputValidationError(
                "fileContext 不允许空白项",
                field="fileContext",
                details={"index": index},
            )
        if len(item) > config.MAX_ITEM_CHARS:
            raise InputValidationError(
                f"fileContext 单项不能超过 {config.MAX_ITEM_CHARS} 字符",
                field="fileContext",
                details={"index": index, "length": len(item)},
            )
        total += len(item)
        items.append(item)

    if total > config.MAX_TOTAL_CHARS:
        raise InputValidationError(
            f"fileContext 总字符数不能超过 {config.MAX_TOTAL_CHARS}",
            field="fileContext",
            details={"length": total},
        )

    return InputDocument(
        request_id=request_id,
        template_id=template_id,
        schema_version=schema_version,
        file_context=tuple(items),
    )

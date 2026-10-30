"""Task orchestration and merge for the MVP extraction pipeline."""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from . import config
from .adapter import ModelAdapter
from .models import (
    InputDocument,
    InvalidJSONError,
    InvalidResponseError,
    ModelRequest,
    ModelTimeoutError,
    RateLimitError,
    ServerError,
    Subject,
)
from .normalize import load_schema, normalize_merged, normalize_task_data
from .subject_index import build_subject_index
from .tasks import (
    TASK_OUTPUT_SCHEMA,
    TASK_SYSTEM_PROMPT,
    TaskSpec,
    build_task_prompt,
    build_task_specs,
)
from .validation import ResultValidator


class PipelineError(RuntimeError):
    code = "PIPELINE_ERROR"

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})


@dataclass(frozen=True)
class PipelineResult:
    data: dict[str, Any]
    review: dict[str, Any]
    subjects: tuple[Subject, ...]
    status: str = "ok"
    error: dict[str, Any] | None = None


def _subject_to_dict(subject: Subject) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "subjectId": subject.subject_id,
        "role": subject.role,
        "entityType": subject.entity_type,
        "name": subject.name,
        "aliases": list(subject.aliases),
        "outputPointer": subject.output_pointer,
        "sourceRefs": [
            {
                "fileContextIndex": source.file_context_index,
                "charStart": source.char_start,
                "charEnd": source.char_end,
                "quote": source.quote,
            }
            for source in subject.source_refs
        ],
    }
    if subject.principal_ids:
        payload["principalIds"] = list(subject.principal_ids)
    return payload


def _resolve_pointer(data: Any, path: str) -> Any:
    """Resolve a concrete JSON pointer; wildcard paths are not evidence paths."""

    if not isinstance(path, str) or not path.startswith("/") or "*" in path:
        raise KeyError(path)
    current = data
    for raw_part in path.split("/")[1:]:
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, Mapping):
            current = current[part]
        else:
            raise KeyError(path)
    return current


def _resolve_wildcard_pointer(data: Any, path: str) -> tuple[str, Any]:
    """把唯一元素数组的 * 路径落成具体索引，例如 /*/ -> /0/。

    Qwen 有时用 `/*/` 写 evidence；如果数组有多个元素，无法可靠判断是哪一个，
    仍交给上层报 EVIDENCE_PATH_INVALID，不猜测。
    """

    current = data
    concrete_parts: list[str] = []
    for raw_part in path.split("/")[1:]:
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if part == "*":
            if not isinstance(current, list) or len(current) != 1:
                raise KeyError(path)
            part = "0"
        concrete_parts.append(part)
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, Mapping):
            current = current[part]
        else:
            raise KeyError(path)
    return "/" + "/".join(concrete_parts), current


def _quote_variants(quote: str) -> list[str]:
    """模型可能把中文日期写成 ISO；证据引文允许在这两种格式间尝试。"""

    variants: list[str] = []
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", quote)
    if iso:
        year, month, day = iso.groups()
        variants.append(f"{year}年{int(month)}月{int(day)}日")
        variants.append(f"{year}年{month}月{day}日")
    chinese = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", quote)
    if chinese:
        year, month, day = chinese.groups()
        variants.append(f"{year}-{int(month):02d}-{int(day):02d}")
    return [item for item in variants if item != quote]


def _warning(
    code: str,
    message: str,
    *,
    field_path: str | None = None,
    subject_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": code,
        "message": message,
        "severity": "warning",
    }
    if field_path is not None:
        payload["fieldPath"] = field_path
    if subject_id is not None:
        payload["subjectId"] = subject_id
    return payload


def _normalize_evidence(
    document: InputDocument,
    data: Mapping[str, Any],
    subjects: tuple[Subject, ...],
    raw_evidence: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Turn model evidence quotes into review-schema evidence with offsets."""

    subject_ids = {subject.subject_id for subject in subjects}
    evidence: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    for index, raw in enumerate(raw_evidence):
        if not isinstance(raw, Mapping):
            warnings.append(_warning("EVIDENCE_INVALID", f"evidence[{index}] 不是对象"))
            continue

        field_path = raw.get("fieldPath")
        if not isinstance(field_path, str) or not field_path.startswith("/"):
            warnings.append(_warning("EVIDENCE_PATH_INVALID", f"evidence[{index}].fieldPath 非法"))
            continue

        had_wildcard = "*" in field_path
        try:
            if had_wildcard:
                # 只有数组长度为 1 时才可把 * 安全落成 /0；多元素数组不猜测。
                field_path, _ = _resolve_wildcard_pointer(data, field_path)
            else:
                _resolve_pointer(data, field_path)
        except (KeyError, IndexError, TypeError, ValueError):
            code = "EVIDENCE_PATH_INVALID" if had_wildcard else "EVIDENCE_PATH_NOT_FOUND"
            message = (
                f"evidence[{index}] 的通配路径无法唯一确定"
                if had_wildcard
                else f"evidence[{index}] 指向的字段不在结果中"
            )
            warnings.append(_warning(code, message, field_path=field_path))
            continue

        raw_subject_id = raw.get("subjectId")
        subject_id: str | None = None
        if raw_subject_id is not None:
            if not isinstance(raw_subject_id, str) or raw_subject_id not in subject_ids:
                warnings.append(
                    _warning(
                        "UNKNOWN_SUBJECT_ID",
                        f"evidence[{index}].subjectId 不在主体索引中",
                        field_path=field_path,
                    )
                )
            else:
                subject_id = raw_subject_id

        quote = raw.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            warnings.append(
                _warning(
                    "EVIDENCE_QUOTE_EMPTY",
                    f"evidence[{index}].quote 为空",
                    field_path=field_path,
                    subject_id=subject_id,
                )
            )
            continue
        quote = quote.strip()

        source_index: int | None = None
        char_start: int | None = None
        for candidate in [quote, *_quote_variants(quote)]:
            for context_index, text in enumerate(document.file_context):
                position = text.find(candidate)
                if position >= 0:
                    quote = candidate
                    source_index = context_index
                    char_start = position
                    break
            if source_index is not None:
                break
        if source_index is None or char_start is None:
            warnings.append(
                _warning(
                    "EVIDENCE_QUOTE_NOT_FOUND",
                    f"evidence[{index}].quote 在输入文本中找不到",
                    field_path=field_path,
                    subject_id=subject_id,
                )
            )
            continue

        kind = raw.get("kind", "explicit")
        if kind not in {"explicit", "negative", "derived"}:
            warnings.append(
                _warning(
                    "EVIDENCE_KIND_INVALID",
                    f"evidence[{index}].kind 非法，已按 explicit 处理",
                    field_path=field_path,
                    subject_id=subject_id,
                )
            )
            kind = "explicit"

        confidence = raw.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            confidence = None
        elif not 0 <= float(confidence) <= 1:
            confidence = None

        key = (field_path, subject_id, source_index, char_start, len(quote))
        if key in seen:
            continue
        seen.add(key)

        item: dict[str, Any] = {
            "fieldPath": field_path,
            "subjectId": subject_id,
            "fileContextIndex": source_index,
            "charStart": char_start,
            "charEnd": char_start + len(quote),
            "quote": quote,
            "kind": kind,
        }
        if confidence is not None:
            item["confidence"] = confidence
        evidence.append(item)

    return evidence, warnings


class ExtractionPipeline:
    """Runs one subject-index call followed by five parallel extraction tasks."""

    def __init__(
        self,
        adapter: ModelAdapter,
        *,
        max_workers: int = 5,
        validator: ResultValidator | None = None,
    ):
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        self.adapter = adapter
        self.max_workers = max_workers
        self._validator = validator
        self._schema = load_schema()
        self._tasks: tuple[TaskSpec, ...] = build_task_specs()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="extract")
        self._quota = threading.BoundedSemaphore(max_workers)
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._closed = True

    def __enter__(self) -> "ExtractionPipeline":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _call_model(self, request: ModelRequest):
        with self._quota:
            return self.adapter.generate_json(request)

    def _build_subject_index(self, document: InputDocument) -> tuple[Subject, ...]:
        # 主体索引也属于模型调用，格式/超时/限流错误同样有限重试一次。
        for attempt in range(config.MODEL_RETRY_LIMIT + 1):
            try:
                return build_subject_index(
                    self.adapter,
                    document,
                    mock_key=f"{document.request_id}:subject_index",
                )
            except (
                InvalidJSONError,
                InvalidResponseError,
                ModelTimeoutError,
                RateLimitError,
                ServerError,
            ):
                if attempt >= config.MODEL_RETRY_LIMIT:
                    raise
                continue
        raise InvalidResponseError("主体索引重试后仍未得到有效结果")

    def _run_task(
        self,
        task: TaskSpec,
        document: InputDocument,
        subjects: tuple[Subject, ...],
    ) -> tuple[dict[str, Any], list[Any]]:
        request = ModelRequest(
            request_id=f"{document.request_id}:{task.name}",
            system_prompt=TASK_SYSTEM_PROMPT,
            user_prompt=build_task_prompt(task, document, subjects),
            json_schema=TASK_OUTPUT_SCHEMA,
            metadata={
                "phase": task.name,
                "mock_key": f"{document.request_id}:{task.name}",
            },
        )
        # 只对格式/限流/超时/服务端错误做有限重试；业务缺失和鉴权错误不重试。
        for attempt in range(config.MODEL_RETRY_LIMIT + 1):
            try:
                response = self._call_model(request)
                payload = response.parsed
                if not isinstance(payload, Mapping):
                    raise InvalidResponseError(f"{task.name} 返回的不是 JSON 对象")
                data = payload.get("data")
                if not isinstance(data, Mapping):
                    raise InvalidResponseError(f"{task.name} 缺少 data 对象")
                evidence = payload.get("evidence", [])
                if not isinstance(evidence, list):
                    evidence = []
                # 先归一化单个任务输出：展开扁平路径、把 {"*": ...} 转数组、补 category。
                return normalize_task_data(data, self._schema), evidence
            except (
                InvalidJSONError,
                InvalidResponseError,
                ModelTimeoutError,
                RateLimitError,
                ServerError,
            ):
                if attempt >= config.MODEL_RETRY_LIMIT:
                    raise
                # 保持 MVP 简单：立即重试一次，不在任务线程里长时间 sleep。
                continue

        raise InvalidResponseError(f"{task.name} 重试后仍未得到有效结果")

    @staticmethod
    def _merge(base: dict[str, Any], incoming: Mapping[str, Any], path: str = "") -> None:
        # 任务输出是局部嵌套对象，按路径深合并；同一路径值冲突时直接报错，不静默覆盖。
        for key, value in incoming.items():
            field_path = f"{path}/{key}"
            if key not in base or base[key] is None:
                base[key] = value
            elif isinstance(base[key], dict) and isinstance(value, Mapping):
                ExtractionPipeline._merge(base[key], value, field_path)
            elif base[key] == value:
                continue
            else:
                raise PipelineError(
                    "任务合并冲突",
                    details={"fieldPath": field_path, "left": base[key], "right": value},
                )

    def run(self, document: InputDocument) -> PipelineResult:
        if self._closed:
            raise RuntimeError("pipeline is closed")

        # 先建立一次共享主体索引，后续 5 个任务都复用 P1/P2，避免各任务各自猜主体。
        subjects = self._build_subject_index(document)

        # 5 个任务提交到同一个有界线程池；模型调用前再由 _call_model 获取全局配额。
        future_map: dict[Future[tuple[dict[str, Any], list[Any]]], TaskSpec] = {
            self._executor.submit(self._run_task, task, document, subjects): task
            for task in self._tasks
        }

        partials: list[Mapping[str, Any]] = []
        raw_evidence: list[Any] = []
        try:
            for future in as_completed(future_map):
                try:
                    data, task_evidence = future.result()
                except Exception:
                    for pending in future_map:
                        pending.cancel()
                    raise
                partials.append(data)
                raw_evidence.extend(task_evidence)
        finally:
            # The context manager waits for running tasks; this is enough for
            # the MVP because the executor is shared by the pipeline.
            pass

        # 各任务字段互不重叠，按完成顺序深合并即可；字段冲突会抛 PipelineError。
        merged: dict[str, Any] = {}
        for partial in partials:
            self._merge(merged, partial)

        # 合并后补齐固定对象/叶子、统一金额/枚举格式和当事人结构，再进入业务校验。
        normalization_issues: list[dict[str, Any]] = []
        merged = normalize_merged(merged, self._schema, normalization_issues)

        # 把模型返回的 evidence 引文换算成可审计的 fileContext 字符区间。
        evidence, warnings = _normalize_evidence(document, merged, subjects, raw_evidence)
        review = {
            "subjectIndex": [_subject_to_dict(subject) for subject in subjects],
            "evidence": evidence,
            "conflicts": [],
            "missing": [],
            "warnings": warnings + normalization_issues,
        }
        if self._validator is not None:
            outcome = self._validator.validate(merged, review)
            return PipelineResult(
                data=merged,
                review=outcome.review,
                subjects=subjects,
                status=outcome.status,
                error=outcome.error,
            )
        return PipelineResult(data=merged, review=review, subjects=subjects)

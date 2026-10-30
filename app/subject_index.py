"""Shared subject index: one model call before the parallel extraction tasks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .adapter import ModelAdapter
from .models import InputDocument, ModelRequest, SourceRef, Subject

_ROLES = ("原告", "被告", "第三人")
_ENTITY_TYPES = ("自然人", "法人、非法人组织")
_ROLE_POINTERS = {"原告": "/原告", "被告": "/被告", "第三人": "/第三人"}


class SubjectIndexError(RuntimeError):
    """Subject index is malformed or not usable."""

    def __init__(self, code: str, message: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})


SUBJECT_INDEX_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["subjects"],
    "additionalProperties": False,
    "properties": {
        "subjects": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["role", "entityType", "name", "aliases", "quote"],
                "additionalProperties": False,
                "properties": {
                    "role": {"enum": list(_ROLES)},
                    "entityType": {"enum": list(_ENTITY_TYPES)},
                    "name": {"type": ["string", "null"], "minLength": 1},
                    "aliases": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "uniqueItems": True,
                    },
                    "quote": {"type": "string", "minLength": 1},
                },
            },
        }
    },
}

SUBJECT_INDEX_SYSTEM_PROMPT = """你是民间借贷要素式起诉状的主体识别器。
只依据用户提供的文本识别原告、被告、第三人，不补充、不推测、不合并不同主体。
返回一个 JSON 对象，不要输出 markdown，不要输出解释。
每个主体必须有 role、entityType、name、aliases、quote。
quote 必须是输入文本中的连续原文片段，优先使用包含姓名和角色的片段。
无法确定的 name 用 null；不要把“原告”“被告”当成姓名。"""


def _user_prompt(document: InputDocument) -> str:
    blocks = []
    for index, text in enumerate(document.file_context):
        blocks.append(f"[{index}]\n{text}")
    return (
        "请识别下面文本中的当事人。\n"
        "返回格式：\n"
        '{"subjects":[{"role":"原告|被告|第三人","entityType":"自然人|法人、非法人组织",'
        '"name":"姓名或名称或 null","aliases":["原告","原告公司"],"quote":"原文片段"}]}\n'
        "要求：\n"
        "1. 只识别实际出现的主体，不要为空白模板创建主体；\n"
        "2. 多个主体分别列出；\n"
        "3. aliases 写文本中使用的称谓/简称；\n"
        "4. quote 必须能在输入文本中逐字找到；\n"
        "5. 至少识别出一个原告和一个被告。\n\n"
        + "\n\n".join(blocks)
    )


def _find_source_ref(document: InputDocument, quote: str) -> SourceRef | None:
    for index, text in enumerate(document.file_context):
        start = text.find(quote)
        if start >= 0:
            return SourceRef(
                file_context_index=index,
                char_start=start,
                char_end=start + len(quote),
                quote=quote,
            )
    return None


def _normalize_subject(raw: Mapping[str, Any], *, index: int) -> tuple[str, str, str | None, tuple[str, ...], str]:
    role = raw.get("role")
    if role not in _ROLES:
        raise SubjectIndexError("SUBJECT_INDEX_INVALID", f"subjects[{index}].role 非法", details={"role": role})

    entity_type = raw.get("entityType")
    if entity_type not in _ENTITY_TYPES:
        raise SubjectIndexError(
            "SUBJECT_INDEX_INVALID",
            f"subjects[{index}].entityType 非法",
            details={"entityType": entity_type},
        )

    name_raw = raw.get("name")
    if name_raw is not None and not isinstance(name_raw, str):
        raise SubjectIndexError("SUBJECT_INDEX_INVALID", f"subjects[{index}].name 必须是字符串或 null")
    name = name_raw.strip() if isinstance(name_raw, str) else None
    if not name:
        name = None

    aliases_raw = raw.get("aliases") or []
    if not isinstance(aliases_raw, list):
        raise SubjectIndexError("SUBJECT_INDEX_INVALID", f"subjects[{index}].aliases 必须是数组")
    aliases: list[str] = []
    for alias in aliases_raw:
        if not isinstance(alias, str) or not alias.strip():
            raise SubjectIndexError("SUBJECT_INDEX_INVALID", f"subjects[{index}].aliases 含非法值")
        if alias.strip() not in aliases:
            aliases.append(alias.strip())
    if role not in aliases:
        aliases.append(role)

    quote = raw.get("quote")
    if not isinstance(quote, str) or not quote.strip():
        raise SubjectIndexError("SUBJECT_INDEX_INVALID", f"subjects[{index}].quote 必须是非空字符串")

    return role, entity_type, name, tuple(aliases), quote.strip()


def build_subject_index(
    adapter: ModelAdapter,
    document: InputDocument,
    *,
    mock_key: str = "subject_index",
) -> tuple[Subject, ...]:
    """Call the model once and return the normalized subject index."""

    request = ModelRequest(
        request_id=f"{document.request_id}:subject_index",
        system_prompt=SUBJECT_INDEX_SYSTEM_PROMPT,
        user_prompt=_user_prompt(document),
        json_schema=SUBJECT_INDEX_SCHEMA,
        metadata={"phase": "subject_index", "mock_key": mock_key},
    )
    response = adapter.generate_json(request)
    if not isinstance(response.parsed, Mapping):
        raise SubjectIndexError("SUBJECT_INDEX_INVALID", "主体索引响应不是 JSON 对象")

    raw_subjects = response.parsed.get("subjects")
    if not isinstance(raw_subjects, list) or not raw_subjects:
        raise SubjectIndexError("SUBJECT_INDEX_INVALID", "主体索引缺少 subjects 数组")

    # 主体 ID 由服务端统一分配（P1/P2...），不让模型决定内部标识。
    counters = {role: 0 for role in _ROLES}
    subjects: list[Subject] = []
    seen: set[tuple[str, str]] = set()

    for index, raw in enumerate(raw_subjects):
        if not isinstance(raw, Mapping):
            raise SubjectIndexError("SUBJECT_INDEX_INVALID", f"subjects[{index}] 不是对象")
        role, entity_type, name, aliases, quote = _normalize_subject(raw, index=index)

        source = _find_source_ref(document, quote)
        if source is None and name:
            source = _find_source_ref(document, name)
            if source is not None:
                quote = name
        if source is None:
            raise SubjectIndexError(
                "SUBJECT_INDEX_INVALID",
                f"subjects[{index}].quote 在输入文本中找不到",
                details={"quote": quote},
            )

        key = (role, source.quote)
        if key in seen:
            continue
        seen.add(key)

        role_index = counters[role]
        counters[role] += 1
        subjects.append(
            Subject(
                subject_id=f"P{len(subjects) + 1}",
                role=role,
                entity_type=entity_type,
                name=name,
                aliases=aliases,
                output_pointer=f"{_ROLE_POINTERS[role]}/{role_index}",
                source_refs=(source,),
            )
        )

    # 民间借贷起诉状至少要有一个原告和一个被告，否则不能进入后续抽取。
    if counters["原告"] == 0 or counters["被告"] == 0:
        raise SubjectIndexError(
            "NO_PARTIES",
            "未识别到原告或被告",
            details={"原告": counters["原告"], "被告": counters["被告"]},
        )

    return tuple(subjects)


def subjects_for_prompt(subjects: Sequence[Subject]) -> list[dict[str, Any]]:
    """Compact subject index included in downstream task prompts."""

    return [
        {
            "subjectId": subject.subject_id,
            "role": subject.role,
            "entityType": subject.entity_type,
            "name": subject.name,
            "aliases": list(subject.aliases),
            "outputPointer": subject.output_pointer,
        }
        for subject in subjects
    ]

"""Task definitions and prompt projection.

Five tasks are used in the MVP: parties, claims, factsA, factsB, remaining.
Facts are split statically because the facts module has 51 fields.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from . import config
from .models import InputDocument, Subject
from .subject_index import subjects_for_prompt


@dataclass(frozen=True)
class TaskSpec:
    name: str
    fields: tuple[Mapping[str, Any], ...]

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(str(field["outputPath"]) for field in self.fields)


TASK_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["data"],
    "additionalProperties": False,
    "properties": {
        "data": {"type": "object"},
        "evidence": {
            "type": "array",
            "items": {"type": "object"},
        },
    },
}

TASK_SYSTEM_PROMPT = """你是民间借贷要素式起诉状的字段抽取器。
只依据用户提供的案件文本抽取，不补充、不推测、不编造。
只输出一个 JSON 对象，不要输出 markdown、解释或多余文字。
当前任务列出的每个字段都必须出现在 data 中；未提及或无法确定时用 null，不能省略。
只有文本明确表示“没有、无、未、不存在”时才填“否/无”；沉默不等于否，禁止用空白推断否。
对于“是否/有无”字段：原文没有提到该事项时一律 null，不能因为其他请求、空白或“只主张某项”就推断“否/无”。
枚举字段只能从任务列出的枚举值中原样选择，不能写近似值；没有匹配项时，如果有“其他”就选“其他”，否则 null。
data 必须是嵌套对象；不要用 "/路径" 作为键；数组必须是 JSON 数组，不要用 {"*": ...} 包装。
金额用两位小数字符串（例如 60000.00），日期用 YYYY-MM-DD；不完整日期不要补日。
evidence.quote 必须是输入文本中的连续原文片段。"""


def _load_fields() -> list[Mapping[str, Any]]:
    payload = json.loads(config.FIELD_CONFIG_PATH.read_text(encoding="utf-8"))
    return list(payload.get("fields", []))


def _select(fields: list[Mapping[str, Any]], predicate: Callable[[str], bool]) -> tuple[Mapping[str, Any], ...]:
    selected = tuple(field for field in fields if predicate(str(field["outputPath"])))
    if not selected:
        raise RuntimeError("任务字段投影为空")
    return selected


def _fact_number(path: str) -> int | None:
    prefix = "/事实与理由/"
    if not path.startswith(prefix):
        return None
    rest = path[len(prefix):]
    if rest == "事实与理由原文":
        return 0
    match = re.match(r"^(\d+)\.", rest)
    return int(match.group(1)) if match else None


def build_task_specs() -> tuple[TaskSpec, ...]:
    fields = _load_fields()

    # 任务一只负责当事人与代理人，任务二只负责诉讼请求，避免 prompt 字段过多。
    parties = _select(
        fields,
        lambda path: path.startswith(("/原告/", "/被告/", "/第三人/", "/委托诉讼代理人/")),
    )
    claims = _select(fields, lambda path: path.startswith("/诉讼请求/"))
    # 事实模块 51 个字段，固定拆成 A（原文 + 1-9）和 B（10-18），两者不重叠。
    facts_a = _select(
        fields,
        lambda path: (n := _fact_number(path)) is not None and 0 <= n <= 9,
    )
    facts_b = _select(
        fields,
        lambda path: (n := _fact_number(path)) is not None and 10 <= n <= 18,
    )
    remaining = _select(
        fields,
        lambda path: path.startswith(
            ("/约定管辖和诉前保全/", "/对纠纷解决方式的意愿/", "/签署信息/")
        ),
    )

    return (
        TaskSpec("parties", parties),
        TaskSpec("claims", claims),
        TaskSpec("factsA", facts_a),
        TaskSpec("factsB", facts_b),
        TaskSpec("remaining", remaining),
    )


def _type_label(field: Mapping[str, Any]) -> str:
    schema = field.get("schema") or {}
    kind = schema.get("type", "string")
    if isinstance(kind, list):
        kind = "/".join(str(item) for item in kind)
    values = schema.get("enum") or (schema.get("items") or {}).get("enum")
    if values:
        kind = f"{kind}；{'、'.join(str(value) for value in values if value is not None)}"
    if schema.get("format"):
        kind = f"{kind}；{schema['format']}"
    return str(kind)


def build_task_prompt(task: TaskSpec, document: InputDocument, subjects: tuple[Subject, ...]) -> str:
    subject_payload = json.dumps(subjects_for_prompt(subjects), ensure_ascii=False, indent=2)
    blocks = [f"[{index}]\n{text}" for index, text in enumerate(document.file_context)]
    field_lines = []
    for field in task.fields:
        category = field.get("categoryCondition")
        category_text = f" | category={category}" if category else ""
        field_lines.append(
            f"- {field['outputPath']} | {_type_label(field)}{category_text} | {field.get('description', '')}"
        )

    # prompt 同时给出案件原文、共享主体索引和本任务字段清单，模型只输出局部 data。
    return (
        "任务：" + task.name + "\n\n"
        "案件文本：\n" + "\n\n".join(blocks) + "\n\n"
        "共享主体索引：\n" + subject_payload + "\n\n"
        "只填写以下字段（必须输出每个字段；可以输出嵌套的局部对象；不要输出未列出的字段）：\n"
        + "\n".join(field_lines)
        + "\n\n输出格式：\n"
        '{"data": {"局部嵌套对象": "..."}, "evidence": ['
        '{"fieldPath": "/具体路径", "subjectId": "P1 或 null", '
        '"quote": "原文连续片段", "kind": "explicit|negative|derived"}]}\n'
        "evidence 只覆盖关键字段；无法给出时可以为空数组。"
    )

"""把模型常见输出偏差归一化为项目 Schema 可校验的形状。

真实 Qwen 调用暴露过几类偏差：
- 返回扁平路径键，例如 "/事实与理由/..."；
- 数组用 {"*": {...}} 表示；
- 当事人缺少 category，或缺少部分 data 叶子；
- 空数组代替 null；
- 金额返回 "60000" 而不是 "60000.00"。

这里做“形状归一化”，不补业务值；缺失值仍然保持 null。
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from . import config

_ORG_KEYS = {
    "creditCode",
    "legalRep",
    "registrationPlace",
    "ownership",
    "ownershipOther",
    "stateOwnershipType",
    "type",
}


def load_schema() -> dict[str, Any]:
    return json.loads(config.SCHEMA_PATH.read_text(encoding="utf-8"))


def _resolve_ref(root: Mapping[str, Any], ref: str) -> Mapping[str, Any]:
    node: Any = root
    for part in ref.lstrip("#/").split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def _blank(node: Mapping[str, Any], root: Mapping[str, Any]) -> Any:
    if "$ref" in node:
        return _blank(_resolve_ref(root, str(node["$ref"])), root)
    if "properties" in node:
        return {key: _blank(value, root) for key, value in node["properties"].items()}
    return None


def blank_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    return _blank(schema, schema)


def _deep_merge(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, Mapping):
            _deep_merge(target[key], value)
        else:
            target[key] = value


def _set_path(target: dict[str, Any], parts: list[str], value: Any) -> None:
    current = target
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    leaf = parts[-1]
    if isinstance(current.get(leaf), dict) and isinstance(value, Mapping):
        _deep_merge(current[leaf], value)
    else:
        current[leaf] = value


def expand_flat_paths(value: Any) -> Any:
    """把 Qwen 返回的 "/a/b" 扁平键展开成嵌套对象。"""

    if isinstance(value, list):
        return [expand_flat_paths(item) for item in value]
    if not isinstance(value, Mapping):
        return value

    result: dict[str, Any] = {}
    for key, item in value.items():
        expanded = expand_flat_paths(item)
        if isinstance(key, str) and key.startswith("/") and "/" in key:
            parts = [part for part in key.split("/") if part]
            if parts:
                _set_path(result, parts, expanded)
                continue
        _deep_merge(result, {key: expanded})
    return result


def wildcard_to_list(value: Any) -> Any:
    """把 {"*": {...}} 归一化为单元素数组。"""

    if isinstance(value, list):
        return [wildcard_to_list(item) for item in value]
    if not isinstance(value, Mapping):
        return value

    if "*" in value:
        star = value["*"]
        if isinstance(star, list):
            # Qwen 可能把 {"*": [ {...} ]} 也返回出来；此时 "*" 本身已经是数组。
            return [wildcard_to_list(item) for item in star]
        if isinstance(star, Mapping):
            item = {key: wildcard_to_list(val) for key, val in value.items() if key != "*"}
            _deep_merge(item, {key: wildcard_to_list(val) for key, val in star.items()})
            return [item]
        return [wildcard_to_list(star)]

    return {key: wildcard_to_list(val) for key, val in value.items()}


def empty_arrays_to_null(value: Any) -> Any:
    if isinstance(value, list):
        if not value:
            return None
        return [empty_arrays_to_null(item) for item in value]
    if isinstance(value, Mapping):
        return {key: empty_arrays_to_null(item) for key, item in value.items()}
    return value


def _infer_category(data: Mapping[str, Any]) -> str:
    return "法人、非法人组织" if any(key in data for key in _ORG_KEYS) else "自然人"


def _blank_party_data(category: str, schema: Mapping[str, Any]) -> dict[str, Any]:
    def_name = "organizationData" if category == "法人、非法人组织" else "naturalPersonData"
    return _blank(schema["$defs"][def_name], schema)


def normalize_parties(data: dict[str, Any], schema: Mapping[str, Any]) -> dict[str, Any]:
    """补齐 category/data，并保证自然人/组织字段叶子存在。"""

    for role in ("原告", "被告", "第三人"):
        value = data.get(role)
        if value is None:
            continue
        if isinstance(value, Mapping):
            value = [value]
        if not isinstance(value, list):
            continue

        normalized = []
        for item in value:
            if not isinstance(item, Mapping):
                continue
            raw_data = item.get("data")
            if not isinstance(raw_data, Mapping):
                raw_data = {key: val for key, val in item.items() if key != "category"}

            category = item.get("category")
            if category not in ("自然人", "法人、非法人组织"):
                category = _infer_category(raw_data)

            filled = _blank_party_data(category, schema)
            filled.update(raw_data)
            normalized.append({"category": category, "data": filled})

        data[role] = normalized or None
    return data


def _is_money_schema(node: Mapping[str, Any]) -> bool:
    pattern = str(node.get("pattern", ""))
    return pattern.endswith(r"\.[0-9]{2}$")


def _to_money(value: Any) -> Any:
    # Qwen 常把金额写成“50000元”“人民币50,000元”“尚欠本金60000元”，
    # 从字符串中取第一个数字，再补两位小数；不处理“5万元”这类中文数量词。
    text = str(value).strip().replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return value
    try:
        amount = Decimal(match.group(0))
    except (InvalidOperation, ValueError):
        return value
    return f"{amount:.2f}"


def normalize_money(value: Any, node: Mapping[str, Any], root: Mapping[str, Any]) -> Any:
    """只对金额 Schema 做两位小数字符串归一化，不动费率/日期。"""

    if "$ref" in node:
        return normalize_money(value, _resolve_ref(root, str(node["$ref"])), root)

    if isinstance(value, Mapping):
        properties = node.get("properties", {})
        for key, item in list(value.items()):
            if key in properties:
                value[key] = normalize_money(item, properties[key], root)
        return value

    if isinstance(value, list):
        items = node.get("items")
        if isinstance(items, Mapping):
            return [normalize_money(item, items, root) for item in value]
        return value

    if _is_money_schema(node) and isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return _to_money(value)
    return value


def _enum_warning(path: str, value: Any) -> dict[str, Any]:
    return {
        "code": "ENUM_NORMALIZED",
        "message": f"枚举值 {value!r} 不在允许项中，已归为“其他”",
        "severity": "warning",
        "fieldPath": path,
    }


def normalize_enums(
    value: Any,
    node: Mapping[str, Any],
    root: Mapping[str, Any],
    path: str = "",
    issues: list[dict[str, Any]] | None = None,
) -> Any:
    """把模型近似的枚举值归到“其他”，并留下 review 警告。"""

    if "$ref" in node:
        return normalize_enums(value, _resolve_ref(root, str(node["$ref"])), root, path, issues)

    if isinstance(value, Mapping) and "properties" in node:
        properties = node["properties"]
        for key, item in list(value.items()):
            if key in properties:
                value[key] = normalize_enums(
                    item,
                    properties[key],
                    root,
                    f"{path}/{key}",
                    issues,
                )
        return value

    if isinstance(value, list) and isinstance(node.get("items"), Mapping):
        for index, item in enumerate(value):
            value[index] = normalize_enums(
                item,
                node["items"],
                root,
                f"{path}/{index}",
                issues,
            )
        return value

    enum_values = node.get("enum")
    if isinstance(value, str) and enum_values and value not in enum_values:
        if "其他" in enum_values:
            if issues is not None:
                issues.append(_enum_warning(path, value))
            return "其他"
    return value


def _fill_missing(template: Any, actual: Any) -> Any:
    if isinstance(template, Mapping) and isinstance(actual, Mapping):
        result = {key: copy.deepcopy(val) for key, val in template.items()}
        for key, value in actual.items():
            if key in result:
                result[key] = _fill_missing(result[key], value)
            else:
                result[key] = value
        return result
    if actual is None:
        return copy.deepcopy(template)
    return actual


def normalize_task_data(data: Mapping[str, Any], schema: Mapping[str, Any]) -> dict[str, Any]:
    """先处理单个任务输出，避免扁平键/通配数组污染合并。"""

    normalized = expand_flat_paths(dict(data))
    normalized = wildcard_to_list(normalized)
    normalized = empty_arrays_to_null(normalized)
    normalized = normalize_parties(normalized, schema)
    normalized = normalize_money(normalized, schema, schema)
    return normalized


def normalize_merged(
    data: Mapping[str, Any],
    schema: Mapping[str, Any],
    issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """合并后补齐固定对象、金额格式、枚举和当事人结构。"""

    template = blank_schema(schema)
    normalized = _fill_missing(template, dict(data))
    normalized = normalize_parties(normalized, schema)
    normalized = normalize_money(normalized, schema, schema)
    normalized = normalize_enums(normalized, schema, schema, "", issues)
    normalized = empty_arrays_to_null(normalized)
    return normalized

"""Structural and business validation for merged extraction results."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from . import config

try:  # optional at import time; Stage 1 installs requirements.txt
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:  # pragma: no cover - exercised only before dependency install
    Draft202012Validator = None  # type: ignore[assignment]
    FormatChecker = None  # type: ignore[assignment]


@dataclass(frozen=True)
class ValidationOutcome:
    status: str
    review: dict[str, Any]
    error: dict[str, Any] | None = None


def _pointer(parts) -> str:
    if not parts:
        return "/"
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _money(value: Any) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


def _warning(code: str, message: str, *, field_path: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message, "severity": "warning"}
    if field_path:
        payload["fieldPath"] = field_path
    return payload


def _has_value(value: Any) -> bool:
    return value not in (None, [], {})


# E2 关键字段：有值时必须能追溯 evidence；"否/无"必须是 negative 证据。
_KEY_PATTERNS: tuple[str, ...] = tuple(
    f"/{role}/*/data/{field}"
    for role in ("原告", "被告", "第三人")
    for field in (
        "name",
        "sex",
        "birthDate",
        "ethnicity",
        "phone",
        "residence",
        "certificateType",
        "certificateNumber",
    )
) + (
    "/委托诉讼代理人/是否委托",
    "/诉讼请求/1.本金/尚欠本金",
    "/诉讼请求/1.本金/币种",
    "/诉讼请求/2.利息/计算基数",
    "/诉讼请求/2.利息/利率标准",
    "/诉讼请求/2.利息/起算日期",
    "/诉讼请求/2.利息/是否请求支付至实际清偿之日止",
    "/诉讼请求/3.是否要求提前还款或解除合同/是否要求",
    "/诉讼请求/4.是否主张担保权利/是否主张",
    "/诉讼请求/5.是否主张实现债权的费用/是否主张",
    "/诉讼请求/6.是否主张诉讼费用",
    "/事实与理由/3.借款金额/约定",
    "/事实与理由/3.借款金额/实际提供",
    "/事实与理由/3.借款金额/提供方式",
    "/事实与理由/4.借款期限/是否到期",
    "/事实与理由/4.借款期限/约定起始日期",
    "/事实与理由/4.借款期限/约定截止日期",
    "/事实与理由/5.借款利率/利率",
    "/事实与理由/5.借款利率/周期",
    "/事实与理由/5.借款利率/补充内容",
    "/事实与理由/6.借款提供时间/*/提供日期",
    "/事实与理由/6.借款提供时间/*/金额",
    "/事实与理由/7.还款方式/方式",
    "/事实与理由/8.还款情况/已还本金",
    "/事实与理由/9.是否存在逾期还款/是否存在",
    "/事实与理由/9.是否存在逾期还款/逾期起始日期",
    "/事实与理由/10.是否签订物的担保（抵押、质押）合同/是否签订",
    "/事实与理由/12.是否最高额担保（抵押、质押）/是否最高额担保",
    "/事实与理由/13.是否办理抵押、质押登记/是否办理",
    "/事实与理由/14.是否签订保证合同/是否签订",
    "/事实与理由/15.其他担保方式/是否存在",
    "/事实与理由/17.请求依据/合同约定",
)


def _expand_pattern(data: Any, pattern: str) -> list[tuple[str, Any]]:
    """把包含 * 的字段路径展开成当前数据中的具体路径。"""

    parts = pattern.split("/")[1:]
    results: list[tuple[str, Any]] = []

    def walk(node: Any, index: int, path: str) -> None:
        if index == len(parts):
            results.append((path, node))
            return
        part = parts[index]
        if part == "*":
            if isinstance(node, list):
                for item_index, item in enumerate(node):
                    walk(item, index + 1, f"{path}/{item_index}")
            return
        if isinstance(node, Mapping) and part in node:
            walk(node[part], index + 1, f"{path}/{part}")

    walk(data, 0, "")
    return results


class ResultValidator:
    """Validate merged data against the full schema, then apply MVP rules."""

    def __init__(self) -> None:
        if Draft202012Validator is None or FormatChecker is None:
            raise RuntimeError("jsonschema is not installed; run pip install -r requirements.txt")
        self.schema = json.loads(config.SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(self.schema)
        self.validator = Draft202012Validator(self.schema, format_checker=FormatChecker())

    def validate(self, data: Mapping[str, Any], review: Mapping[str, Any]) -> ValidationOutcome:
        result_review = copy.deepcopy(dict(review))
        result_review.setdefault("warnings", [])
        result_review.setdefault("conflicts", [])
        result_review.setdefault("missing", [])

        # 先做完整结构校验；Schema 失败直接 error，不进入业务规则，避免误判。
        schema_errors = []
        for error in sorted(
            self.validator.iter_errors(data),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        ):
            schema_errors.append(
                {
                    "path": _pointer(error.absolute_path),
                    "message": error.message,
                    "validator": error.validator,
                }
            )
        if schema_errors:
            result_review["warnings"].append(
                _warning("SCHEMA_INVALID", "结果未通过完整 JSON Schema")
            )
            return ValidationOutcome(
                status="error",
                review=result_review,
                error={
                    "code": "SCHEMA_INVALID",
                    "message": "结果未通过完整 JSON Schema",
                    "retryable": False,
                    "details": {"errors": schema_errors[:20]},
                },
            )

        business_errors, warnings = self._business(data)
        result_review["warnings"].extend(warnings)

        if business_errors:
            first = business_errors[0]
            result_review["warnings"].append(_warning(first["code"], first["message"]))
            return ValidationOutcome(
                status="error",
                review=result_review,
                error={
                    "code": first["code"],
                    "message": first["message"],
                    "retryable": False,
                    "details": first.get("details", {}),
                },
            )

        # E2：有 evidence 时才检查；Mock 测试不强制，真实模型必须能追溯关键字段。
        if result_review.get("evidence"):
            missing_evidence, evidence_warnings = self._check_evidence(data, result_review)
            result_review["missing"].extend(missing_evidence)
            result_review["missing"] = list(dict.fromkeys(result_review["missing"]))
            result_review["warnings"].extend(evidence_warnings)

        if result_review["warnings"] or result_review["conflicts"] or result_review["missing"]:
            return ValidationOutcome(status="needs_review", review=result_review)

        return ValidationOutcome(status="ok", review=result_review)

    @staticmethod
    def _check_evidence(
        data: Mapping[str, Any],
        review: Mapping[str, Any],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        evidence_by_path: dict[str, Mapping[str, Any]] = {}
        for item in review.get("evidence") or []:
            if isinstance(item, Mapping) and isinstance(item.get("fieldPath"), str):
                evidence_by_path[item["fieldPath"]] = item

        missing: list[str] = []
        warnings: list[dict[str, Any]] = []

        for pattern in _KEY_PATTERNS:
            for path, value in _expand_pattern(data, pattern):
                if not _has_value(value):
                    continue
                evidence = evidence_by_path.get(path)
                if evidence is None:
                    missing.append(path)
                    continue
                # “否/无”必须有明确否定证据，不能靠沉默或普通明示证据。
                if value in ("否", "无") and evidence.get("kind") != "negative":
                    warnings.append(
                        _warning(
                            "NEGATIVE_WITHOUT_EVIDENCE",
                            "否/无缺少明确否定证据",
                            field_path=path,
                        )
                    )

        return list(dict.fromkeys(missing)), warnings

    @staticmethod
    def _business(data: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        def warn(code: str, message: str, field_path: str | None = None) -> None:
            warnings.append(_warning(code, message, field_path=field_path))

        # 1. 当事人必须存在，且不能是“所有叶子都为 null”的空白主体
        for role in ("原告", "被告"):
            parties = data.get(role)
            if not isinstance(parties, list) or not parties:
                errors.append({"code": "NO_PARTIES", "message": f"缺少{role}"})
                continue
            for index, party in enumerate(parties):
                info = party.get("data") if isinstance(party, Mapping) else None
                if not isinstance(info, Mapping) or not any(_has_value(value) for value in info.values()):
                    errors.append(
                        {
                            "code": "EMPTY_PARTY",
                            "message": f"{role}[{index}] 的主体信息为空",
                            "details": {"path": f"/{role}/{index}"},
                        }
                    )

        # 2. MVP 只有一个全局代理人区块，不能按主体拆分
        agent = data.get("委托诉讼代理人") or {}
        agent_status = agent.get("是否委托")
        agent_items = agent.get("代理人")
        if agent_status == "有" and not agent_items:
            warn("AGENT_MISSING", "是否委托=有，但未提供代理人", "/委托诉讼代理人/是否委托")
        if agent_status == "无" and agent_items:
            warn("AGENT_SHOULD_BE_NULL", "是否委托=无，但代理人非空", "/委托诉讼代理人/是否委托")

        # 3. 5B：Schema 保持多选，互斥/组合规则在这里由业务校验拦截
        for role in ("原告", "被告", "第三人"):
            for index, party in enumerate(data.get(role) or []):
                if not isinstance(party, Mapping):
                    continue
                info = party.get("data")
                if not isinstance(info, Mapping) or party.get("category") != "法人、非法人组织":
                    continue
                path = f"/{role}/{index}/data"
                types = info.get("type")
                if isinstance(types, list):
                    legal = [item for item in types if item != "上市公司"]
                    if len(legal) > 1:
                        warn("ORG_TYPE_MULTIPLE_FORMS", "组织类型包含多个法律形式", f"{path}/type")
                    if "上市公司" in types and "股份有限公司" not in types:
                        warn("ORG_TYPE_LISTED_WITHOUT_JOINT_STOCK", "上市公司未同时选择股份有限公司", f"{path}/type")
                    if "上市公司" in types and any(
                        item not in {"股份有限公司", "上市公司"} for item in types
                    ):
                        warn("ORG_TYPE_LISTED_CONFLICT", "上市公司与其他法律形式同时出现", f"{path}/type")
                    if "其他企业法人" in types and any(item != "其他企业法人" for item in legal):
                        warn("ORG_TYPE_OTHER_CONFLICT", "其他企业法人与具体法律形式同时出现", f"{path}/type")

                state_types = info.get("stateOwnershipType")
                if isinstance(state_types, list) and len(state_types) > 1:
                    warn("STATE_OWNERSHIP_MULTIPLE", "控股/参股同时出现", f"{path}/stateOwnershipType")

        facts = data.get("事实与理由") or {}
        claims = data.get("诉讼请求") or {}

        repayment = ((facts.get("7.还款方式") or {}).get("方式"))
        if isinstance(repayment, list) and len(repayment) > 1:
            warn("REPAYMENT_METHOD_MULTIPLE", "单笔借款出现多个还款方式", "/事实与理由/7.还款方式/方式")

        registration = ((facts.get("13.是否办理抵押、质押登记") or {}).get("登记类型"))
        if isinstance(registration, list) and len(registration) > 1:
            warn("REGISTRATION_TYPE_MULTIPLE", "单个担保物出现多个登记类型", "/事实与理由/13.是否办理抵押、质押登记/登记类型")

        # 4. 金额口径：本金/利息/总额、计算基数、实际提供、已还本金和分次交付
        principal = _money(((claims.get("1.本金") or {}).get("尚欠本金")))
        interest = _money(((claims.get("2.利息") or {}).get("尚欠利息")))
        total = _money(((claims.get("8.标的总额") or {}).get("金额")))
        if principal is not None and interest is not None and total is not None:
            if principal + interest != total:
                warn("TOTAL_MISMATCH", "本金 + 利息 != 标的总额", "/诉讼请求/8.标的总额/金额")

        interest_base = _money(((claims.get("2.利息") or {}).get("计算基数")))
        if principal is not None and interest_base is not None and principal != interest_base:
            warn("INTEREST_BASE_MISMATCH", "利息计算基数 != 尚欠本金", "/诉讼请求/2.利息/计算基数")

        actual = _money(((facts.get("3.借款金额") or {}).get("实际提供")))
        repaid = _money(((facts.get("8.还款情况") or {}).get("已还本金"))) or Decimal("0")
        if principal is not None and actual is not None:
            if principal > actual - repaid:
                warn("PRINCIPAL_OVER_ACTUAL", "尚欠本金超过实际提供减已还本金", "/诉讼请求/1.本金/尚欠本金")

        deliveries = facts.get("6.借款提供时间")
        if isinstance(deliveries, list) and deliveries and actual is not None:
            delivered = Decimal("0")
            for item in deliveries:
                amount = _money((item or {}).get("金额"))
                if amount is not None:
                    delivered += amount
            if delivered != actual:
                warn("DELIVERY_SUM_MISMATCH", "各次提供金额之和 != 实际提供", "/事实与理由/6.借款提供时间")

        # 5. 日期只检查已明确给出的日期顺序，不用运行当天推断“是否到期”
        term = facts.get("4.借款期限") or {}
        start = term.get("约定起始日期")
        end = term.get("约定截止日期")
        if isinstance(start, str) and isinstance(end, str) and start > end:
            warn("TERM_DATE_ORDER", "约定起始日期晚于约定截止日期", "/事实与理由/4.借款期限")

        interest_info = claims.get("2.利息") or {}
        interest_start = interest_info.get("起算日期")
        interest_cutoff = interest_info.get("暂计截止日期")
        if isinstance(interest_start, str) and isinstance(interest_cutoff, str) and interest_start > interest_cutoff:
            warn("INTEREST_DATE_ORDER", "利息起算日期晚于暂计截止日期", "/诉讼请求/2.利息")

        # 6. 第 5 项实现债权费用与第 6 项诉讼费用的口径边界
        fee_detail = ((claims.get("5.是否主张实现债权的费用") or {}).get("明细"))
        if isinstance(fee_detail, str) and "诉讼费用" in fee_detail:
            warn("FEE_OVERLAP", "第5项实现债权费用明细包含诉讼费用", "/诉讼请求/5.是否主张实现债权的费用/明细")

        # 7. 担保条件：否时必须清空详情，并检查 10 与其他担保项的跨项矛盾
        def guarantee_block(key: str) -> Mapping[str, Any]:
            block = facts.get(key)
            return block if isinstance(block, Mapping) else {}

        g10 = guarantee_block("10.是否签订物的担保（抵押、质押）合同")
        g11 = guarantee_block("11.担保人、担保物")
        if g10.get("是否签订") == "否" and any(_has_value(value) for value in g11.values()):
            warn("GUARANTEE_11_WITH_NO_CONTRACT", "10=否但11仍有担保人/担保物", "/事实与理由/11.担保人、担保物")

        for key, selector, detail_keys in [
            ("12.是否最高额担保（抵押、质押）", "是否最高额担保", ("担保债权的确定时间", "担保额度")),
            ("13.是否办理抵押、质押登记", "是否办理", ("登记类型",)),
            ("14.是否签订保证合同", "是否签订", ("签订时间", "保证人", "主要内容", "保证方式")),
            ("15.其他担保方式", "是否存在", ("形式", "签订时间")),
        ]:
            block = guarantee_block(key)
            if block.get(selector) == "否":
                for detail_key in detail_keys:
                    if _has_value(block.get(detail_key)):
                        warn(
                            "GUARANTEE_DETAIL_WITH_NO",
                            f"{key} = 否，但 {detail_key} 非空",
                            f"/事实与理由/{key}/{detail_key}",
                        )

        if g10.get("是否签订") == "否":
            for key, selector in [
                ("12.是否最高额担保（抵押、质押）", "是否最高额担保"),
                ("13.是否办理抵押、质押登记", "是否办理"),
                ("14.是否签订保证合同", "是否签订"),
                ("15.其他担保方式", "是否存在"),
            ]:
                if guarantee_block(key).get(selector) == "是":
                    warn("GUARANTEE_CROSS_CONFLICT", f"10=否但{key}=是", f"/事实与理由/{key}/{selector}")

        # Deduplicate warnings by code + path + message
        unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in warnings:
            key = (str(item.get("code")), str(item.get("fieldPath")), str(item.get("message")))
            unique[key] = item
        return errors, list(unique.values())

from __future__ import annotations

import copy
import json
import re
import unittest
from pathlib import Path

from app.adapter import MockModelAdapter
from app.input import parse_request
from app.models import RateLimitError
from app.pipeline import ExtractionPipeline
from app.tasks import build_task_specs

CASE_DIR = Path("stage0/fixtures/cases/L001")


def _fact_number(key: str) -> int | None:
    if key == "事实与理由原文":
        return 0
    match = re.match(r"^(\d+)\.", key)
    return int(match.group(1)) if match else None


def _split_data(data: dict) -> dict[str, dict]:
    facts = data["事实与理由"]
    facts_a = {
        key: value
        for key, value in facts.items()
        if (number := _fact_number(key)) is not None and 0 <= number <= 9
    }
    facts_b = {
        key: value
        for key, value in facts.items()
        if (number := _fact_number(key)) is not None and 10 <= number <= 18
    }
    return {
        "parties": {
            "原告": data["原告"],
            "被告": data["被告"],
            "第三人": data["第三人"],
            "委托诉讼代理人": data["委托诉讼代理人"],
        },
        "claims": {"诉讼请求": data["诉讼请求"]},
        "factsA": {"事实与理由": facts_a},
        "factsB": {"事实与理由": facts_b},
        "remaining": {
            "约定管辖和诉前保全": data["约定管辖和诉前保全"],
            "对纠纷解决方式的意愿": data["对纠纷解决方式的意愿"],
            "签署信息": data["签署信息"],
        },
    }


def _subject_index_response(data: dict) -> dict:
    subjects = []
    for role in ("原告", "被告", "第三人"):
        for party in data[role] or []:
            name = party["data"]["name"]
            subjects.append(
                {
                    "role": role,
                    "entityType": party["category"],
                    "name": name,
                    "aliases": [role],
                    "quote": name,
                }
            )
    return {"subjects": subjects}


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.input = json.loads((CASE_DIR / "input.json").read_text(encoding="utf-8"))
        self.expected = json.loads((CASE_DIR / "expected.data.json").read_text(encoding="utf-8"))
        self.request_id = self.input["requestId"]
        parts = _split_data(self.expected)
        self.responses: dict[str, dict] = {
            f"{self.request_id}:subject_index": _subject_index_response(self.expected),
        }
        for task_name, partial in parts.items():
            self.responses[f"{self.request_id}:{task_name}"] = {"data": partial}

    def test_task_specs(self) -> None:
        specs = build_task_specs()
        self.assertEqual([spec.name for spec in specs], ["parties", "claims", "factsA", "factsB", "remaining"])
        counts = {spec.name: len(spec.fields) for spec in specs}
        self.assertEqual(sum(counts.values()), 160)
        self.assertEqual(counts["parties"], 72)
        self.assertEqual(counts["claims"], 22)
        self.assertEqual(counts["remaining"], 15)
        self.assertEqual(counts["factsA"] + counts["factsB"], 51)

    def test_mock_pipeline_merges_to_expected_data(self) -> None:
        pipeline = ExtractionPipeline(MockModelAdapter(responses=self.responses), max_workers=5)
        try:
            result = pipeline.run(parse_request(self.input))
        finally:
            pipeline.close()
        self.assertEqual(result.data, self.expected)
        self.assertEqual([s["subjectId"] for s in result.review["subjectIndex"]], ["P1", "P2"])
        self.assertEqual(result.review["conflicts"], [])

    def test_evidence_offsets_and_warnings(self) -> None:
        responses = copy.deepcopy(self.responses)
        key = f"{self.request_id}:parties"
        responses[key]["evidence"] = [
            {
                "fieldPath": "/原告/0/data/name",
                "subjectId": "P1",
                "quote": "陈明远",
                "kind": "explicit",
                "confidence": 0.99,
            },
            {
                "fieldPath": "/被告/0/data/name",
                "subjectId": "P2",
                "quote": "李志强",
                "kind": "explicit",
            },
            {
                "fieldPath": "/诉讼请求/1.本金/尚欠本金",
                "subjectId": None,
                "quote": "50000元",
                "kind": "explicit",
            },
            {
                "fieldPath": "/原告/0/data/name",
                "subjectId": "P1",
                "quote": "陈明远",
                "kind": "explicit",
            },
            {
                "fieldPath": "/原告/0/data/name",
                "subjectId": "P9",
                "quote": "陈明远",
                "kind": "explicit",
            },
            {
                "fieldPath": "/原告/0/data/name",
                "subjectId": "P1",
                "quote": "不存在",
                "kind": "explicit",
            },
            {
                "fieldPath": "/原告/0/data/unknown",
                "subjectId": "P1",
                "quote": "陈明远",
                "kind": "explicit",
            },
            {
                "fieldPath": "/原告/0/data/ethnicity",
                "subjectId": "P1",
                "quote": "汉族",
                "kind": "guess",
            },
            {
                "fieldPath": "/事实与理由/6.借款提供时间/*/提供日期",
                "subjectId": None,
                "quote": "2025-02-10",
                "kind": "explicit",
            },
        ]
        pipeline = ExtractionPipeline(MockModelAdapter(responses=responses), max_workers=5)
        try:
            result = pipeline.run(parse_request(self.input))
        finally:
            pipeline.close()

        evidence = result.review["evidence"]
        by_path = {item["fieldPath"]: item for item in evidence}
        self.assertIn("/原告/0/data/name", by_path)
        self.assertIn("/被告/0/data/name", by_path)
        self.assertIn("/诉讼请求/1.本金/尚欠本金", by_path)
        self.assertIn("/事实与理由/6.借款提供时间/0/提供日期", by_path)

        text = self.input["fileContext"][0]
        for item in evidence:
            self.assertEqual(
                text[item["charStart"]:item["charEnd"]],
                item["quote"],
            )

        codes = {item["code"] for item in result.review["warnings"]}
        self.assertIn("EVIDENCE_QUOTE_NOT_FOUND", codes)
        self.assertIn("UNKNOWN_SUBJECT_ID", codes)
        self.assertIn("EVIDENCE_PATH_NOT_FOUND", codes)
        self.assertIn("EVIDENCE_KIND_INVALID", codes)
        self.assertEqual(len(evidence), 6)

    def test_task_failure_is_propagated(self) -> None:
        responses = dict(self.responses)
        errors = {f"{self.request_id}:claims": RateLimitError("limited", retry_after_s=1)}
        pipeline = ExtractionPipeline(
            MockModelAdapter(responses=responses, errors=errors),
            max_workers=5,
        )
        try:
            with self.assertRaises(RateLimitError):
                pipeline.run(parse_request(self.input))
        finally:
            pipeline.close()


if __name__ == "__main__":
    unittest.main()

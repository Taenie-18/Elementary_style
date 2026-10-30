from __future__ import annotations

import copy
import json
import re
import unittest
from pathlib import Path

from app.adapter import MockModelAdapter
from app.models import RateLimitError
from app.service import ExtractionService

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


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads((CASE_DIR / "input.json").read_text(encoding="utf-8"))
        self.expected = json.loads((CASE_DIR / "expected.data.json").read_text(encoding="utf-8"))
        self.request_id = self.payload["requestId"]
        self.responses: dict[str, dict] = {
            f"{self.request_id}:subject_index": _subject_index_response(self.expected),
        }
        for task_name, partial in _split_data(self.expected).items():
            self.responses[f"{self.request_id}:{task_name}"] = {"data": partial}

    def test_valid_request_returns_ok(self) -> None:
        service = ExtractionService(MockModelAdapter(responses=self.responses), max_workers=5)
        try:
            response = service.extract(self.payload)
        finally:
            service.close()
        self.assertEqual(response.http_status, 200)
        self.assertEqual(response.body["status"], "ok")
        self.assertEqual(response.body["data"], self.expected)
        self.assertIsNone(response.body["error"])

    def test_invalid_input_returns_400(self) -> None:
        payload = dict(self.payload)
        payload["fileContext"] = []
        service = ExtractionService(MockModelAdapter(responses=self.responses), max_workers=5)
        try:
            response = service.extract(payload)
        finally:
            service.close()
        self.assertEqual(response.http_status, 400)
        self.assertEqual(response.body["error"]["code"], "INVALID_REQUEST")
        self.assertIsNone(response.body["data"])

    def test_no_defendant_returns_422(self) -> None:
        responses = {
            f"{self.request_id}:subject_index": {
                "subjects": [
                    {
                        "role": "原告",
                        "entityType": "自然人",
                        "name": "陈明远",
                        "aliases": ["原告"],
                        "quote": "陈明远",
                    }
                ]
            }
        }
        service = ExtractionService(MockModelAdapter(responses=responses), max_workers=5)
        try:
            response = service.extract(self.payload)
        finally:
            service.close()
        self.assertEqual(response.http_status, 422)
        self.assertEqual(response.body["error"]["code"], "NO_PARTIES")

    def test_model_rate_limit_returns_429(self) -> None:
        responses = copy.deepcopy(self.responses)
        errors = {f"{self.request_id}:claims": RateLimitError("limited", retry_after_s=1)}
        service = ExtractionService(
            MockModelAdapter(responses=responses, errors=errors),
            max_workers=5,
        )
        try:
            response = service.extract(self.payload)
        finally:
            service.close()
        self.assertEqual(response.http_status, 429)
        self.assertEqual(response.body["error"]["code"], "MODEL_RATE_LIMIT")


if __name__ == "__main__":
    unittest.main()

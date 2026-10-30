from __future__ import annotations

import copy
import json
import unittest

from app.adapter import MockModelAdapter
from app.models import InvalidJSONError, ModelTimeoutError
from app.service import ExtractionService
from tests.test_service import CASE_DIR, _split_data, _subject_index_response


class NegativeCaseTests(unittest.TestCase):
    """Stage 1 负例：覆盖 error / needs_review 两条主路径。"""

    def setUp(self) -> None:
        self.payload = json.loads((CASE_DIR / "input.json").read_text(encoding="utf-8"))
        self.expected = json.loads((CASE_DIR / "expected.data.json").read_text(encoding="utf-8"))
        self.request_id = self.payload["requestId"]
        self.responses: dict[str, dict] = {
            f"{self.request_id}:subject_index": _subject_index_response(self.expected),
        }
        for task_name, partial in _split_data(self.expected).items():
            self.responses[f"{self.request_id}:{task_name}"] = {"data": copy.deepcopy(partial)}

    def _run(self, responses: dict, *, errors: dict | None = None):
        service = ExtractionService(
            MockModelAdapter(responses=responses, errors=errors),
            max_workers=5,
        )
        try:
            return service.extract(self.payload)
        finally:
            service.close()

    def test_no_defendant_is_error(self) -> None:
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
        response = self._run(responses)
        self.assertEqual(response.http_status, 422)
        self.assertEqual(response.body["status"], "error")
        self.assertEqual(response.body["error"]["code"], "NO_PARTIES")
        self.assertIsNone(response.body["data"])

    def test_empty_party_is_error(self) -> None:
        responses = copy.deepcopy(self.responses)
        party_data = responses[f"{self.request_id}:parties"]["data"]["原告"][0]["data"]
        responses[f"{self.request_id}:parties"]["data"]["原告"][0]["data"] = {
            key: None for key in party_data
        }
        response = self._run(responses)
        self.assertEqual(response.http_status, 422)
        self.assertEqual(response.body["error"]["code"], "EMPTY_PARTY")

    def test_agent_missing_needs_review(self) -> None:
        responses = copy.deepcopy(self.responses)
        responses[f"{self.request_id}:parties"]["data"]["委托诉讼代理人"] = {
            "是否委托": "有",
            "代理人": None,
        }
        response = self._run(responses)
        self.assertEqual(response.http_status, 200)
        self.assertEqual(response.body["status"], "needs_review")
        codes = {item["code"] for item in response.body["review"]["warnings"]}
        self.assertIn("AGENT_MISSING", codes)

    def test_principal_over_actual_needs_review(self) -> None:
        responses = copy.deepcopy(self.responses)
        responses[f"{self.request_id}:claims"]["data"]["诉讼请求"]["1.本金"]["尚欠本金"] = "99999.00"
        response = self._run(responses)
        self.assertEqual(response.http_status, 200)
        self.assertEqual(response.body["status"], "needs_review")
        codes = {item["code"] for item in response.body["review"]["warnings"]}
        self.assertIn("PRINCIPAL_OVER_ACTUAL", codes)

    def test_guarantee_conflict_needs_review(self) -> None:
        responses = copy.deepcopy(self.responses)
        facts_b = responses[f"{self.request_id}:factsB"]["data"]["事实与理由"]
        facts_b["10.是否签订物的担保（抵押、质押）合同"]["是否签订"] = "否"
        facts_b["11.担保人、担保物"]["担保人"] = ["某担保人"]
        response = self._run(responses)
        self.assertEqual(response.http_status, 200)
        self.assertEqual(response.body["status"], "needs_review")
        codes = {item["code"] for item in response.body["review"]["warnings"]}
        self.assertIn("GUARANTEE_11_WITH_NO_CONTRACT", codes)

    def test_schema_invalid_is_error(self) -> None:
        responses = copy.deepcopy(self.responses)
        responses[f"{self.request_id}:claims"]["data"]["诉讼请求"]["6.是否主张诉讼费用"] = "maybe"
        response = self._run(responses)
        self.assertEqual(response.http_status, 422)
        self.assertEqual(response.body["error"]["code"], "SCHEMA_INVALID")

    def test_model_timeout_is_504(self) -> None:
        errors = {f"{self.request_id}:claims": ModelTimeoutError("timeout")}
        response = self._run(copy.deepcopy(self.responses), errors=errors)
        self.assertEqual(response.http_status, 504)
        self.assertEqual(response.body["error"]["code"], "MODEL_TIMEOUT")

    def test_invalid_json_is_502(self) -> None:
        errors = {f"{self.request_id}:claims": InvalidJSONError("bad json")}
        response = self._run(copy.deepcopy(self.responses), errors=errors)
        self.assertEqual(response.http_status, 502)
        self.assertEqual(response.body["error"]["code"], "INVALID_JSON")


if __name__ == "__main__":
    unittest.main()

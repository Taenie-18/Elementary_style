"""FastAPI 接口层测试：使用 TestClient 验证 HTTP 状态与响应包装。"""

from __future__ import annotations

import json
import unittest

from fastapi.testclient import TestClient

from app.adapter import MockModelAdapter
from app.api import create_app
from app.service import ExtractionService
from tests.test_service import CASE_DIR, _split_data, _subject_index_response


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads((CASE_DIR / "input.json").read_text(encoding="utf-8"))
        self.expected = json.loads((CASE_DIR / "expected.data.json").read_text(encoding="utf-8"))
        self.request_id = self.payload["requestId"]
        self.responses: dict[str, dict] = {
            f"{self.request_id}:subject_index": _subject_index_response(self.expected),
        }
        for task_name, partial in _split_data(self.expected).items():
            self.responses[f"{self.request_id}:{task_name}"] = {"data": partial}
        self.service = ExtractionService(
            MockModelAdapter(responses=self.responses),
            max_workers=5,
        )
        self.client = TestClient(create_app(self.service, shutdown_close=False))

    def tearDown(self) -> None:
        self.client.close()
        self.service.close()

    def test_healthz(self) -> None:
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_valid_extract(self) -> None:
        response = self.client.post("/v1/extract", json=self.payload)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["data"], self.expected)
        self.assertIsNone(body["error"])

    def test_invalid_input(self) -> None:
        payload = dict(self.payload)
        payload["fileContext"] = []
        response = self.client.post("/v1/extract", json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "INVALID_REQUEST")

    def test_no_defendant(self) -> None:
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
        client = TestClient(create_app(service, shutdown_close=False))
        try:
            response = client.post("/v1/extract", json=self.payload)
        finally:
            client.close()
            service.close()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "NO_PARTIES")


if __name__ == "__main__":
    unittest.main()

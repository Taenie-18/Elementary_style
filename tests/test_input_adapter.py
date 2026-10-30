from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.adapter import MockModelAdapter, QwenAdapter, QwenConfig
from app.input import parse_request
from app.models import (
    InputValidationError,
    InvalidJSONError,
    InvalidResponseError,
    ModelConfigError,
    ModelRequest,
    RateLimitError,
)


def valid_payload() -> dict:
    return {
        "requestId": "req-1",
        "templateId": "private_lending_complaint",
        "schemaVersion": "0.1.0",
        "fileContext": ["原告：张三。", "被告：李四。"],
    }


class InputTests(unittest.TestCase):
    def test_valid_request(self) -> None:
        doc = parse_request(valid_payload())
        self.assertEqual(doc.request_id, "req-1")
        self.assertEqual(len(doc.file_context), 2)
        self.assertEqual(doc.text, "原告：张三。\n被告：李四。")

    def test_empty_file_context(self) -> None:
        payload = valid_payload()
        payload["fileContext"] = []
        with self.assertRaises(InputValidationError) as ctx:
            parse_request(payload)
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")
        self.assertEqual(ctx.exception.field, "fileContext")

    def test_too_many_items(self) -> None:
        payload = valid_payload()
        payload["fileContext"] = ["x"] * 21
        with self.assertRaises(InputValidationError):
            parse_request(payload)

    def test_item_too_long(self) -> None:
        payload = valid_payload()
        payload["fileContext"] = ["x" * 50_001]
        with self.assertRaises(InputValidationError):
            parse_request(payload)

    def test_total_too_long(self) -> None:
        payload = valid_payload()
        payload["fileContext"] = ["x" * 40_000] * 3
        with self.assertRaises(InputValidationError):
            parse_request(payload)

    def test_whitespace_item(self) -> None:
        payload = valid_payload()
        payload["fileContext"] = ["   "]
        with self.assertRaises(InputValidationError):
            parse_request(payload)

    def test_extra_key(self) -> None:
        payload = valid_payload()
        payload["caseId"] = "L001"
        with self.assertRaises(InputValidationError):
            parse_request(payload)

    def test_wrong_template(self) -> None:
        payload = valid_payload()
        payload["templateId"] = "sales_contract"
        with self.assertRaises(InputValidationError):
            parse_request(payload)

    def test_empty_request_id(self) -> None:
        payload = valid_payload()
        payload["requestId"] = " "
        with self.assertRaises(InputValidationError):
            parse_request(payload)


class MockAdapterTests(unittest.TestCase):
    def request(self, key: str = "k") -> ModelRequest:
        return ModelRequest(
            request_id="req-1",
            system_prompt="system",
            user_prompt="user",
            metadata={"mock_key": key},
        )

    def test_returns_parsed_object(self) -> None:
        adapter = MockModelAdapter(responses={"k": {"a": 1}})
        response = adapter.generate_json(self.request())
        self.assertEqual(response.parsed, {"a": 1})
        self.assertEqual(response.request_id, "req-1")
        self.assertEqual(response.usage.total_tokens, 0)
        self.assertTrue(adapter.healthcheck())

    def test_raises_normalized_error(self) -> None:
        adapter = MockModelAdapter(errors={"k": RateLimitError("limited", retry_after_s=3)})
        with self.assertRaises(RateLimitError) as ctx:
            adapter.generate_json(self.request())
        self.assertEqual(ctx.exception.retry_after_s, 3)

    def test_missing_response(self) -> None:
        adapter = MockModelAdapter()
        with self.assertRaises(InvalidResponseError):
            adapter.generate_json(self.request())


class QwenConfigTests(unittest.TestCase):
    def test_from_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "QWEN_BASE_URL": "https://example.test/v1",
                "QWEN_API_KEY": "secret",
                "QWEN_MODEL": "qwen3.8",
                "QWEN_TIMEOUT_S": "30",
                "QWEN_MAX_TOKENS": "2048",
                "QWEN_ENABLE_THINKING": "false",
            },
            clear=True,
        ):
            cfg = QwenConfig.from_env()
        self.assertEqual(cfg.base_url, "https://example.test/v1")
        self.assertEqual(cfg.model, "qwen3.8")
        self.assertEqual(cfg.api_key, "secret")
        self.assertEqual(cfg.timeout_s, 30)
        self.assertEqual(cfg.max_tokens, 2048)
        self.assertIs(cfg.enable_thinking, False)

    def test_missing_base_url(self) -> None:
        with patch.dict(os.environ, {"QWEN_MODEL": "qwen3.8"}, clear=True):
            with self.assertRaises(ModelConfigError):
                QwenConfig.from_env()

    def test_invalid_thinking(self) -> None:
        with patch.dict(
            os.environ,
            {
                "QWEN_BASE_URL": "https://example.test/v1",
                "QWEN_API_KEY": "secret",
                "QWEN_MODEL": "qwen3.8",
                "QWEN_ENABLE_THINKING": "maybe",
            },
            clear=True,
        ):
            with self.assertRaises(ModelConfigError):
                QwenConfig.from_env()

    def test_missing_api_key(self) -> None:
        with patch.dict(
            os.environ,
            {
                "QWEN_BASE_URL": "https://example.test/v1",
                "QWEN_MODEL": "qwen3.8",
            },
            clear=True,
        ):
            with self.assertRaises(ModelConfigError):
                QwenConfig.from_env()


class QwenAdapterTests(unittest.TestCase):
    def config(self) -> QwenConfig:
        return QwenConfig(
            base_url="https://example.test/v1",
            model="qwen3.8",
            api_key=None,
            enable_thinking=False,
        )

    def request(self, schema: bool = True) -> ModelRequest:
        return ModelRequest(
            request_id="req-1",
            system_prompt="system",
            user_prompt="user",
            json_schema={"type": "object"} if schema else None,
        )

    def test_chat_url(self) -> None:
        self.assertEqual(
            QwenAdapter._chat_url("https://example.test/v1"),
            "https://example.test/v1/chat/completions",
        )
        self.assertEqual(
            QwenAdapter._chat_url("https://example.test/v1/chat/completions"),
            "https://example.test/v1/chat/completions",
        )

    def test_build_payload(self) -> None:
        adapter = QwenAdapter(self.config())
        payload = adapter._build_payload(self.request())
        self.assertEqual(payload["model"], "qwen3.8")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertIs(payload["enable_thinking"], False)
        self.assertEqual(payload["temperature"], 0.0)

    def test_build_payload_without_schema(self) -> None:
        adapter = QwenAdapter(self.config())
        payload = adapter._build_payload(self.request(schema=False))
        self.assertNotIn("response_format", payload)

    def test_parse_json_object(self) -> None:
        self.assertEqual(QwenAdapter._parse_json_content('{"a": 1}'), {"a": 1})

    def test_reject_invalid_json(self) -> None:
        with self.assertRaises(InvalidJSONError):
            QwenAdapter._parse_json_content("not json")

    def test_reject_non_object(self) -> None:
        with self.assertRaises(InvalidJSONError):
            QwenAdapter._parse_json_content("[1, 2]")


if __name__ == "__main__":
    unittest.main()

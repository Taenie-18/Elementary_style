"""Qwen 真实联调冒烟测试。

未设置 QWEN_API_KEY 时自动跳过；设置后执行一次最小 JSON 调用。
"""

from __future__ import annotations

import os
import unittest

from app.adapter import QwenAdapter, QwenConfig
from app.models import ModelRequest

LIVE_READY = bool(
    os.getenv("QWEN_BASE_URL")
    and os.getenv("QWEN_API_KEY")
    and os.getenv("QWEN_MODEL")
)


@unittest.skipUnless(LIVE_READY, "QWEN_BASE_URL / QWEN_API_KEY / QWEN_MODEL 未设置")
class QwenLiveTests(unittest.TestCase):
    def test_minimal_json_call(self) -> None:
        cfg = QwenConfig.from_env()
        adapter = QwenAdapter(cfg)
        request = ModelRequest(
            request_id="qwen-live-smoke",
            system_prompt="你是 JSON 输出器，只输出 JSON 对象，不要输出 markdown。",
            user_prompt='请返回 {"ok": true}。',
            json_schema={"type": "object"},
            max_tokens=64,
            timeout_s=cfg.timeout_s,
        )
        response = adapter.generate_json(request)
        self.assertEqual(response.parsed, {"ok": True})
        self.assertEqual(response.request_id, "qwen-live-smoke")


if __name__ == "__main__":
    unittest.main()

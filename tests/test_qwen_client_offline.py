"""Qwen 百炼客户端离线回归：不读取 Key、不创建网络请求。"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from pydantic import BaseModel

from backend.services import qwen_client


class _Result(BaseModel):
    value: int


class QwenClientOfflineTests(unittest.TestCase):
    def test_image_block_uses_chat_compatible_data_url(self):
        block = qwen_client.image_block(b"png-bytes", "drawing.png")
        self.assertEqual(block["type"], "image_url")
        self.assertTrue(block["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_run_uses_json_mode_and_disables_thinking_without_network(self):
        captured = {}

        class Completions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return SimpleNamespace(
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(content='{"value": 7}'),
                        finish_reason="stop",
                    )],
                    usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=3),
                )

        old_client = qwen_client.get_client
        try:
            qwen_client.get_client = lambda: SimpleNamespace(
                chat=SimpleNamespace(completions=Completions())
            )
            result = qwen_client.run("只输出 JSON", [qwen_client.text_block("测试")], _Result)
        finally:
            qwen_client.get_client = old_client

        self.assertEqual(result.value, 7)
        self.assertEqual(captured["model"], qwen_client.QWEN_TEXT_MODEL)
        self.assertEqual(captured["response_format"], {"type": "json_object"})
        self.assertIn("JSON", captured["messages"][0]["content"])
        self.assertIn("enable_thinking", captured["extra_body"])

    def test_web_search_is_rejected_before_creating_client(self):
        with self.assertRaisesRegex(RuntimeError, "联网检索"):
            qwen_client.run(
                "只输出 JSON", [qwen_client.text_block("测试")], _Result,
                extra_tools=[qwen_client.WEB_SEARCH_TOOL],
            )

    def test_quota_error_switches_to_next_text_model(self):
        calls = []

        class QuotaError(Exception):
            status_code = 429

        class Completions:
            def create(self, **kwargs):
                calls.append(kwargs["model"])
                if len(calls) == 1:
                    raise QuotaError("quota exhausted")
                return SimpleNamespace(
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(content='{"value": 8}'),
                        finish_reason="stop",
                    )],
                    usage=None,
                )

        old_client = qwen_client.get_client
        old_error = qwen_client.APIStatusError
        old_pool = qwen_client.QWEN_TEXT_MODELS
        try:
            qwen_client.get_client = lambda: SimpleNamespace(
                chat=SimpleNamespace(completions=Completions())
            )
            qwen_client.APIStatusError = QuotaError
            qwen_client.QWEN_TEXT_MODELS = ("text-primary", "text-backup")
            qwen_client._unavailable_models["text"].clear()
            result = qwen_client.run("只输出 JSON", [qwen_client.text_block("测试")], _Result)
        finally:
            qwen_client.get_client = old_client
            qwen_client.APIStatusError = old_error
            qwen_client.QWEN_TEXT_MODELS = old_pool
            qwen_client._unavailable_models["text"].clear()

        self.assertEqual(result.value, 8)
        self.assertEqual(calls, ["text-primary", "text-backup"])

    def test_native_web_search_keeps_returned_sources(self):
        captured = {}

        class Response:
            status_code = 200
            text = ""

            @staticmethod
            def json():
                return {
                    "output": {
                        "choices": [{"message": {"content": '{"value": 9}'}}],
                        "search_info": {"search_results": [{"title": "官方目录", "url": "https://example.com/catalog"}]},
                    },
                    "usage": {"plugins": {"search": {"count": 1}}},
                }

        class Client:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def post(self, url, **kwargs):
                captured["url"] = url
                captured.update(kwargs)
                return Response()

        old_key = qwen_client.QWEN_API_KEY
        old_pool = qwen_client.QWEN_WEB_SEARCH_MODELS
        old_client = qwen_client.httpx.Client
        try:
            qwen_client.QWEN_API_KEY = "test-key"
            qwen_client.QWEN_WEB_SEARCH_MODELS = ("qwen-plus",)
            qwen_client.httpx.Client = lambda **kwargs: Client()
            qwen_client._unavailable_models["web"].clear()
            result, meta = qwen_client.complete_to_model_with_web_search(
                "只输出 JSON", {"query": "VQ110-5M"}, _Result,
            )
        finally:
            qwen_client.QWEN_API_KEY = old_key
            qwen_client.QWEN_WEB_SEARCH_MODELS = old_pool
            qwen_client.httpx.Client = old_client
            qwen_client._unavailable_models["web"].clear()

        self.assertEqual(result.value, 9)
        self.assertTrue(captured["json"]["parameters"]["enable_search"])
        self.assertTrue(captured["json"]["parameters"]["search_options"]["forced_search"])
        self.assertEqual(meta["search_count"], 1)
        self.assertEqual(meta["sources"][0].url, "https://example.com/catalog")


if __name__ == "__main__":
    unittest.main()

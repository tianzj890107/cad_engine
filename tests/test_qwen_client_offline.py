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

    def test_run_uses_json_mode_and_configured_thinking_without_network(self):
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
            qwen_client.get_client = lambda vision=False: SimpleNamespace(
                chat=SimpleNamespace(completions=Completions())
            )
            result = qwen_client.run("只输出 JSON", [qwen_client.text_block("测试")], _Result)
        finally:
            qwen_client.get_client = old_client

        self.assertEqual(result.value, 7)
        self.assertEqual(captured["model"], qwen_client._model_candidates(False)[0])
        self.assertEqual(captured["response_format"], {"type": "json_object"})
        self.assertIn("JSON", captured["messages"][0]["content"])
        self.assertIn("enable_thinking", captured["extra_body"])

    def test_web_search_is_rejected_before_creating_client(self):
        with self.assertRaisesRegex(RuntimeError, "联网检索"):
            qwen_client.run(
                "只输出 JSON", [qwen_client.text_block("测试")], _Result,
                extra_tools=[qwen_client.WEB_SEARCH_TOOL],
            )

    def test_configured_model_is_never_silently_swapped(self):
        """以前配的模型一报 429/404 就静默换成池里的下一个 —— 界面上配了 opus5，
        实际跑的还是旧 qwen 型号，用户完全看不出来。现在只用配置的那一个，
        失败就如实报错，并且错误里要带上模型名。"""
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
        old_runtime_pool = qwen_client._runtime["text_models"]
        old_candidates = qwen_client._model_candidates
        try:
            qwen_client.get_client = lambda vision=False: SimpleNamespace(
                chat=SimpleNamespace(completions=Completions())
            )
            qwen_client.APIStatusError = QuotaError
            qwen_client.QWEN_TEXT_MODELS = ("text-primary", "text-backup")
            qwen_client._runtime["text_models"] = ("text-primary", "text-backup")
            qwen_client._unavailable_models["text"].clear()
            # 模型选择现在来自「模型设置」，不再是 qwen 的模型池。
            qwen_client._model_candidates = lambda vision: ("text-primary",)
            with self.assertRaises(RuntimeError) as caught:
                qwen_client.run("只输出 JSON", [qwen_client.text_block("测试")], _Result)
            error = str(caught.exception)
        finally:
            qwen_client.get_client = old_client
            qwen_client.APIStatusError = old_error
            qwen_client.QWEN_TEXT_MODELS = old_pool
            qwen_client._runtime["text_models"] = old_runtime_pool
            qwen_client._unavailable_models["text"].clear()
            qwen_client._model_candidates = old_candidates

        # 只调用了配置的那一个模型，没有偷偷换第二个。
        self.assertEqual(calls, ["text-primary"])
        self.assertIn("text-primary", error)
        self.assertNotIn("text-backup", error)

    def test_schema_failure_is_repaired_without_reuploading(self):
        calls = []

        class Completions:
            def create(self, **kwargs):
                calls.append(kwargs)
                if len(calls) == 1:
                    return SimpleNamespace(
                        choices=[SimpleNamespace(
                            message=SimpleNamespace(content='{"value":'),
                            finish_reason="length",
                        )],
                        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4, total_tokens=7),
                    )
                return SimpleNamespace(
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(content='{"value": 9}'),
                        finish_reason="stop",
                    )],
                    usage=None,
                )

        old_client = qwen_client.get_client
        try:
            qwen_client.get_client = lambda vision=False: SimpleNamespace(
                chat=SimpleNamespace(completions=Completions())
            )
            result = qwen_client.run("只输出 JSON", [qwen_client.text_block("测试")], _Result)
        finally:
            qwen_client.get_client = old_client

        self.assertEqual(result.value, 9)
        self.assertEqual(len(calls), 2)
        self.assertIn("上一次没有返回完整 JSON", calls[1]["messages"][1]["content"][-1]["text"])

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


class ConfiguredParamsTests(unittest.TestCase):
    """「模型设置」里配的推理参数必须真的进请求 —— 以前只对 Agent 会话生效，
    平台自己的解析与分析仍走 .env 固定值，等于设了没用。"""

    def _run_capturing(self, tuning):
        captured = {}

        class Completions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return SimpleNamespace(
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(content='{"value": 1}'),
                        finish_reason="stop")],
                    usage=None,
                )

        old_client = qwen_client.get_client
        old_tuning = qwen_client._tuning
        try:
            qwen_client.get_client = lambda vision=False: SimpleNamespace(
                chat=SimpleNamespace(completions=Completions()))
            qwen_client._tuning = lambda: tuning
            qwen_client.run("只输出 JSON", [qwen_client.text_block("测试")], _Result)
        finally:
            qwen_client.get_client = old_client
            qwen_client._tuning = old_tuning
        return captured

    def test_temperature_and_max_tokens_reach_the_request(self):
        args = self._run_capturing({"temperature": 0.25, "max_tokens": 4096, "thinking": True})
        self.assertEqual(args["temperature"], 0.25)
        self.assertEqual(args["max_tokens"], 4096)
        self.assertTrue(args["extra_body"]["enable_thinking"])

    def test_blank_temperature_is_not_sent_as_zero(self):
        """留空表示用模型默认值；传 0 会把模型钉死在贪心解码上。"""
        args = self._run_capturing({"temperature": None, "max_tokens": None, "thinking": False})
        self.assertNotIn("temperature", args)
        self.assertFalse(args["extra_body"]["enable_thinking"])

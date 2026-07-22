"""OpenAI 客户端的离线回归测试：不创建任何 API 请求。"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

import httpx
from openai import APIConnectionError
from pydantic import BaseModel

from backend.services import openai_client


class _Result(BaseModel):
    value: int


class _FakeResponses:
    def __init__(self):
        self.request_args = None

    def create(self, **kwargs):
        self.request_args = kwargs
        return SimpleNamespace(status="completed", output_text='{"value": 1}', refusal=None)


class _SequenceResponses:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.request_args = []

    def create(self, **kwargs):
        self.request_args.append(kwargs)
        return SimpleNamespace(status="completed", output_text=self.outputs.pop(0), refusal=None)


class OpenAIClientOfflineTests(unittest.TestCase):
    def test_json_object_mode_does_not_send_full_schema(self):
        self.assertEqual(openai_client._json_object_format(), {"type": "json_object"})

    def test_incomplete_max_tokens_is_actionable_and_safe(self):
        response = SimpleNamespace(
            status="incomplete",
            error=None,
            max_output_tokens=6000,
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            usage=SimpleNamespace(
                input_tokens=123,
                output_tokens=6000,
                total_tokens=6123,
                output_tokens_details=SimpleNamespace(reasoning_tokens=5100),
            ),
        )
        message = openai_client._background_error_detail(response)
        self.assertIn("max_output_tokens", message)
        self.assertIn("6000", message)
        self.assertIn("推理 5100", openai_client._background_usage_summary(response))

    def test_incomplete_content_filter_has_its_own_diagnosis(self):
        response = SimpleNamespace(
            status="incomplete",
            error=None,
            max_output_tokens=6000,
            incomplete_details=SimpleNamespace(reason="content_filter"),
            usage=None,
        )
        self.assertIn("内容安全过滤", openai_client._background_error_detail(response))

    def test_reasoning_configuration_can_be_disabled(self):
        old_value = openai_client.OPENAI_REASONING_EFFORT
        try:
            openai_client.OPENAI_REASONING_EFFORT = ""
            self.assertIsNone(openai_client._reasoning_params("gpt-5.6"))
            openai_client.OPENAI_REASONING_EFFORT = "low"
            self.assertEqual(
                openai_client._reasoning_params("gpt-5.6"), {"effort": "low"}
            )
            self.assertIsNone(openai_client._reasoning_params("gpt-4o-mini"))
        finally:
            openai_client.OPENAI_REASONING_EFFORT = old_value

    def test_run_uses_small_json_mode_and_never_calls_a_real_client(self):
        responses = _FakeResponses()
        fake_client = SimpleNamespace(responses=responses)
        old_client = openai_client.get_client
        old_effort = openai_client.OPENAI_REASONING_EFFORT
        try:
            openai_client.get_client = lambda: fake_client
            openai_client.OPENAI_REASONING_EFFORT = "low"
            result = openai_client.run(
                "只输出 JSON",
                [openai_client.text_block("测试")],
                _Result,
            )
        finally:
            openai_client.get_client = old_client
            openai_client.OPENAI_REASONING_EFFORT = old_effort

        self.assertEqual(result.value, 1)
        self.assertEqual(responses.request_args["text"], {"format": {"type": "json_object"}})
        self.assertEqual(responses.request_args["reasoning"], {"effort": "low"})
        self.assertTrue(responses.request_args["background"])

    def test_web_tools_are_allowed_initially_but_disabled_for_json_repair(self):
        responses = _SequenceResponses(['{"value": "not-an-int"}', '{"value": 2}'])
        fake_client = SimpleNamespace(responses=responses)
        old_client = openai_client.get_client
        old_retries = openai_client.OPENAI_SCHEMA_REPAIR_RETRIES
        try:
            openai_client.get_client = lambda: fake_client
            openai_client.OPENAI_SCHEMA_REPAIR_RETRIES = 1
            result = openai_client.run(
                "只输出 JSON",
                [openai_client.text_block("测试")],
                _Result,
                extra_tools=[openai_client.WEB_SEARCH_TOOL],
            )
        finally:
            openai_client.get_client = old_client
            openai_client.OPENAI_SCHEMA_REPAIR_RETRIES = old_retries

        self.assertEqual(result.value, 2)
        self.assertEqual(responses.request_args[0]["tools"], [openai_client.WEB_SEARCH_TOOL])
        self.assertIn("可使用请求中实际提供的工具", responses.request_args[0]["instructions"])
        self.assertEqual(responses.request_args[1]["tools"], [])
        self.assertIsNone(responses.request_args[1]["max_tool_calls"])
        self.assertIn("没有可调用的输出工具", responses.request_args[1]["instructions"])

    def test_poll_connection_error_retries_same_background_response(self):
        class PollingResponses:
            def __init__(self):
                self.retrieve_ids = []

            def retrieve(self, response_id):
                self.retrieve_ids.append(response_id)
                if len(self.retrieve_ids) == 1:
                    raise APIConnectionError(
                        request=httpx.Request("GET", "https://api.openai.com/v1/responses/resp_test")
                    )
                return SimpleNamespace(status="completed", id=response_id, output_text='{}')

            def cancel(self, response_id):
                raise AssertionError(f"不应取消可恢复任务: {response_id}")

        responses = PollingResponses()
        old_client = openai_client.get_client
        old_sleep = openai_client.time.sleep
        try:
            openai_client.get_client = lambda: SimpleNamespace(responses=responses)
            openai_client.time.sleep = lambda _seconds: None
            result = openai_client._wait_for_background_response(
                SimpleNamespace(status="in_progress", id="resp_test")
            )
        finally:
            openai_client.get_client = old_client
            openai_client.time.sleep = old_sleep

        self.assertEqual(result.status, "completed")
        self.assertEqual(responses.retrieve_ids, ["resp_test", "resp_test"])


if __name__ == "__main__":
    unittest.main()

"""需求确认 AI 检查的实际模型留痕回归测试。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from backend import main


class RequirementAiCheckModelTests(unittest.TestCase):
    def test_check_note_records_the_model_that_really_succeeded(self):
        rule_check = {"items": [{"item": "检查项", "status": "ok", "detail": "已具备"}]}
        result = main.RequirementAiCheckResult(
            summary="检查完成",
            items=[{"item": "检查项", "status": "ok", "detail": "已具备"}],
        )
        with patch.object(main.qwen_client, "last_used_model", return_value="direct-deepseek-v4-flash"):
            check = main._normalize_requirement_ai_check(rule_check, result)
        self.assertEqual(check["model"], "direct-deepseek-v4-flash")
        self.assertEqual(check["model_source"], "runtime_actual")

    def test_missing_runtime_model_is_not_replaced_by_configured_default(self):
        rule_check = {"items": []}
        result = main.RequirementAiCheckResult(summary="检查完成")
        with patch.object(main.qwen_client, "last_used_model", return_value=None):
            check = main._normalize_requirement_ai_check(rule_check, result)
        self.assertEqual(check["model"], "")
        self.assertEqual(check["model_source"], "unavailable")


if __name__ == "__main__":
    unittest.main()

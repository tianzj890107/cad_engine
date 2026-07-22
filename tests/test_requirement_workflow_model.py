"""需求单保存模型的回归测试。"""
from __future__ import annotations

import unittest

from backend.models.workflow import RequirementDoc, WorkflowReview


class RequirementWorkflowModelTests(unittest.TestCase):
    def test_draft_accepts_history_and_updated_at_written_by_save_endpoint(self):
        doc = RequirementDoc(
            project_id="123456789abc",
            requirement_no="REQ-001",
            title="测试需求",
            data={},
        )
        doc.history = [WorkflowReview(
            action="saved", actor="system", role="admin", comment="", at="2026-07-20 10:00:00",
        )]
        doc.updated_at = "2026-07-20 10:00:00"

        saved = doc.model_dump()
        self.assertEqual(saved["history"][0]["action"], "saved")
        self.assertEqual(saved["updated_at"], "2026-07-20 10:00:00")


if __name__ == "__main__":
    unittest.main()

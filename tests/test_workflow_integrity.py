"""真实业务状态一致性回归：任务幂等、确认失效、审批绑定与发布快照。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend import main
from backend.models.approval import ApprovalNode, QuoteApproval
from backend.models.material import MaterialPlan
from backend.models.workflow import ProcessReport, RequirementDoc
from backend.services import tasks
from backend.storage import store


class WorkflowIntegrityTests(unittest.TestCase):
    def test_task_dedup_uses_business_input_key_not_only_kind(self):
        records = []

        def save_task(_project_id, record):
            records.append(record.copy())

        with patch.object(tasks.store, "list_tasks", side_effect=lambda _pid: records), \
             patch.object(tasks.store, "save_task", side_effect=save_task), \
             patch.object(tasks._executor, "submit"):
            first = tasks.submit("p1", "process", lambda: {}, dedup_key="process:P-001:a")
            duplicate = tasks.submit("p1", "process", lambda: {}, dedup_key="process:P-001:a")
            second_part = tasks.submit("p1", "process", lambda: {}, dedup_key="process:P-002:b")

        self.assertEqual(first, duplicate)
        self.assertNotEqual(first, second_part)
        self.assertEqual(len(records), 2)

    def test_confirmation_is_server_owned_and_content_change_revokes_it(self):
        current = MaterialPlan(project_id="p1")
        current.body.selected = "AlN"
        current.body.rationale = "已评审依据"
        current.body.confirmed = True
        current.body.confirmed_by = "engineer.a"
        current.body.confirmed_at = "2026-08-13 10:00:00"

        unchanged = current.model_copy(deep=True)
        unchanged.body.confirmed_by = "forged-user"
        main._sync_confirmation(current.body, unchanged.body)
        self.assertTrue(unchanged.body.confirmed)
        self.assertEqual(unchanged.body.confirmed_by, "engineer.a")

        changed = current.model_copy(deep=True)
        changed.body.selected = "Al2O3"
        main._sync_confirmation(current.body, changed.body)
        self.assertFalse(changed.body.confirmed)
        self.assertIsNone(changed.body.confirmed_by)
        self.assertIsNone(changed.body.confirmed_at)

    def test_empty_collection_is_not_treated_as_filled_requirement_data(self):
        self.assertFalse(main._is_filled([]))
        self.assertFalse(main._is_filled({}))
        self.assertTrue(main._is_filled(["里程碑"] ))

    def test_quote_approver_must_match_current_business_role(self):
        approval = QuoteApproval(
            project_id="p1", status="in_review", level=2,
            chain=[
                ApprovalNode(seq=1, role="销售总监", status="pending"),
                ApprovalNode(seq=2, role="财务负责人", status="waiting"),
            ],
        )
        with patch.object(main.store, "load_meta", return_value={"project_id": "p1"}), \
             patch.object(main, "_load_approval", return_value=approval), \
             patch.object(main.store, "save_approval"):
            with self.assertRaises(HTTPException) as caught:
                main.act_approval(
                    "p1", main.ApprovalAction(decision="approve"),
                    user={"username": "finance.a", "role": "finance_manager"},
                )
        self.assertEqual(caught.exception.status_code, 403)

    def test_published_report_snapshot_is_not_overwritten_by_new_draft(self):
        docs = {}

        class FakeMeta:
            def get_doc(self, project_id, kind):
                return docs.get((project_id, kind))

            def put_doc(self, project_id, kind, data):
                docs[(project_id, kind)] = data

        published = {"project_id": "p1", "version": 1, "status": "published", "title": "V1"}
        draft = {"project_id": "p1", "version": 2, "status": "draft", "title": "V2"}
        with patch.object(store, "_meta", return_value=FakeMeta()), \
             patch.object(store, "_touch_stage"), patch.object(store, "audit"):
            store.save_process_report("p1", published, author="manager")
            store.save_process_report("p1", draft, author="manager")
            snapshot = store.get_process_report_version("p1", 1)

        self.assertEqual(snapshot["title"], "V1")
        self.assertEqual(snapshot["status"], "published")

    def test_report_review_snapshot_ignores_audit_meta_but_detects_business_change(self):
        snapshot = {
            "device_name": "设备 A", "meta": {"stages": {"report": "old"}},
            "ir": {"parts": [{"part_id": "P1"}]},
            "steps": {"material": {"body": {"selected": "AlN"}}},
            "summary": {"conclusion": "可行"},
        }
        doc = ProcessReport(project_id="p1", report_no="RPT-P1", source_snapshot=snapshot)
        current = {**snapshot, "meta": {"stages": {"report": "new"}}}
        with patch.object(main.summary_svc, "aggregate", return_value=current):
            self.assertTrue(main._report_source_is_current("p1", doc))

        current = {**current, "summary": {"conclusion": "需整改"}}
        with patch.object(main.summary_svc, "aggregate", return_value=current):
            self.assertFalse(main._report_source_is_current("p1", doc))

    def test_approved_requirement_reopens_when_engineering_input_changes(self):
        requirement = RequirementDoc(
            project_id="p1", requirement_no="REQ-P1", status="approved",
            confirmed_by="manager.a", confirmed_at="2026-08-13 09:00:00",
            reviewed_by="director.a", reviewed_at="2026-08-13 10:00:00",
            ai_check={"ok": True},
        ).model_dump()
        saved = {}
        with patch.object(main.store, "save_requirement", side_effect=lambda _pid, doc, **_kw: saved.update(doc)), \
             patch.object(main.store, "audit"):
            main._reset_approved_requirement_after_input_change(
                "p1", requirement,
                {"username": "manager.a", "role": "process_manager"},
                "替换原始图纸",
            )

        self.assertEqual(saved["status"], "draft")
        self.assertIsNone(saved["confirmed_by"])
        self.assertIsNone(saved["reviewed_by"])
        self.assertEqual(saved["ai_check"], {})
        self.assertEqual(saved["history"][-1]["action"], "approved_requirement_reopened")

    def test_report_publish_requires_director_role(self):
        with self.assertRaises(HTTPException) as caught:
            main.publish_process_report(
                "p1", main.PublishAction(recipients=[]),
                user={"username": "manager.a", "role": "process_manager"},
            )
        self.assertEqual(caught.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()

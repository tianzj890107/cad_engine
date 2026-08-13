"""后端加固离线回归：不读取真实项目数据、不发起模型或网络请求。"""
from __future__ import annotations

import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import UploadFile

from backend import main
from backend.services import llm_client, tasks
from backend.storage import store
from backend.storage.meta_backend import JsonMetaBackend


class BackendHardeningTests(unittest.TestCase):
    @unittest.skipUnless(main.AUTH_AUTO_ADMIN, "仅在临时免登录演示模式验证")
    def test_auto_admin_mode_reports_authenticated_ui_as_disabled(self):
        """免登录模式仍提供管理员身份，但前端不应再显示登录遮罩。"""
        self.assertFalse(main.health()["auth_enabled"])

    def test_web_tools_are_safely_disabled_for_a_provider_without_support(self):
        old_available = llm_client.WEB_SEARCH_AVAILABLE
        try:
            llm_client.WEB_SEARCH_AVAILABLE = False
            self.assertIsNone(llm_client.web_search_tools(True))
            self.assertIn("不得声称", llm_client.web_search_notice(False))
        finally:
            llm_client.WEB_SEARCH_AVAILABLE = old_available

    def test_json_meta_backend_writes_a_complete_document_and_keeps_audit_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = JsonMetaBackend(Path(tmp))
            backend.put_meta("project", {"project_id": "project"})
            backend.put_doc("project", "ir", {"parts": [{"part_id": "P-001"}]})
            for index in range(20):
                backend.append_audit("project", {"action": f"event-{index}"})

            self.assertEqual(backend.get_doc("project", "ir")["parts"][0]["part_id"], "P-001")
            self.assertEqual(len(backend.list_audit("project")), 20)
            self.assertFalse(list(Path(tmp).rglob("*.tmp")))

    def test_upload_reader_rejects_oversize_file_before_full_memory_accumulation(self):
        upload = UploadFile(filename="large.png", file=io.BytesIO(b"x" * 9))
        with patch.object(main, "MAX_UPLOAD_BYTES", 8):
            with self.assertRaisesRegex(Exception, "超过大小上限"):
                asyncio.run(main._read_upload_limited(upload, label="测试文件"))

    def test_project_api_path_accepts_only_generated_project_ids(self):
        self.assertTrue(main._valid_project_path("/api/projects/13c8d8236c7a/material"))
        self.assertTrue(main._valid_project_path("/api/projects/3d"))
        self.assertFalse(main._valid_project_path("/api/projects/../../_auth_users.json"))
        self.assertFalse(main._valid_project_path("/api/projects/not-a-project/parse"))

    def test_restart_recovery_marks_only_unfinished_tasks(self):
        projects = [{"project_id": "p1"}]
        records = [
            {"task_id": "queued", "status": "queued"},
            {"task_id": "running", "status": "running"},
            {"task_id": "done", "status": "succeeded"},
        ]
        saved = []
        with patch.object(tasks.store, "list_projects", return_value=projects), \
             patch.object(tasks.store, "list_tasks", return_value=records), \
             patch.object(tasks.store, "save_task", side_effect=lambda pid, task: saved.append((pid, task.copy()))):
            self.assertEqual(tasks.recover_interrupted_tasks(), 2)

        self.assertEqual([task[1]["task_id"] for task in saved], ["queued", "running"])
        self.assertTrue(all(task[1]["status"] == "failed" for task in saved))

    def test_task_update_is_atomic_at_storage_boundary(self):
        records = [{"task_id": "t1", "status": "queued", "result": None}]
        saved = []
        class FakeMeta:
            def get_doc(self, project_id, kind):
                return {"items": [row.copy() for row in records]}
            def put_doc(self, project_id, kind, data):
                saved.append(data)
        with patch.object(store, "_meta", return_value=FakeMeta()):
            updated = store.update_task("p1", "t1", status="succeeded", result={"ok": True})
        self.assertEqual(updated["status"], "succeeded")
        self.assertEqual(updated["result"], {"ok": True})
        self.assertEqual(saved[-1]["items"][0]["result"], {"ok": True})

    def test_legacy_example_enterprise_data_is_not_returned_as_real(self):
        equipment = [
            {"id": "eq_kiln01", "name": "高温烧结炉 GSL-1800X"},
            {"id": "eq_real", "name": "客户维护设备"},
        ]
        suppliers = [
            {"id": "sup_al01", "name": "示例-高纯氧化铝粉厂A"},
            {"id": "sup_real", "name": "客户维护供应商"},
        ]
        with patch.object(store, "_read_equipment", return_value=equipment):
            self.assertEqual(store.list_equipment(), [equipment[1]])
        with patch.object(store, "_read_suppliers", return_value=suppliers):
            self.assertEqual(store.list_suppliers(), [suppliers[1]])


if __name__ == "__main__":
    unittest.main()

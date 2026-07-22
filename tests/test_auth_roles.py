"""认证与角色分工的离线回归测试。"""
from __future__ import annotations

import unittest

from backend.services import auth


class AuthRoleTests(unittest.TestCase):
    def test_password_is_hashed_and_public_view_excludes_sensitive_hash(self):
        user = auth.make_user("manager.demo", "DemoPassword!2026", "process_manager", "王经理")

        self.assertNotEqual(user["password_hash"], "DemoPassword!2026")
        self.assertTrue(auth.verify_password("DemoPassword!2026", user["password_hash"]))
        self.assertFalse(auth.verify_password("wrong-password", user["password_hash"]))
        self.assertNotIn("password_hash", auth.public_user(user))

    def test_manager_and_director_permissions_are_separated(self):
        # 工艺技术经理负责创建、确认、汇总与发布；总监负责需求和报告审核。
        self.assertIn("process_manager", auth.MANAGER_ROLES)
        self.assertNotIn("process_manager", auth.DIRECTOR_ROLES)
        self.assertIn("process_director", auth.DIRECTOR_ROLES)
        self.assertNotIn("process_director", auth.MANAGER_ROLES)
        self.assertIn("admin", auth.MANAGER_ROLES)
        self.assertIn("admin", auth.DIRECTOR_ROLES)

    def test_engineer_can_only_edit_own_project(self):
        engineer = {"username": "engineer.a", "role": "engineer"}
        self.assertTrue(auth.can_edit_project(engineer, {"owner": "engineer.a"}))
        self.assertFalse(auth.can_edit_project(engineer, {"owner": "engineer.b"}))
        self.assertTrue(auth.can_edit_project({"username": "manager", "role": "process_manager"}, {"owner": "engineer.a"}))
        self.assertFalse(auth.can_edit_project({"username": "director", "role": "process_director"}, {"owner": "director"}))


if __name__ == "__main__":
    unittest.main()

"""2.1 图纸解析 Agent（open-claude）的离线回归测试。

不发起任何模型调用：只验证平台工具的分派与返回、只读约束、SSE 帧格式，
以及「RequestParse 不自己解析」这条边界 —— 解析必须走平台既有流水线。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

SAMPLE_IR = {
    "device_name": "变频冰箱门体总成",
    "design_intent": "门体总成由面板、内胆与发泡层构成，重点控制平面度与保温性能。",
    "overall_dims": "1200 × 595 × 22 mm",
    "assembly_notes": "面板与内胆合装后注入聚氨酯。",
    "assemblies": [{"assembly_id": "A-001", "name": "门体总成", "quantity": 1}],
    "parts": [
        {"part_id": "P-001", "name": "前面板", "parent_id": "A-001", "quantity": 1,
         "confidence": 0.82, "material": {"spec": "VCM 覆膜板"},
         "tolerance_general": "ISO 2768-m",
         "features": [{"type": "plate", "length": 1200, "width": 595, "thickness": 0.5}],
         "provenance": {"note": "主视图"}},
        {"part_id": "P-002", "name": "内胆", "parent_id": "A-001", "quantity": 1,
         "confidence": 0.64, "material": {"spec": "HIPS"},
         "features": [{"type": "box", "length": 1180, "width": 620, "height": 480}]},
    ],
    "standard_parts": [{"spec": "GB/T 5783 M6x20", "category": "bolt", "quantity": 8}],
    "open_questions": [
        {"field": "P-001.thickness", "reason": "料厚标注模糊", "guess": "0.5mm"},
        {"field": "发泡层厚度", "reason": "图纸未标注"},
    ],
}


class OcAgentToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="oc_agent_"))
        cls.project_id = "aa11bb22cc33"
        os.environ["DATA_DIR"] = str(cls.tmp)

        import backend.config as config
        cls._original_data_dir = config.DATA_DIR
        config.DATA_DIR = cls.tmp

        # 让 store 的文件后端指向临时目录，避免碰用户的 data/。
        from backend.storage import meta_backend, store
        meta_backend._backend = meta_backend.JsonMetaBackend(cls.tmp)
        cls._store = store
        store.DATA_DIR = cls.tmp

        project_dir = cls.tmp / cls.project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "meta.json").write_text(json.dumps({
            "id": cls.project_id, "source_filename": "冰箱门体.png",
            "note": "门体平面度 ≤1.5mm", "attachments": ["技术协议.txt"],
        }, ensure_ascii=False), encoding="utf-8")
        (project_dir / "ir.json").write_text(json.dumps(SAMPLE_IR, ensure_ascii=False),
                                             encoding="utf-8")
        attachments = project_dir / "attachments"
        attachments.mkdir(exist_ok=True)
        (attachments / "技术协议.txt").write_text("门封磁条闭合无缝隙", encoding="utf-8")

        from backend.services import oc_agent
        cls.agent_module = oc_agent
        cls.cwd = str(project_dir)

    @classmethod
    def tearDownClass(cls):
        import backend.config as config
        config.DATA_DIR = cls._original_data_dir
        cls._store.DATA_DIR = cls._original_data_dir
        from backend.storage import meta_backend
        meta_backend._backend = None
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _run(self, name: str, params: dict | None = None):
        return self.agent_module._run_platform_tool(name, params or {}, self.cwd)

    # ------------------------------------------------------------ 平台工具
    def test_project_state_reports_real_project_data(self):
        state = json.loads(self._run("GetProjectState"))
        self.assertEqual(state["project_id"], self.project_id)
        self.assertEqual(state["source_drawing"], "冰箱门体.png")
        self.assertEqual(state["attachments"], ["技术协议.txt"])
        self.assertTrue(state["parsed"])
        self.assertEqual(state["device_name"], "变频冰箱门体总成")
        self.assertEqual(state["part_count"], 2)
        self.assertEqual(state["standard_part_count"], 1)
        self.assertEqual(state["open_question_count"], 2)
        self.assertAlmostEqual(state["average_confidence"], 0.73, places=3)

    def test_list_parts_is_a_flat_summary(self):
        parts = json.loads(self._run("ListParts"))
        self.assertEqual([item["part_id"] for item in parts], ["P-001", "P-002"])
        self.assertEqual(parts[0]["material"], "VCM 覆膜板")
        self.assertEqual(parts[0]["feature_count"], 1)

    def test_part_detail_returns_features_and_reports_unknown_id(self):
        detail = json.loads(self._run("GetPartDetail", {"part_id": "P-001"}))
        self.assertEqual(detail["name"], "前面板")
        self.assertEqual(detail["features"][0]["thickness"], 0.5)

        missing = json.loads(self._run("GetPartDetail", {"part_id": "P-999"}))
        self.assertIn("error", missing)
        self.assertEqual(missing["available"], ["P-001", "P-002"])

    def test_open_questions_are_returned_verbatim(self):
        questions = json.loads(self._run("GetOpenQuestions"))
        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[0]["field"], "P-001.thickness")

    def test_request_parse_only_requests_and_never_parses(self):
        """Agent 不得自己实现一套解析：该工具只发请求，解析由平台流水线执行。"""
        result = json.loads(self._run("RequestParse", {"reason": "用户说开始解析"}))
        self.assertTrue(result["requested"])
        self.assertEqual(result["reason"], "用户说开始解析")
        self.assertIn("平台流水线", result["note"])
        self.assertIn("请勿自行编造", result["note"])
        # IR 未被工具改写。
        self.assertEqual(self._store.load_ir(self.project_id)["device_name"], "变频冰箱门体总成")

    def test_unknown_platform_tool_is_reported_not_raised(self):
        self.assertIn("未知的平台工具", self._run("NotARealTool"))

    # -------------------------------------------------- 改零件参数（唯一的写操作）
    def _restore_ir(self):
        self._store.save_ir(self.project_id, json.loads(json.dumps(SAMPLE_IR)),
                            stage="parsed", author="test")

    def _update(self, params):
        return json.loads(self._run("UpdatePartParameters", params))

    def test_edit_applies_and_reports_exactly_what_changed(self):
        self._restore_ir()
        result = self._update({"part_id": "P-001", "material_spec": "SUS304",
                               "feature_updates": [{"feature_index": 0,
                                                    "field": "thickness", "value": 0.8}],
                               "reason": "客户确认改用不锈钢"})
        self.assertTrue(result["applied"])
        self.assertTrue(result["requires_regeneration"])   # 尺寸变了，几何要重生
        fields = {change["field"]: change for change in result["changes"]}
        self.assertEqual(fields["material.spec"]["old"], "VCM 覆膜板")
        self.assertEqual(fields["material.spec"]["new"], "SUS304")
        self.assertEqual(fields["features[0].thickness"]["old"], 0.5)
        self.assertEqual(fields["features[0].thickness"]["new"], 0.8)

        saved = self._store.load_ir(self.project_id)
        part = next(p for p in saved["parts"] if p["part_id"] == "P-001")
        self.assertEqual(part["material"]["spec"], "SUS304")
        self.assertEqual(part["features"][0]["thickness"], 0.8)
        self._restore_ir()

    def test_edit_leaves_other_parts_untouched(self):
        self._restore_ir()
        self._update({"part_id": "P-001", "quantity": 4})
        saved = self._store.load_ir(self.project_id)
        other = next(p for p in saved["parts"] if p["part_id"] == "P-002")
        self.assertEqual(other["material"]["spec"], "HIPS")
        self.assertEqual(other["features"][0]["length"], 1180)
        self._restore_ir()

    def test_edit_records_who_asked_for_it(self):
        """审计要记真实操作人 —— Agent 跑在 worker 线程里，拿不到就只会记 system。"""
        self._restore_ir()
        token = self.agent_module._ACTOR.set("zhang.san")
        try:
            self._update({"part_id": "P-001", "quantity": 9, "reason": "BOM 对齐"})
        finally:
            self.agent_module._ACTOR.reset(token)
        entry = next(item for item in reversed(self._store.list_audit(self.project_id))
                     if item.get("action") == "agent_part_edit")
        self.assertEqual(entry["detail"]["by"], "zhang.san")
        self.assertEqual(entry["detail"]["part_id"], "P-001")
        self.assertEqual(entry["detail"]["reason"], "BOM 对齐")
        self._restore_ir()

    def test_rejections_come_back_as_tool_results_not_exceptions(self):
        """工具被拒绝要变成模型看得懂的结果，而不是中断整轮对话。"""
        self._restore_ir()
        cases = {
            "未知零件": {"part_id": "P-999", "quantity": 2},
            "缺 part_id": {"quantity": 2},
            "特征序号越界": {"part_id": "P-001",
                             "feature_updates": [{"feature_index": 7, "field": "thickness",
                                                  "value": 1}]},
            "字段不在白名单": {"part_id": "P-001",
                               "feature_updates": [{"feature_index": 0, "field": "diameter",
                                                    "value": 5}]},
            "尺寸非正": {"part_id": "P-001",
                         "feature_updates": [{"feature_index": 0, "field": "thickness",
                                              "value": 0}]},
        }
        for label, params in cases.items():
            with self.subTest(case=label):
                result = self._update(params)
                self.assertIn("error", result)
                self.assertNotEqual(result.get("applied"), True)
        # 一次都没写进去。
        saved = self._store.load_ir(self.project_id)
        part = next(p for p in saved["parts"] if p["part_id"] == "P-001")
        self.assertEqual(part["features"][0]["thickness"], 0.5)
        self.assertEqual(len(part["features"]), 1)

    def test_no_op_edit_is_not_reported_as_a_change(self):
        """把原值再写一遍不算修改，否则版本历史里全是空版本。"""
        self._restore_ir()
        before = len(self._store.list_versions(self.project_id))
        result = self._update({"part_id": "P-001", "material_spec": "VCM 覆膜板"})
        self.assertFalse(result["applied"])
        self.assertEqual(result["changes"], [])
        self.assertEqual(len(self._store.list_versions(self.project_id)), before)

    def test_imported_3d_projects_refuse_text_edits(self):
        """精确 STEP 实体改 IR 只会让两者脱节，必须拒绝。"""
        self._restore_ir()
        meta = self._store.load_meta(self.project_id)
        original = meta["source_filename"]
        meta["source_filename"] = "门体总成.step"
        self._store._meta().put_meta(self.project_id, meta)
        try:
            result = self._update({"part_id": "P-001", "quantity": 3})
            self.assertIn("STEP", result["error"])
            self.assertFalse(result["applied"])
            saved = self._store.load_ir(self.project_id)
            part = next(p for p in saved["parts"] if p["part_id"] == "P-001")
            self.assertEqual(part["quantity"], 1)
        finally:
            meta["source_filename"] = original
            self._store._meta().put_meta(self.project_id, meta)
            self._restore_ir()

    def test_edit_is_the_only_tool_that_changes_the_ir(self):
        """写口子只能有这一个：把其余每个工具都跑一遍，IR 必须原封不动。

        用行为验证而不是扫描工具描述里的关键词 —— 描述是给模型看的自然语言，
        拿它当契约，改一句措辞就会误判。
        """
        self.assertEqual(set(self.agent_module.READONLY_DISABLED_TOOLS),
                         {"Write", "Edit", "Bash"})
        self._restore_ir()
        before = json.dumps(self._store.load_ir(self.project_id), sort_keys=True)
        for schema in self.agent_module.PLATFORM_TOOL_SCHEMAS:
            name = schema["name"]
            if name == "UpdatePartParameters":
                continue
            with self.subTest(tool=name):
                try:
                    self._run(name, {"part_id": "P-001", "quantity": 5,
                                     "material_spec": "SUS304", "reason": "x"})
                except Exception:
                    # 查库类工具在没有知识库的测试环境里会抛错；这里要验的是
                    # 「有没有写 IR」，抛错的那次同样要检查，不能直接跳过。
                    pass
                self.assertEqual(
                    json.dumps(self._store.load_ir(self.project_id), sort_keys=True), before,
                    f"{name} 改动了 IR —— 只读工具不该写业务数据")

    # ------------------------------------------------------------ 工具表约定
    def test_platform_tools_are_declared_with_schemas(self):
        names = {schema["name"] for schema in self.agent_module.PLATFORM_TOOL_SCHEMAS}
        self.assertEqual(names, self.agent_module.PLATFORM_TOOL_NAMES)
        self.assertEqual(names, {"GetProjectState", "ListParts", "GetPartDetail",
                                 "GetOpenQuestions", "RequestParse",
                                 "LookupComponentLibrary", "LookupProcessLibrary",
                                 "LookupCostLibrary", "UpdatePartParameters"})
        for schema in self.agent_module.PLATFORM_TOOL_SCHEMAS:
            with self.subTest(tool=schema["name"]):
                self.assertTrue(schema["description"].strip())
                self.assertEqual(schema["input_schema"]["type"], "object")

    def test_ui_actions_are_limited_to_the_two_that_need_the_page(self):
        """只有需要界面配合的工具才映射成 ui_action，其余一律纯数据返回。"""
        self.assertEqual(self.agent_module.UI_ACTION_TOOLS,
                         {"RequestParse": "parse",
                          "UpdatePartParameters": "refresh-ir"})

    def test_write_tools_are_disabled_in_web_mode(self):
        self.assertEqual(set(self.agent_module.READONLY_DISABLED_TOOLS),
                         {"Write", "Edit", "Bash"})

    # ------------------------------------------------------------ SSE
    def test_sse_frames_are_well_formed(self):
        frame = self.agent_module.sse({"type": "text", "text": "厚度 0.5mm"})
        self.assertTrue(frame.startswith("data: "))
        self.assertTrue(frame.endswith("\n\n"))
        payload = json.loads(frame[len("data: "):].strip())
        self.assertEqual(payload["text"], "厚度 0.5mm")

    def test_sse_keeps_chinese_readable(self):
        self.assertIn("厚度", self.agent_module.sse({"type": "text", "text": "厚度"}))

    # ------------------------------------------------------------ 可用性探测
    def test_available_explains_a_missing_api_key(self):
        module = self.agent_module
        saved = os.environ.get("ANTHROPIC_API_KEY", "")
        os.environ["ANTHROPIC_API_KEY"] = ""
        try:
            ok, reason = module.available()
        finally:
            if saved:
                os.environ["ANTHROPIC_API_KEY"] = saved
            else:
                os.environ.pop("ANTHROPIC_API_KEY", None)
        self.assertFalse(ok)
        self.assertIn("ANTHROPIC_API_KEY", reason)

    def test_open_claude_directory_is_resolved_inside_the_repo(self):
        self.assertEqual(self.agent_module.OPEN_CLAUDE_DIR.name, "open-claude")
        self.assertTrue((self.agent_module.OPEN_CLAUDE_DIR / "open_claude").is_dir())

    def test_workdir_is_the_project_data_dir(self):
        workdir = self.agent_module._project_workdir(self.project_id)
        self.assertEqual(workdir.name, self.project_id)
        self.assertTrue(workdir.is_dir())


@unittest.skipUnless(os.getenv("ANTHROPIC_API_KEY", "").strip(),
                     "需要 ANTHROPIC_API_KEY 才能实例化 open-claude Conversation")
class OcAgentDispatchTests(unittest.TestCase):
    """验证 execute_tool 的前置分派：平台工具自处理，其余原样转交 open-claude。"""

    def test_dispatch_wraps_but_does_not_replace_open_claude(self):
        from backend.services import oc_agent

        ok, reason = oc_agent.available()
        if not ok:
            self.skipTest(reason)
        agent = oc_agent.get_agent("dispatch-probe")
        import open_claude.repl as oc_repl

        self.assertTrue(hasattr(oc_repl.execute_tool, "__wrapped__"),
                        "分派层未安装或被覆盖")
        # 平台工具走自己的实现。
        self.assertIn("project_id", oc_repl.execute_tool("GetProjectState", {}, agent.cwd))
        # 未知工具仍由 open-claude 回答，而不是被分派层吞掉。
        self.assertIn("Unknown tool", oc_repl.execute_tool("NoSuchTool", {}, agent.cwd))
        oc_agent.drop_agent("dispatch-probe")

    def test_tool_exception_becomes_a_tool_result_not_a_crash(self):
        """工具内部异常必须变成工具结果文本，否则整轮对话会被打断。"""
        from backend.services import oc_agent

        ok, reason = oc_agent.available()
        if not ok:
            self.skipTest(reason)
        agent = oc_agent.get_agent("error-probe")
        import open_claude.repl as oc_repl

        original = oc_agent._project_state
        oc_agent._project_state = lambda _pid: (_ for _ in ()).throw(RuntimeError("磁盘故障"))
        try:
            text = oc_repl.execute_tool("GetProjectState", {}, agent.cwd)
        finally:
            oc_agent._project_state = original
            oc_agent.drop_agent("error-probe")
        self.assertIn("磁盘故障", text)
        self.assertIn("执行失败", text)

    def test_conversation_hides_write_tools_and_exposes_platform_tools(self):
        from backend.services import oc_agent

        ok, reason = oc_agent.available()
        if not ok:
            self.skipTest(reason)
        agent = oc_agent.get_agent("surface-probe")
        try:
            names = {tool["name"] for tool in agent.meta()["tools"]}
            self.assertTrue(oc_agent.PLATFORM_TOOL_NAMES <= names)
            self.assertFalse(names & set(oc_agent.READONLY_DISABLED_TOOLS))
            self.assertIn("Read", names)          # 只读能力保留
            self.assertIn("平台", agent.conv.system_prompt or "")
        finally:
            oc_agent.drop_agent("surface-probe")


if __name__ == "__main__":
    unittest.main()

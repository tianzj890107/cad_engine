"""图纸拆解阶段的零部件库检索。

这里守的是三件事：
  - IR 的特征能正确翻成漏斗要的检索参数（否则精筛全落空，什么都匹配不上）；
  - 三档结论（可复用 / 可改制 / 未匹配）由分数与 match_type 唯一决定；
  - 检索过程逐条上报，且上报失败不能连累检索本身 —— 对话框里的处理过程就靠它。

漏斗本身（kb_repo.recommend_components）在 test_da_db.py 里另测，这里把它打桩，
让结论逻辑与知识库内容解耦。
"""
from __future__ import annotations

import unittest
from unittest import mock

from backend.services import component_match


PLATE_PART = {
    "part_id": "P-001",
    "name": "设备安装底板",
    "material": {"spec": "SUS304"},
    "features": [
        {"type": "plate", "length": 200.0, "width": 120.0, "thickness": 2.0},
        {"type": "hole", "diameter": 6.5},
    ],
}


def candidate(score: float, match_type: str = "param_near", **extra) -> dict:
    base = {
        "component_id": 1, "component_code": "CMP-PLT-0001", "name": "标准安装底板",
        "score": score, "match_type": match_type, "gap_notes": "",
    }
    base.update(extra)
    return base


class BuildQueryTests(unittest.TestCase):
    def test_plate_and_hole_features_become_lookup_params(self):
        params = component_match.build_query(PLATE_PART)["params"]
        self.assertEqual(params["length"], 200.0)
        self.assertEqual(params["width"], 120.0)
        self.assertEqual(params["thickness"], 2.0)
        self.assertEqual(params["hole_diameter"], 6.5)

    def test_cylinder_and_box_use_the_same_param_vocabulary(self):
        query = component_match.build_query({
            "features": [
                {"type": "cylinder", "diameter": 30.0, "height": 80.0},
                {"type": "box", "length": 10.0, "width": 11.0, "height": 12.0},
            ],
        })
        self.assertEqual(query["params"]["diameter"], 30.0)
        # 同名参数取第一个非空值：圆柱先出现，高度不该被后面的方盒盖掉。
        self.assertEqual(query["params"]["height"], 80.0)

    def test_zero_and_missing_dimensions_are_dropped(self):
        params = component_match.build_query({
            "features": [{"type": "plate", "length": 0, "width": None, "thickness": 3.0}],
        })["params"]
        self.assertEqual(params, {"thickness": 3.0})

    def test_feature_type_key_variants_are_both_accepted(self):
        params = component_match.build_query({
            "features": [{"feature_type": "PLATE", "thickness": 4.0}],
        })["params"]
        self.assertEqual(params["thickness"], 4.0)

    def test_material_spec_survives_a_plain_string(self):
        with mock.patch.object(component_match.kb_repo, "list_materials", return_value=[]):
            query = component_match.build_query({"features": [], "material": "Q235"})
        self.assertEqual(query["material_spec"], "Q235")

    def test_material_code_is_looked_up_not_guessed(self):
        rows = [{"material_code": "MAT-SUS304", "grade": "SUS304"}]
        with mock.patch.object(component_match.kb_repo, "list_materials", return_value=rows):
            self.assertEqual(component_match.build_query(PLATE_PART)["material_code"], "MAT-SUS304")

    def test_unknown_grade_leaves_the_code_empty(self):
        rows = [{"material_code": "MAT-A", "grade": "6061"}]
        with mock.patch.object(component_match.kb_repo, "list_materials", return_value=rows):
            self.assertIsNone(component_match.build_query(PLATE_PART)["material_code"])


class DecisionTests(unittest.TestCase):
    def match(self, candidates):
        with mock.patch.object(component_match.kb_repo, "list_materials", return_value=[]), \
             mock.patch.object(component_match.kb_repo, "recommend_components", return_value=candidates):
            return component_match.match_part(PLATE_PART)

    def test_high_score_is_directly_reusable(self):
        result = self.match([candidate(0.95)])
        self.assertEqual(result["decision"], "reuse")
        self.assertEqual(result["decision_label"], "可直接复用")
        self.assertTrue(result["matched"])
        self.assertEqual(result["component_code"], "CMP-PLT-0001")

    def test_exact_match_is_reusable_regardless_of_score(self):
        """标准件命中就是命中，不该因为分数没到 0.90 被降级成可改制。"""
        self.assertEqual(self.match([candidate(0.71, "exact")])["decision"], "reuse")

    def test_middling_score_is_modifiable(self):
        result = self.match([candidate(0.62, gap_notes="length: 库内 200.0mm / 图纸 210.0")])
        self.assertEqual(result["decision"], "modify")
        self.assertTrue(result["matched"])
        self.assertIn("length", result["gap_notes"])

    def test_low_score_falls_through_to_new(self):
        result = self.match([candidate(0.40)])
        self.assertEqual(result["decision"], "new")
        self.assertFalse(result["matched"])
        # 未匹配时不能残留候选编码，否则界面上会显示一个并不成立的命中。
        self.assertIsNone(result["component_code"])

    def test_empty_library_yields_new(self):
        result = self.match([])
        self.assertEqual(result["decision"], "new")
        self.assertEqual(result["match_type"], "none")
        self.assertEqual(result["score"], 0.0)

    def test_thresholds_are_the_only_boundary(self):
        self.assertEqual(self.match([candidate(component_match.REUSE_SCORE)])["decision"], "reuse")
        self.assertEqual(self.match([candidate(component_match.MODIFY_SCORE)])["decision"], "modify")
        self.assertEqual(
            self.match([candidate(component_match.MODIFY_SCORE - 0.01)])["decision"], "new")

    def test_candidates_are_kept_for_the_detail_view(self):
        result = self.match([candidate(0.95), candidate(0.7, component_code="CMP-PLT-0002")])
        self.assertEqual(len(result["candidates"]), 2)


class ProjectReportTests(unittest.TestCase):
    IR = {"parts": [
        dict(PLATE_PART),
        {"part_id": "P-002", "name": "异形壳体", "features": []},
        {"part_id": "P-003", "name": "改型底板", "features": PLATE_PART["features"]},
    ]}

    def run_project(self, progress=None):
        scores = iter([[candidate(0.98)], [], [candidate(0.66)]])

        def fake_recommend(query, limit=3):
            return next(scores)

        with mock.patch.object(component_match.kb_repo, "list_materials", return_value=[]), \
             mock.patch.object(component_match.kb_repo, "recommend_components", side_effect=fake_recommend), \
             mock.patch.object(component_match.kb_repo, "list_components", return_value=[{}] * 12):
            return component_match.match_project("proj-1", self.IR, progress=progress)

    def test_summary_counts_the_three_buckets(self):
        summary = self.run_project()["summary"]
        self.assertEqual(summary, {"total": 3, "reuse": 1, "modify": 1, "new": 1, "matched": 2})

    def test_report_records_library_size_and_thresholds(self):
        report = self.run_project()
        self.assertEqual(report["library_size"], 12)
        self.assertEqual(report["thresholds"]["reuse"], component_match.REUSE_SCORE)
        self.assertTrue(report["generated_at"])

    def test_every_step_is_reported_for_the_chat_timeline(self):
        lines: list[str] = []
        self.run_project(progress=lines.append)
        joined = "\n".join(lines)
        self.assertIn("零部件库检索开始：3 个零件", joined)
        # 查询条件本身也要播出去：只报结论的话，匹配不上时无从判断
        # 是库里没有还是条件提错了。
        self.assertIn("查询条件：", joined)
        self.assertIn("length=200", joined)
        self.assertIn("检索零部件库（2/3）", joined)
        # 命中与未命中用不同措辞，前端据此给出不同标记。
        self.assertIn("命中 CMP-PLT-0001", joined)
        self.assertIn("库内无同类件", joined)
        self.assertIn("可复用 1、可改制 1、未匹配 1", joined)

    def test_progress_failures_do_not_break_the_lookup(self):
        def boom(_message):
            raise RuntimeError("SSE 断了")

        self.assertEqual(self.run_project(progress=boom)["summary"]["total"], 3)

    def test_empty_ir_is_not_an_error(self):
        with mock.patch.object(component_match.kb_repo, "list_components", return_value=[]):
            report = component_match.match_project("proj-1", {}, progress=None)
        self.assertEqual(report["summary"]["total"], 0)
        self.assertEqual(report["items"], [])


class PersistenceTests(unittest.TestCase):
    REPORT = {"items": [{"part_id": "P-001", "candidates": [candidate(0.98)]}], "summary": {}}

    def test_report_goes_to_the_project_store(self):
        with mock.patch.object(component_match.store, "save_component_match") as save, \
             mock.patch.object(component_match, "_persist_to_da"):
            component_match.save_report("proj-1", self.REPORT)
        save.assert_called_once_with("proj-1", self.REPORT)

    def test_da_write_failure_is_swallowed_and_audited(self):
        """DA 库还与主流程并行，写不进去不能让拆解任务失败。"""
        with mock.patch.object(component_match.store, "load_ir", side_effect=RuntimeError("no db")), \
             mock.patch.object(component_match.store, "audit") as audit:
            component_match._persist_to_da("proj-1", self.REPORT)
        self.assertEqual(audit.call_args[0][1], "component_match_da_skip")


class ProgressLogTests(unittest.TestCase):
    """进度必须是只增不改的日志。

    这是"检索过程在对话框里看不见"的真正原因：progress 是单值字段，而零部件库
    检索是纯本地查询，几十条进度可能在一次前端轮询（1.2s）内全部播完，覆盖式写
    法只会剩最后一条，界面上看起来就是"一步都没有"。
    """

    def setUp(self):
        from backend.services import tasks
        from backend.storage import store

        self.tasks, self.store = tasks, store
        self.record = {"task_id": "t-1", "progress": "排队中", "progress_log": []}
        self.doc = {"items": [self.record]}

    def _patched_store(self):
        return mock.patch.object(self.store, "_meta", return_value=mock.Mock(
            get_doc=mock.Mock(return_value=self.doc), put_doc=mock.Mock()))

    def test_append_keeps_every_line_and_tracks_the_latest(self):
        with self._patched_store():
            for line in ("检索零部件库（1/2）", "  ↳ 命中 CMP-0001", "检索零部件库（2/2）"):
                self.store.append_task_progress("proj-1", "t-1", line)
        self.assertEqual(self.record["progress_log"],
                         ["检索零部件库（1/2）", "  ↳ 命中 CMP-0001", "检索零部件库（2/2）"])
        self.assertEqual(self.record["progress"], "检索零部件库（2/2）")

    def test_identical_lines_are_kept_separately(self):
        """两个零件都没匹配上就该出现两条，不能被当成重复吞掉。"""
        with self._patched_store():
            self.store.append_task_progress("proj-1", "t-1", "  ↳ 库内无同类件，按新制评估")
            self.store.append_task_progress("proj-1", "t-1", "  ↳ 库内无同类件，按新制评估")
        self.assertEqual(len(self.record["progress_log"]), 2)

    def test_log_is_bounded(self):
        with self._patched_store():
            for index in range(self.store.PROGRESS_LOG_LIMIT + 25):
                self.store.append_task_progress("proj-1", "t-1", f"步骤 {index}")
        self.assertEqual(len(self.record["progress_log"]), self.store.PROGRESS_LOG_LIMIT)
        self.assertEqual(self.record["progress_log"][-1],
                         f"步骤 {self.store.PROGRESS_LOG_LIMIT + 24}")

    def test_report_progress_appends_instead_of_overwriting(self):
        token = self.tasks._CURRENT_TASK.set(("proj-1", "t-1", "parse", 0))
        try:
            with mock.patch.object(self.tasks.store, "append_task_progress") as append, \
                 mock.patch.object(self.tasks.store, "update_task"):
                self.tasks.report_progress("检索零部件库（1/3）")
        finally:
            self.tasks._CURRENT_TASK.reset(token)
        append.assert_called_once_with("proj-1", "t-1", "检索零部件库（1/3）")


if __name__ == "__main__":
    unittest.main()

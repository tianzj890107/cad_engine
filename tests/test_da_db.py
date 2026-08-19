"""DA 数据库(SQLite)与知识库图库的离线回归测试。

覆盖:建库与外键约束、图库文件夹扫描登记、零部件推荐三级漏斗、
按时点取价与费率、成本合计的确定性重算、IR 落表与回读、报告冻结快照。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path


class DaDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 测试库与图库都放临时目录,绝不碰用户的 data/。
        cls.tmp = Path(tempfile.mkdtemp(prefix="da_test_"))
        os.environ["DA_DB_PATH"] = str(cls.tmp / "da.db")
        os.environ["KB_DIR"] = str(cls.tmp / "kb")
        os.environ["DATA_DIR"] = str(cls.tmp)

        import backend.config as config
        config.DA_DB_PATH = cls.tmp / "da.db"
        config.KB_DIR = cls.tmp / "kb"
        config.DATA_DIR = cls.tmp

        from backend.storage import da_db, da_repo, da_seed, kb_library, kb_repo
        cls.db = da_db
        cls.kb = kb_repo
        cls.lib = kb_library
        cls.repo = da_repo
        cls.seed = da_seed

        da_db.init_db(force=True)
        kb_library.ensure_kb_dirs()
        da_seed.seed_all()

    @classmethod
    def tearDownClass(cls):
        cls.db.close_conn()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- 建库
    def test_schema_creates_all_layers(self):
        tables = set(self.db.table_names())
        for expected in (
            "kb_component", "kb_component_param", "kb_component_feature",
            "kb_component_drawing", "kb_process_step", "kb_process_route",
            "kb_material", "kb_material_price", "kb_cost_rate", "kb_cost_factor",
            "src_project", "src_requirement", "src_input_file",
            "wip_design_ir", "wip_part", "wip_part_feature", "wip_component_match",
            "wip_cost_estimate", "wip_cost_item",
            "out_process_report", "out_report_snapshot", "out_cost_result",
            "ops_audit", "ops_llm_call", "ops_kb_promotion",
        ):
            self.assertIn(expected, tables)

    def test_foreign_keys_are_enforced(self):
        import sqlite3
        # 外键若没开启,这行会被静默写入,DA 的回指约束就成了摆设。
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO kb_component_param (component_id, param_key) VALUES ('NOPE', 'length')"
            )

    def test_enum_check_constraints_reject_bad_values(self):
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO kb_process_step (step_code, name, process_type) "
                "VALUES ('PS-BAD', '错误工序', 'not_a_process')"
            )

    # ------------------------------------------------------------ 图库文件夹
    def test_drawing_folders_are_the_source_of_truth(self):
        code = "CMP-PLT-0001"
        self.lib.put_component_file(code, "2d", "底板.dxf", b"0\nSECTION\n", rev="A")
        self.lib.put_component_file(code, "3d", "base_plate.step", b"ISO-10303-21;", rev="A")
        self.lib.put_component_file(code, "doc", "检验规范.pdf", b"%PDF-1.4", rev="A")

        result = self.kb.sync_component_drawings(code)
        self.assertEqual(result["added"], 3)

        component = self.kb.get_component(code)
        kinds = {d["drawing_kind"] for d in component["drawings"]}
        self.assertEqual(kinds, {"2d", "3d", "doc"})

        # 中文名落盘时规范化,原名仍可追溯。
        dxf = next(d for d in component["drawings"] if d["drawing_kind"] == "2d")
        self.assertNotIn("底板", Path(dxf["file_path"]).name)
        self.assertTrue(dxf["file_sha256"])
        self.assertTrue((self.lib.blob_root() / dxf["file_path"]).exists())

    def test_directory_names_keep_standard_numbers_intact(self):
        # 'GB/T 97.1' 的 '.1' 是标准号的一部分,不是扩展名。
        self.assertEqual(self.lib.safe_segment("GB/T 97.1"), "GB-T-97.1")
        self.assertEqual(self.lib.safe_segment("GB/T 5783"), "GB-T-5783")
        self.assertEqual(self.lib.safe_segment("CMP-PLT-0001"), "CMP-PLT-0001")
        # 不同中文名不得塌缩成同一个目录。
        self.assertNotEqual(self.lib.safe_segment("底板"), self.lib.safe_segment("盖板"))

    def test_new_revision_supersedes_old_one(self):
        code = "CMP-BRK-0001"
        self.lib.put_component_file(code, "2d", "bracket.dxf", b"rev-a", rev="A")
        self.kb.sync_component_drawings(code)
        self.lib.put_component_file(code, "2d", "bracket.dxf", b"rev-b", rev="B")
        self.kb.sync_component_drawings(code)

        drawings = [d for d in self.kb.get_component(code)["drawings"] if d["drawing_kind"] == "2d"]
        current = {d["rev"]: d["is_current"] for d in drawings}
        self.assertEqual(current, {"A": 0, "B": 1})

    def test_deleted_file_is_removed_from_index(self):
        code = "CMP-PLT-0001"
        meta = self.lib.put_component_file(code, "2d", "临时图.dxf", b"tmp", rev="A")
        self.kb.sync_component_drawings(code)
        (self.lib.blob_root() / meta["file_path"]).unlink()
        result = self.kb.sync_component_drawings(code)
        self.assertEqual(result["removed"], 1)

    # -------------------------------------------------------- 零部件推荐漏斗
    def test_recommend_ranks_matching_component_first(self):
        part = {
            "name": "安装底板",
            "features": [
                {"type": "plate", "length": 200, "width": 120, "thickness": 10},
                {"type": "hole_pattern", "diameter": 9, "count_x": 2, "count_y": 2},
            ],
            "params": {"length": 200, "width": 120, "thickness": 10, "hole_diameter": 9},
        }
        matches = self.kb.recommend_components(part)
        self.assertTrue(matches)
        self.assertEqual(matches[0]["component_code"], "CMP-PLT-0001")
        self.assertEqual(matches[0]["match_type"], "exact")
        self.assertGreater(matches[0]["score"], 0.9)

    def test_recommend_reports_gaps_for_near_miss(self):
        part = {
            "name": "改型底板",
            "features": [{"type": "plate", "length": 210, "width": 125, "thickness": 12}],
            "params": {"length": 210, "width": 125, "thickness": 12},
        }
        matches = self.kb.recommend_components(part)
        top = next(m for m in matches if m["component_code"] == "CMP-PLT-0001")
        self.assertIn("thickness", top["gap_notes"])   # 12 超出 ±1 允差,应提示改制
        self.assertNotEqual(top["match_type"], "exact")

    def test_recommend_filters_out_wrong_size_class(self):
        part = {
            "name": "大型机架",
            "features": [{"type": "plate", "length": 2000, "width": 1200, "thickness": 20}],
            "params": {"length": 2000},
        }
        codes = [m["component_code"] for m in self.kb.recommend_components(part)]
        self.assertNotIn("CMP-PLT-0001", codes)   # 包络粗筛应淘汰

    def test_scores_are_reproducible(self):
        part = {"features": [{"type": "plate", "length": 200, "width": 120, "thickness": 10}],
                "params": {"length": 200}}
        first = self.kb.recommend_components(part)
        second = self.kb.recommend_components(part)
        self.assertEqual(first, second)

    # ------------------------------------------------------ 工艺步骤 / 路线
    def test_features_recall_process_steps(self):
        steps = {s["step_code"] for s in self.kb.steps_for_features(["hole_pattern"])}
        self.assertIn("PS-DRILL-HOLE", steps)
        self.assertNotIn("PS-TURN-ROUGH", steps)

    def test_route_expands_with_step_details(self):
        route = self.kb.get_route("RT-PLATE-MACHINED")
        self.assertEqual([s["seq"] for s in route["steps"]], [10, 20, 30, 40, 50, 60, 70])
        self.assertEqual(route["steps"][1]["depends_on"], [10])
        self.assertEqual(route["steps"][0]["step"]["name"], "激光下料")

    def test_route_recommendation_prefers_matching_category(self):
        routes = self.kb.recommend_routes(category="钣金件", material_category="金属")
        self.assertEqual(routes[0]["route_code"], "RT-SHEET-BOX")

    # --------------------------------------------------------- 价格 / 费率
    def test_price_lookup_is_time_scoped(self):
        self.kb.add_material_price({
            "material_code": "MAT-STL-Q235", "price": 4.9, "unit": "kg",
            "price_type": "internal_purchase", "valid_from": "2026-06-01 00:00:00",
        })
        old = self.kb.current_price("MAT-STL-Q235", at="2026-03-01 00:00:00")
        new = self.kb.current_price("MAT-STL-Q235", at="2026-08-01 00:00:00")
        self.assertEqual(old["price"], 4.2)
        self.assertEqual(new["price"], 4.9)

    def test_rate_falls_back_to_global_scope(self):
        specific = self.kb.effective_rate("labor", scope_type="equipment_class",
                                          scope_ref="EQC-CNC-VMC")
        fallback = self.kb.effective_rate("labor", scope_type="equipment_class",
                                          scope_ref="EQC-UNKNOWN")
        self.assertEqual(specific["rate_code"], "RATE-LABOR-CNC")
        self.assertEqual(fallback["rate_code"], "RATE-LABOR-GLOBAL")

    def test_supplier_matching_is_deterministic(self):
        matches = self.kb.match_suppliers({
            "material_code": "MAT-CER-AL2O3-96", "purity_pct_min": 99.9, "d50_um_max": 3.0,
        })
        self.assertTrue(matches)
        self.assertFalse(matches[0]["qualified"])
        self.assertIn("纯度", matches[0]["gap_notes"])

    def test_standard_part_lookup_from_spec_string(self):
        found = self.kb.find_standard_part("GB/T 5783 M8x25")
        self.assertEqual(found["category"], "bolt")

    # ------------------------------------------------------------ 项目侧落表
    def test_design_ir_round_trip(self):
        project_id = "abc123456789"
        self.repo.ensure_project(project_id, {"name": "测试设备"})
        ir = {
            "device_name": "测试设备", "design_intent": "验证 IR 落表",
            "overall_dims": "200 x 120 x 95 mm",
            "assemblies": [{"assembly_id": "A-001", "name": "底架总成", "quantity": 1}],
            "parts": [{
                "part_id": "P-001", "name": "底板", "parent_id": "A-001", "quantity": 2,
                "confidence": 0.82, "material": {"spec": "Q235"},
                "tolerance_general": "ISO 2768-m",
                "features": [{"type": "plate", "length": 200, "width": 120, "thickness": 10}],
                "provenance": {"bbox": [0.1, 0.2, 0.3, 0.4], "note": "主视图"},
            }],
            "standard_parts": [{"spec": "GB/T 5783 M8x25", "category": "bolt", "quantity": 8}],
            "open_questions": [{"field": "P-001.thickness", "reason": "标注模糊"}],
        }
        ir_id = self.repo.save_design_ir(project_id, ir, model_name="qwen3-vl-plus")

        loaded = self.repo.load_design_ir(project_id)
        self.assertEqual(loaded["device_name"], "测试设备")
        self.assertEqual(len(loaded["parts"]), 1)
        part = loaded["parts"][0]
        self.assertEqual(part["quantity"], 2)
        self.assertEqual(part["features"][0]["thickness"], 10)
        self.assertEqual(part["provenance"]["bbox"], [0.1, 0.2, 0.3, 0.4])

        questions = self.db.query(
            "SELECT * FROM wip_open_question WHERE project_id = ? AND stage = '2.1'", (project_id,)
        )
        self.assertEqual(len(questions), 1)
        return ir_id

    def test_recompute_ir_replaces_previous_parts(self):
        project_id = "def123456789"
        self.repo.ensure_project(project_id, {"name": "重算设备"})
        base = {"device_name": "重算设备", "design_intent": "x",
                "parts": [{"part_id": "P-001", "name": "旧件", "features": []},
                          {"part_id": "P-002", "name": "另一件", "features": []}]}
        self.repo.save_design_ir(project_id, base, version=1)
        self.repo.save_design_ir(
            project_id, {**base, "parts": [{"part_id": "P-001", "name": "新件", "features": []}]},
            version=1,
        )
        loaded = self.repo.load_design_ir(project_id, version=1)
        self.assertEqual([p["name"] for p in loaded["parts"]], ["新件"])

    def test_component_match_decision_bumps_reuse_count(self):
        project_id = "aaa111222333"
        self.repo.ensure_project(project_id, {"name": "复用测试"})
        ir_id = self.repo.save_design_ir(project_id, {
            "device_name": "复用测试", "design_intent": "x",
            "parts": [{"part_id": "P-001", "name": "底板",
                       "features": [{"type": "plate", "length": 200, "width": 120, "thickness": 10}]}],
        })
        matches = self.kb.recommend_components({
            "features": [{"type": "plate", "length": 200, "width": 120, "thickness": 10}],
            "params": {"length": 200, "width": 120, "thickness": 10},
        })
        self.repo.save_component_matches(ir_id, "P-001", matches)

        before = self.kb.get_component("CMP-PLT-0001")["reuse_count"]
        row = self.db.query_one(
            "SELECT match_id FROM wip_component_match WHERE ir_id = ? ORDER BY score DESC LIMIT 1",
            (ir_id,),
        )
        self.repo.decide_component_match(row["match_id"], "reuse", "工艺员甲")
        after = self.kb.get_component("CMP-PLT-0001")["reuse_count"]
        self.assertEqual(after, before + 1)

    # --------------------------------------------------------------- 成本
    def test_cost_totals_are_recomputed_not_trusted(self):
        project_id = "bbb111222333"
        self.repo.ensure_project(project_id, {"name": "成本测试"})
        price = self.kb.current_price("MAT-STL-Q235", at="2026-03-01 00:00:00")
        estimate = {
            "currency": "CNY", "batch_size": 100,
            "material_costs": [{
                "item": "Q235 板", "unit_usage": 1.88, "unit": "kg", "unit_price": price["price"],
                "amount": 999.0,                      # 模型给的错值,应被重算覆盖
                "material_code": "MAT-STL-Q235", "price_id": price["price_id"], "source": "kb",
            }],
            "manufacturing_costs": [{
                "process": "精铣成型", "labor_cost": 12.0, "equipment_depreciation": 6.0,
                "energy_cost": 1.5, "subtotal": 0.0,  # 同上
                "step_code": "PS-MILL-FINISH", "rate_code": "RATE-LABOR-CNC",
            }],
            "technical_costs": [{"item": "首件验证分摊", "amount": 3.0, "basis": "一次性投入÷批量"}],
            "logistics_costs": [{"item": "运输", "amount": 1.2, "rate_code": "RATE-LOGISTICS"}],
        }
        self.repo.save_cost_estimate(project_id, estimate)

        loaded = self.repo.load_cost_estimate(project_id)
        material_item = next(i for i in loaded["items"] if i["cost_type"] == "material")
        self.assertAlmostEqual(material_item["amount"], round(1.88 * 4.2, 6))
        manufacturing_item = next(i for i in loaded["items"] if i["cost_type"] == "manufacturing")
        self.assertAlmostEqual(manufacturing_item["amount"], 19.5)
        self.assertAlmostEqual(loaded["grand_total"],
                               round(1.88 * 4.2 + 19.5 + 3.0 + 1.2, 6))
        # 回指主键必须落库,否则报告里的单价无法复现。
        self.assertEqual(material_item["price_id"], price["price_id"])
        self.assertEqual(manufacturing_item["rate_code"], "RATE-LABOR-CNC")

    def test_cost_estimate_replaces_items_on_rerun(self):
        project_id = "ccc111222333"
        self.repo.ensure_project(project_id, {"name": "重算成本"})
        self.repo.save_cost_estimate(project_id, {
            "material_costs": [{"item": "A", "unit_usage": 1, "unit_price": 10}],
        })
        self.repo.save_cost_estimate(project_id, {
            "material_costs": [{"item": "B", "unit_usage": 2, "unit_price": 5}],
        })
        loaded = self.repo.load_cost_estimate(project_id)
        self.assertEqual([i["name"] for i in loaded["items"]], ["B"])
        self.assertAlmostEqual(loaded["grand_total"], 10.0)

    # --------------------------------------------------------------- 报告
    def test_report_snapshot_freezes_kb_references(self):
        project_id = "ddd111222333"
        self.repo.ensure_project(project_id, {"name": "报告测试"})
        self.repo.save_requirement({
            "project_id": project_id, "requirement_no": "REQ-202608-0001",
            "title": "工艺评估需求", "status": "approved",
            "data": {"批量": "100 台", "交期": "8 周"},
        })
        price = self.kb.current_price("MAT-STL-Q235", at="2026-03-01 00:00:00")
        self.repo.save_cost_estimate(project_id, {
            "material_costs": [{
                "item": "Q235 板", "unit_usage": 2.0, "unit_price": price["price"],
                "material_code": "MAT-STL-Q235", "price_id": price["price_id"],
            }],
        })
        self.repo.save_report({
            "project_id": project_id, "report_no": "RPT-202608-0001",
            "requirement_no": "REQ-202608-0001", "status": "approved",
            "evaluation_items": [{"item": "结构可制造性", "status": "可行", "conclusion": "现有产线可满足"}],
            "stage_results": [{"stage": "2.1 图纸拆解", "conclusion": "已完成"}],
        })
        frozen = self.repo.freeze_report("RPT-202608-0001", actor="工艺技术经理")
        self.assertEqual(len(frozen["sha256"]), 64)

        snapshot = self.db.query_one(
            "SELECT * FROM out_report_snapshot WHERE report_no = ?", ("RPT-202608-0001",)
        )
        kb_refs = self.db.decode_json(snapshot["kb_refs"], {})
        self.assertIn(price["price_id"], kb_refs.get("kb_material_price", []))

        result = self.db.query_one(
            "SELECT * FROM out_cost_result WHERE report_no = ?", ("RPT-202608-0001",)
        )
        self.assertAlmostEqual(result["unit_cost"], 8.4)

        # 冻结之后知识库涨价,历史报告的数字不受影响。
        self.kb.add_material_price({
            "material_code": "MAT-STL-Q235", "price": 99.0, "unit": "kg",
            "valid_from": "2026-09-01 00:00:00",
        })
        unchanged = self.db.query_one(
            "SELECT * FROM out_cost_result WHERE report_no = ?", ("RPT-202608-0001",)
        )
        self.assertAlmostEqual(unchanged["unit_cost"], 8.4)

    def test_requirement_fields_are_tabulated(self):
        rows = self.db.query(
            "SELECT * FROM src_requirement_field WHERE requirement_no = ? ORDER BY field_key",
            ("REQ-202608-0001",),
        )
        self.assertEqual({r["field_key"] for r in rows}, {"批量", "交期"})

    # --------------------------------------------------------------- 治理
    def test_knowledge_promotion_requires_review(self):
        promo_id = self.repo.propose_promotion(
            None, source_table="wip_process_step", source_id="1",
            target_kb_table="kb_process_step", payload={"name": "新工序"},
        )
        self.assertEqual(len(self.repo.list_promotions("pending")), 1)
        self.repo.decide_promotion(promo_id, "approved", "工艺技术经理", "同意入库")
        self.assertEqual(self.repo.list_promotions("pending"), [])

    def test_llm_call_is_traceable(self):
        call_id = self.repo.record_llm_call(
            None, stage="2.1", provider="qwen", model="qwen3-vl-plus",
            prompt="拆解这张图", input_tokens=1200, output_tokens=800, latency_ms=4200,
        )
        row = self.db.query_one("SELECT * FROM ops_llm_call WHERE call_id = ?", (call_id,))
        self.assertEqual(row["model"], "qwen3-vl-plus")
        self.assertEqual(len(row["prompt_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()

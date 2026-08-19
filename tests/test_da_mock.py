"""半导体 / 电池 / 电器三行业模拟源表数据的离线回归测试。

重点不是"数据有没有写进去",而是:
  - 只写了数据源侧(kb_* / src_*),产出侧(wip_* / out_*)保持为空;
  - 外键、枚举、单位、价格时点等约束在真实行业数据上仍然成立;
  - 三条行业链路(取价、工艺路线、零部件推荐、供应商匹配)都能跑通。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path


class DaMockDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="da_mock_"))
        os.environ["DA_DB_PATH"] = str(cls.tmp / "da.db")
        os.environ["KB_DIR"] = str(cls.tmp / "kb")
        os.environ["DATA_DIR"] = str(cls.tmp)

        import backend.config as config
        config.DA_DB_PATH = cls.tmp / "da.db"
        config.KB_DIR = cls.tmp / "kb"
        config.DATA_DIR = cls.tmp

        from backend.storage import da_db, da_mock, da_repo, kb_library, kb_repo
        cls.db = da_db
        cls.mock = da_mock
        cls.kb = kb_repo
        cls.lib = kb_library
        cls.repo = da_repo

        da_db.init_db(force=True)
        kb_library.ensure_kb_dirs()
        cls.counts = da_mock.load()

    @classmethod
    def tearDownClass(cls):
        cls.db.close_conn()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ------------------------------------------------------------ 覆盖与隔离
    def test_all_three_industries_loaded(self):
        self.assertEqual(set(self.counts), {"semiconductor", "battery", "appliance"})
        for industry, counts in self.counts.items():
            with self.subTest(industry=industry):
                self.assertGreater(counts["物料"], 0)
                self.assertGreater(counts["工序"], 0)
                self.assertGreater(counts["工艺路线"], 0)
                self.assertGreater(counts["零部件"], 0)
                self.assertGreater(counts["供应商"], 0)
                self.assertGreater(counts["费率"], 0)

    def test_only_source_tables_are_populated(self):
        """产出侧必须为空:评估结论只能由平台跑一遍算出来,预置就是伪造。"""
        for table in ("wip_design_ir", "wip_part", "wip_component_match", "wip_process_plan",
                      "wip_cost_estimate", "wip_cost_item", "out_process_report",
                      "out_report_snapshot", "out_cost_result"):
            with self.subTest(table=table):
                row = self.db.query_one(f"SELECT COUNT(*) AS n FROM {table}")
                self.assertEqual(row["n"], 0, f"{table} 不应有模拟数据")

    def test_source_tables_have_expected_volume(self):
        for table, minimum in (("kb_material", 20), ("kb_material_price", 20),
                               ("kb_process_step", 40), ("kb_process_route", 6),
                               ("kb_component", 10), ("kb_supplier", 12),
                               ("kb_cost_rate", 15), ("kb_equipment", 25),
                               ("src_project", 3), ("src_requirement", 3)):
            with self.subTest(table=table):
                row = self.db.query_one(f"SELECT COUNT(*) AS n FROM {table}")
                self.assertGreaterEqual(row["n"], minimum)

    def test_reload_is_idempotent(self):
        before = self.db.query_one("SELECT COUNT(*) AS n FROM kb_material")["n"]
        self.mock.load()
        after = self.db.query_one("SELECT COUNT(*) AS n FROM kb_material")["n"]
        self.assertEqual(before, after)

    # -------------------------------------------------------------- 完整性
    def test_no_dangling_foreign_keys(self):
        # PRAGMA foreign_key_check 会扫全库,任何悬挂引用都会被列出来。
        violations = self.db.query("PRAGMA foreign_key_check")
        self.assertEqual(violations, [])

    def test_route_steps_reference_existing_process_steps(self):
        orphans = self.db.query(
            "SELECT rs.route_code, rs.step_code FROM kb_process_route_step rs "
            "LEFT JOIN kb_process_step s ON s.step_code = rs.step_code WHERE s.step_code IS NULL"
        )
        self.assertEqual(orphans, [])

    def test_component_defaults_reference_existing_material_and_route(self):
        bad_material = self.db.query(
            "SELECT c.component_code FROM kb_component c LEFT JOIN kb_material m "
            "ON m.material_code = c.default_material_code "
            "WHERE c.default_material_code IS NOT NULL AND m.material_code IS NULL"
        )
        bad_route = self.db.query(
            "SELECT c.component_code FROM kb_component c LEFT JOIN kb_process_route r "
            "ON r.route_code = c.default_route_code "
            "WHERE c.default_route_code IS NOT NULL AND r.route_code IS NULL"
        )
        self.assertEqual(bad_material, [])
        self.assertEqual(bad_route, [])

    def test_every_material_has_a_current_price(self):
        missing = [
            row["material_code"] for row in self.db.query("SELECT material_code FROM kb_material")
            if self.kb.current_price(row["material_code"], at="2026-08-01 00:00:00") is None
        ]
        self.assertEqual(missing, [])

    def test_ai_sourced_prices_carry_confidence(self):
        """市场行情价必须标注可信度,否则和合同价混在一起会误导成本测算。"""
        rows = self.db.query(
            "SELECT material_code, confidence FROM kb_material_price WHERE price_type = 'market'"
        )
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(material=row["material_code"]):
                self.assertLess(row["confidence"], 1.0)

    def test_process_steps_declare_yield_and_quality(self):
        rows = self.db.query(
            "SELECT step_code FROM kb_process_step "
            "WHERE yield_rate IS NULL OR quality_items IS NULL OR unit_min_formula IS NULL"
        )
        self.assertEqual(rows, [])

    # ---------------------------------------------------------- 半导体链路
    def test_semiconductor_packaging_route(self):
        route = self.kb.get_route("RT-SEMI-PKG-QFN")
        names = [s["step"]["name"] for s in route["steps"]]
        self.assertEqual(names[:3], ["晶圆背面减薄", "划片", "固晶"])
        self.assertEqual(names[-1], "成品测试 FT")

    def test_semiconductor_gold_wire_price_rises_over_time(self):
        early = self.kb.current_price("MAT-SEMI-WIRE-AU", at="2026-03-01 00:00:00")
        later = self.kb.current_price("MAT-SEMI-WIRE-AU", at="2026-08-01 00:00:00")
        self.assertLess(early["price"], later["price"])
        self.assertEqual(early["unit"], "g")

    def test_cleanroom_overhead_is_scoped_to_fab(self):
        fab = self.kb.effective_rate("overhead", scope_type="workshop", scope_ref="Fab 一号洁净室")
        self.assertEqual(fab["rate_code"], "RATE-SEMI-CLEANROOM")
        self.assertGreater(fab["value"], 100)

    def test_unqualified_photoresist_supplier_is_flagged(self):
        matches = self.kb.match_suppliers({"material_code": "MAT-SEMI-PR-KRF"})
        self.assertTrue(matches)
        self.assertFalse(matches[0]["qualified"])   # 国产替代验证中

    def test_semiconductor_lead_frame_is_recommended_for_qfn_part(self):
        part = {
            "name": "QFN48 框架",
            "features": [{"type": "plate", "length": 7.0, "width": 7.0, "thickness": 0.20},
                         {"type": "hole_pattern", "diameter": 0.25, "count_x": 12, "count_y": 12}],
            "params": {"length": 7.0, "width": 7.0, "thickness": 0.20, "pin_count": 48,
                       "pin_pitch": 0.5},
        }
        matches = self.kb.recommend_components(part)
        self.assertEqual(matches[0]["component_code"], "CMP-SEMI-LF-0001")
        self.assertGreater(matches[0]["score"], 0.9)

    # ------------------------------------------------------------ 电池链路
    def test_battery_prismatic_route_covers_full_flow(self):
        route = self.kb.get_route("RT-BAT-PRISMATIC")
        names = [s["step"]["name"] for s in route["steps"]]
        for expected in ("匀浆", "极片涂布", "叠片", "注液与静置", "化成", "分容与分选"):
            self.assertIn(expected, names)
        # 化成必须在注液之后,顺序错了整条产线就是错的。
        self.assertLess(names.index("注液与静置"), names.index("化成"))

    def test_lfp_price_drops_in_h1_and_lookup_respects_date(self):
        early = self.kb.current_price("MAT-BAT-LFP", at="2026-03-01 00:00:00")
        later = self.kb.current_price("MAT-BAT-LFP", at="2026-08-01 00:00:00")
        self.assertEqual(early["price"], 42.0)
        self.assertEqual(later["price"], 38.5)

    def test_coating_energy_rate_is_equipment_scoped(self):
        """涂布烘箱能耗必须单列,按全局电价粗估会严重低估。"""
        specific = self.kb.effective_rate("energy", scope_type="equipment_class",
                                          scope_ref="EQC-BAT-COAT")
        self.assertEqual(specific["rate_code"], "RATE-BAT-ENERGY-COAT")
        self.assertGreater(specific["value"], 100)

    def test_battery_stacking_and_winding_routes_coexist(self):
        routes = {r["route_code"] for r in self.kb.recommend_routes(category="锂电芯")}
        self.assertEqual(routes, {"RT-BAT-PRISMATIC", "RT-BAT-CYLINDRICAL"})

    def test_separator_supplier_is_qualified(self):
        matches = self.kb.match_suppliers({"material_code": "MAT-BAT-SEP"})
        self.assertTrue(matches[0]["qualified"])
        self.assertIn("恩捷", matches[0]["supplier"])

    # ------------------------------------------------------------ 电器链路
    def test_refrigerator_route_merges_two_upstream_branches(self):
        """发泡依赖内胆与外壳两条并行支路,依赖关系必须能表达。"""
        route = self.kb.get_route("RT-APP-REFRIGERATOR")
        foam = next(s for s in route["steps"] if s["step_code"] == "PS-APP-FOAM")
        self.assertEqual(foam["depends_on"], [10, 30])

    def test_emc_step_is_optional_with_condition(self):
        route = self.kb.get_route("RT-APP-REFRIGERATOR")
        emc = next(s for s in route["steps"] if s["step_code"] == "PS-APP-EMC")
        self.assertEqual(emc["is_optional"], 1)
        self.assertTrue(emc["condition_expr"])

    def test_appliance_bracket_recommendation(self):
        part = {
            "name": "压缩机支架",
            "features": [{"type": "plate", "length": 320.0, "width": 180.0, "thickness": 2.0},
                         {"type": "hole_pattern", "diameter": 12.0, "count_x": 2, "count_y": 2}],
            "params": {"length": 320.0, "width": 180.0, "thickness": 2.0, "hole_diameter": 12.0},
        }
        matches = self.kb.recommend_components(part)
        self.assertEqual(matches[0]["component_code"], "CMP-APP-BRKT-0001")

    def test_vacform_scrap_factor_is_high_and_documented(self):
        factor = self.kb.effective_factor("scrap", scope="塑料原料")
        self.assertEqual(factor["factor_code"], "FCT-APP-SCRAP-VACFORM")
        self.assertGreater(factor["value"], 0.1)
        self.assertIn("回收", factor["note"])

    def test_packaging_rate_is_per_unit_not_per_hour(self):
        rate = self.kb.effective_rate("packaging")
        self.assertEqual(rate["unit"], "元/台")

    # ------------------------------------------------------ 项目输入(src_)
    def test_each_industry_has_an_intake_requirement(self):
        rows = self.db.query(
            "SELECT r.requirement_no, r.status, p.name FROM src_requirement r "
            "JOIN src_project p ON p.project_id = r.project_id ORDER BY r.requirement_no"
        )
        self.assertEqual([r["requirement_no"] for r in rows],
                         ["REQ-202608-1001", "REQ-202608-1002", "REQ-202608-1003"])
        for row in rows:
            self.assertEqual(row["status"], "approved")

    def test_requirement_fields_use_the_real_form_keys(self):
        """字段键必须与 1.1 表单一致,否则页面回填不到、跨行业也没法对比。"""
        fields = self.db.query(
            "SELECT field_key, field_value FROM src_requirement_field "
            "WHERE requirement_no = ? ORDER BY field_key", ("REQ-202608-1002",)
        )
        keys = {f["field_key"] for f in fields}
        self.assertIn("battery_model", keys)
        self.assertIn("cycle_life", keys)
        self.assertIn("annual_forecast", keys)
        self.assertTrue(all(f["field_value"] for f in fields))

    def test_requirement_industry_is_a_column_not_a_field_row(self):
        rows = self.db.query(
            "SELECT requirement_no, industry FROM src_requirement ORDER BY requirement_no"
        )
        self.assertEqual([r["industry"] for r in rows],
                         ["semiconductor", "battery", "appliance"])
        # industry 描述的是表单结构而非业务内容,不该混进字段表。
        leaked = self.db.query(
            "SELECT requirement_no FROM src_requirement_field WHERE field_key = 'industry'"
        )
        self.assertEqual(leaked, [])

    def test_appliance_requirement_covers_the_whole_section_c_template(self):
        from backend.services import industry_templates

        rows = self.db.query(
            "SELECT field_key, field_value FROM src_requirement_field WHERE requirement_no = ?",
            ("REQ-202608-1003",),
        )
        values = {r["field_key"]: r["field_value"] for r in rows}
        for key in industry_templates.field_keys("appliance"):
            with self.subTest(field=key):
                self.assertIn(key, values, f"电器模板字段 {key} 未落库")
                self.assertTrue(values[key].strip())

    def test_appliance_enum_values_match_the_form_options(self):
        """下拉/标签字段存的必须是选项 value,不能是随手写的中文。"""
        from backend.services import requirement_extract as extract

        rows = self.db.query(
            "SELECT field_key, field_value FROM src_requirement_field WHERE requirement_no = ?",
            ("REQ-202608-1003",),
        )
        values = {r["field_key"]: r["field_value"] for r in rows}
        for key in ("appliance_category", "energy_efficiency_grade", "housing_material",
                    "surface_process", "certification_region"):
            with self.subTest(field=key):
                self.assertIn(values[key], extract._RECOMMENDATION_ENUMS[key])
        for key in ("core_components", "forming_process", "safety_standard"):
            with self.subTest(field=key):
                for item in values[key].split(","):
                    self.assertIn(item, extract._RECOMMENDATION_ENUMS[key])

    def test_project_industry_lookup(self):
        self.assertEqual(self.repo.project_industry("5e11c0d0c003"), "appliance")
        self.assertEqual(self.repo.project_industry("5e11c0d0a001"), "semiconductor")
        # 没有需求单的项目回落到默认模板,而不是抛错。
        self.assertEqual(self.repo.project_industry("不存在的项目"), "semiconductor")

    def test_registered_input_files_actually_exist(self):
        """登记的输入文件必须真实存在,指向空路径的记录就是脏数据。"""
        rows = self.db.query("SELECT project_id, file_path, sha256 FROM src_input_file")
        self.assertEqual(len(rows), 3)
        for row in rows:
            with self.subTest(project=row["project_id"]):
                path = self.lib.blob_root() / row["file_path"]
                self.assertTrue(path.exists(), f"{path} 不存在")
                self.assertEqual(self.lib.sha256_file(path), row["sha256"])

    def test_mock_projects_do_not_collide_with_json_project_store(self):
        """模拟项目目录不含 meta.json,不会出现在既有 JSON 项目列表里。"""
        for project_id in ("5e11c0d0a001", "5e11c0d0b002", "5e11c0d0c003"):
            self.assertFalse((self.lib.blob_root() / project_id / "meta.json").exists())

    # ------------------------------------------------------------ 选择性加载
    def test_single_industry_can_be_loaded(self):
        import tempfile as tf
        from backend.storage import da_db

        other = Path(tf.mkdtemp(prefix="da_mock_one_"))
        try:
            import backend.config as config
            original_db, original_kb, original_data = config.DA_DB_PATH, config.KB_DIR, config.DATA_DIR
            config.DA_DB_PATH = other / "da.db"
            config.KB_DIR = other / "kb"
            config.DATA_DIR = other
            da_db.close_conn()
            da_db.init_db(other / "da.db", force=True)

            result = self.mock.load(["battery"], with_projects=False)
            self.assertEqual(list(result), ["battery"])
            steps = self.db.query("SELECT step_code FROM kb_process_step")
            self.assertTrue(all(s["step_code"].startswith("PS-BAT-") for s in steps))
            self.assertEqual(self.db.query_one("SELECT COUNT(*) AS n FROM src_project")["n"], 0)
        finally:
            da_db.close_conn()
            config.DA_DB_PATH, config.KB_DIR, config.DATA_DIR = original_db, original_kb, original_data
            shutil.rmtree(other, ignore_errors=True)

    def test_unknown_industry_is_rejected(self):
        with self.assertRaises(ValueError):
            self.mock.load(["aerospace"])


if __name__ == "__main__":
    unittest.main()

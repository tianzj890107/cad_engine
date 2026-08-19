"""工艺推荐 / 成本测算之前的知识库检索。

这两个模块的价值全在"依据"二字上：推荐出来的工序编号要能在工艺库里查到，
报出来的单价要能追到某一条 price_id。所以这里守的是：

  - 召回口径正确（路线优先用同类件的默认路线；补充工序必须按材料类别收敛）；
  - 查不到的东西一律进 gaps，绝不静默取默认值；
  - 费率回退到 global 时必须显式标记 —— 这是最容易把成本算低的一处;
  - 检索失败/进度上报失败都不能连累主流程。

kb_repo 全部打桩，让这些断言与知识库里当前存了什么解耦。
"""
from __future__ import annotations

import unittest
from unittest import mock

from backend.services import cost_lookup, process_lookup


PART = {
    "part_id": "P-001",
    "name": "设备安装底板",
    "material": {"spec": "Q235"},
    "features": [
        {"type": "plate", "length": 200.0, "width": 120.0, "thickness": 10.0},
        {"type": "hole", "diameter": 9.0},
    ],
}

MILL_STEP = {
    "step_code": "PS-MILL-ROUGH", "name": "粗铣基准面", "process_type": "milling",
    "category": "机加工", "setup_min": 20.0, "unit_min_formula": "0.00002*area_mm2 + 5",
    "default_equipment_class": "EQC-CNC-VMC", "applicable_material": '["金属"]',
    "is_critical": 1,
}
BEND_STEP = {
    "step_code": "PS-BEND-SHEET", "name": "折弯成型", "process_type": "sheet_metal",
    "category": "钣金", "setup_min": 12.0, "unit_min_formula": "0.4*bend_count",
    "default_equipment_class": "EQC-BEND", "applicable_material": '["金属"]',
    "is_critical": 0,
}
LITHO_STEP = {
    "step_code": "PS-SEMI-PHOTO", "name": "光刻", "process_type": "other",
    "category": "半导体前道", "setup_min": 45.0, "unit_min_formula": "3",
    "default_equipment_class": "EQC-SEMI-LITHO", "applicable_material": '["光刻材料"]',
    "is_critical": 1,
}

ROUTE = {
    "route_code": "RT-PLATE-MACHINED", "name": "板类机加工零件典型路线",
    "applicable_material": ["金属"], "summary": "下料→粗铣→精铣",
    "steps": [{"seq": 10, "step_code": "PS-MILL-ROUGH", "is_optional": 0,
               "condition_expr": None, "step": MILL_STEP}],
}

COMPONENT = {
    "component_code": "CMP-PLT-0001", "category": "结构件",
    "default_route_code": "RT-PLATE-MACHINED", "default_material_code": "MAT-STL-Q235",
}
MATCH = {"matched": True, "component_code": "CMP-PLT-0001"}

MATERIAL = {
    "material_code": "MAT-STL-Q235", "name": "碳素结构钢板", "grade": "Q235",
    "category": "金属", "density": 7.85, "base_unit": "kg", "standard_loss_rate": 0.08,
}
PRICE = {
    "price_id": 1, "price": 4.2, "currency": "CNY", "unit": "kg",
    "price_type": "internal_purchase", "valid_from": "2026-01-01", "source_name": "采购合同",
    "confidence": 1.0,
}


def patch_kb(module, **overrides):
    """把 kb_repo 的读接口整体打桩，只留 overrides 指定的行为。"""
    defaults = {
        "list_materials": [MATERIAL],
        "get_material": MATERIAL,
        "get_component": COMPONENT,
        "get_route": ROUTE,
        "recommend_routes": [],
        "list_process_steps": [MILL_STEP, BEND_STEP, LITHO_STEP],
        "steps_for_features": [],
        "list_equipment": [],
        "current_price": PRICE,
        "effective_rate": None,
        "effective_factor": None,
    }
    defaults.update(overrides)
    patches = []
    for name, value in defaults.items():
        kwargs = {"side_effect": value} if callable(value) else {"return_value": value}
        patches.append(mock.patch.object(module.kb_repo, name, **kwargs))
    return patches


class _KbCase(unittest.TestCase):
    module = process_lookup

    def kb(self, **overrides):
        stack = patch_kb(self.module, **overrides)
        for patcher in stack:
            patcher.start()
            self.addCleanup(patcher.stop)


# --------------------------------------------------------------------------- #
# 工艺库
# --------------------------------------------------------------------------- #
class ProcessQueryTests(_KbCase):
    def test_category_and_route_come_from_the_matched_component(self):
        """同一个零件以前怎么做的，就还怎么做 —— 优先用同类件挂的默认路线。"""
        self.kb()
        query = process_lookup.build_query(PART, match=MATCH)
        self.assertEqual(query["category"], "结构件")
        self.assertEqual(query["default_route_code"], "RT-PLATE-MACHINED")
        self.assertEqual(query["material_category"], "金属")

    def test_category_falls_back_to_a_feature_guess(self):
        self.kb()
        query = process_lookup.build_query(PART, match=None)
        self.assertEqual(query["category"], "结构件")
        self.assertIsNone(query["default_route_code"])

    def test_unknown_grade_leaves_material_category_empty(self):
        self.kb(list_materials=[])
        self.assertIsNone(process_lookup.build_query(PART)["material_category"])

    def test_batch_size_is_never_below_one(self):
        self.kb()
        self.assertEqual(process_lookup.build_query(PART, batch_size=0)["batch_size"], 1)


class ProcessLookupTests(_KbCase):
    def test_default_route_wins_over_recall(self):
        self.kb(recommend_routes=[{"route_code": "RT-OTHER", "score": 0.9}])
        report = process_lookup.lookup_part(PART, match=MATCH)
        self.assertEqual(report["route"]["route_code"], "RT-PLATE-MACHINED")
        self.assertEqual(report["route"]["source"], "库内同类零部件的默认路线")
        self.assertEqual(report["route"]["steps"][0]["step_code"], "PS-MILL-ROUGH")
        self.assertEqual(report["route"]["steps"][0]["setup_min"], 20.0)

    def test_recall_is_used_when_there_is_no_matched_component(self):
        self.kb(recommend_routes=[{"route_code": "RT-PLATE-MACHINED", "score": 0.9}])
        report = process_lookup.lookup_part(PART)
        self.assertEqual(report["route"]["source"], "按类别/材料/批量召回")
        self.assertEqual(report["route"]["score"], 0.9)

    def test_no_route_is_reported_not_faked(self):
        self.kb(recommend_routes=[], get_route=None)
        report = process_lookup.lookup_part(PART)
        self.assertIsNone(report["route"])
        self.assertEqual(report["summary"]["route_steps"], 0)

    def test_supplementary_steps_are_confined_to_the_material_category(self):
        """applicable_feature 是很粗的几何标签：plate 同时挂在铣削、折弯和光刻上。
        不按材料收敛的话，一块碳钢底板会被推荐去做光刻。"""
        self.kb(steps_for_features=[MILL_STEP, BEND_STEP, LITHO_STEP])
        report = process_lookup.lookup_part(PART, match=MATCH)
        codes = {step["step_code"] for step in report["extra_steps"]}
        self.assertIn("PS-BEND-SHEET", codes)
        self.assertNotIn("PS-SEMI-PHOTO", codes)
        # 路线里已有的工序不重复列进补充候选。
        self.assertNotIn("PS-MILL-ROUGH", codes)

    def test_steps_without_a_material_list_count_as_general_purpose(self):
        """检测、钳工这类工序不标适用材料，不能因为筛材料被误杀。"""
        general = {**MILL_STEP, "step_code": "PS-INSP", "name": "终检",
                   "applicable_material": None}
        self.kb(steps_for_features=[general])
        report = process_lookup.lookup_part(PART, match=MATCH)
        self.assertEqual([s["step_code"] for s in report["extra_steps"]], ["PS-INSP"])

    def test_material_falls_back_to_the_routes_own_scope(self):
        """图纸没写牌号时，路线模板的适用材料至少把行业圈对了。"""
        self.kb(list_materials=[], steps_for_features=[BEND_STEP, LITHO_STEP])
        report = process_lookup.lookup_part(PART, match=MATCH)
        codes = {step["step_code"] for step in report["extra_steps"]}
        self.assertEqual(codes, {"PS-BEND-SHEET"})

    def test_without_any_material_scope_recall_is_skipped_with_a_note(self):
        self.kb(list_materials=[], get_route=None, recommend_routes=[],
                steps_for_features=[MILL_STEP, LITHO_STEP])
        report = process_lookup.lookup_part(PART)
        self.assertEqual(report["extra_steps"], [])
        self.assertTrue(report["notes"])
        self.assertIn("材料", report["notes"][0])

    def test_uncovered_features_become_library_gaps(self):
        def by_feature(kinds):
            return [MILL_STEP] if list(kinds) == ["plate"] else []

        self.kb(steps_for_features=by_feature)
        report = process_lookup.lookup_part(PART, match=MATCH)
        self.assertEqual(report["feature_gaps"], ["hole"])
        self.assertEqual(report["summary"]["uncovered_features"], 1)

    def test_every_step_is_reported_for_the_chat_timeline(self):
        self.kb(steps_for_features=[BEND_STEP])
        lines: list[str] = []
        process_lookup.lookup_part(PART, match=MATCH, progress=lines.append)
        joined = "\n".join(lines)
        self.assertIn("检索工艺库：P-001 设备安装底板", joined)
        self.assertIn("命中路线 RT-PLATE-MACHINED", joined)
        self.assertIn("补充工序候选", joined)

    def test_progress_failures_do_not_break_the_lookup(self):
        def boom(_message):
            raise RuntimeError("SSE 断了")

        self.kb()
        self.assertIsNotNone(process_lookup.lookup_part(PART, match=MATCH, progress=boom))


class ProcessPromptTests(_KbCase):
    def test_prompt_carries_codes_and_standard_times(self):
        self.kb(steps_for_features=[BEND_STEP])
        text = process_lookup.as_prompt(process_lookup.lookup_part(PART, match=MATCH))
        self.assertIn("RT-PLATE-MACHINED", text)
        self.assertIn("PS-MILL-ROUGH", text)
        self.assertIn("准备=20.0min", text)
        self.assertIn("EQC-CNC-VMC", text)
        self.assertIn("不得编造", text)

    def test_prompt_says_so_when_the_library_is_empty(self):
        self.kb(get_route=None, recommend_routes=[])
        text = process_lookup.as_prompt(process_lookup.lookup_part(PART))
        self.assertIn("库内没有适用的工艺路线模板", text)


# --------------------------------------------------------------------------- #
# 成本库
# --------------------------------------------------------------------------- #
def rate(code, rate_type, value, scope_type="global", scope_ref=None):
    return {"rate_code": code, "name": code, "rate_type": rate_type, "value": value,
            "unit": "元/小时", "currency": "CNY", "scope_type": scope_type,
            "scope_ref": scope_ref, "effective_from": "2026-01-01"}


def factor(code, factor_type, value, scope=None):
    return {"factor_code": code, "name": code, "factor_type": factor_type,
            "value": value, "applicable_scope": scope, "effective_from": "2026-01-01"}


PROCESS_REPORT = {
    "query": {"category": "结构件"},
    "route": {"steps": [{"equipment_class": "EQC-CNC-VMC"}, {"equipment_class": None}]},
    "extra_steps": [{"equipment_class": "EQC-BEND"}],
}


class CostLookupTests(_KbCase):
    module = cost_lookup

    def test_material_price_is_traceable(self):
        self.kb()
        report = cost_lookup.lookup_part(PART, quantity=50)
        price = report["material"]["price"]
        self.assertEqual(price["price_id"], 1)
        self.assertEqual(price["price"], 4.2)
        self.assertEqual(report["material"]["material_code"], "MAT-STL-Q235")
        self.assertTrue(report["summary"]["has_price"])

    def test_missing_material_becomes_a_gap_not_a_default(self):
        self.kb(list_materials=[])
        report = cost_lookup.lookup_part(PART)
        self.assertFalse(report["material"]["matched"])
        self.assertIsNone(report["material"]["price"])
        self.assertTrue(any("不在物料库" in gap for gap in report["gaps"]))

    def test_missing_price_becomes_a_gap(self):
        self.kb(current_price=None)
        report = cost_lookup.lookup_part(PART)
        self.assertIsNone(report["material"]["price"])
        self.assertTrue(any("无有效价格" in gap for gap in report["gaps"]))

    def test_unlabelled_drawing_falls_back_to_the_matched_components_material(self):
        self.kb(list_materials=[])
        report = cost_lookup.lookup_part({"part_id": "P-9", "name": "无牌号件"}, match=MATCH)
        self.assertTrue(report["material"]["matched"])
        self.assertIn("CMP-PLT-0001", report["material"]["source"])

    def test_scoped_rate_is_used_as_is(self):
        self.kb(effective_rate=lambda rate_type, **kw: rate(
            "RATE-LABOR-CNC", rate_type, 85.0, "equipment_class", kw.get("scope_ref")))
        report = cost_lookup.lookup_part(PART, process_report=PROCESS_REPORT)
        self.assertTrue(report["rates"])
        self.assertFalse(any(item["fallback"] for item in report["rates"]))

    def test_fallback_to_global_is_flagged(self):
        """拿一条全厂通用费率当专机费率用，会把成本压低一大截 —— 必须标出来。"""
        self.kb(effective_rate=lambda rate_type, **kw: rate(
            "RATE-LABOR-GLOBAL", rate_type, 60.0, "global", None))
        report = cost_lookup.lookup_part(PART, process_report=PROCESS_REPORT)
        fallbacks = [item for item in report["rates"] if item["fallback"]]
        self.assertTrue(fallbacks)
        self.assertEqual(fallbacks[0]["requested_scope"], "EQC-CNC-VMC")
        self.assertEqual(report["summary"]["rates_fallback"], len(fallbacks))

    def test_missing_rate_becomes_a_gap(self):
        self.kb(effective_rate=None)
        report = cost_lookup.lookup_part(PART, process_report=PROCESS_REPORT)
        self.assertEqual(report["rates"], [])
        self.assertTrue(any("费率" in gap for gap in report["gaps"]))

    def test_factors_use_the_right_scope(self):
        seen = {}

        def fake_factor(factor_type, *, at=None, scope=None):
            seen[factor_type] = scope
            return factor(f"FCT-{factor_type}", factor_type, 0.9, scope)

        self.kb(effective_factor=fake_factor)
        cost_lookup.lookup_part(PART, process_report=PROCESS_REPORT)
        # 良率跟零件类别走，废品率跟材料类别走，毛利/税率是全公司口径。
        self.assertEqual(seen["yield"], "结构件")
        self.assertEqual(seen["scrap"], "金属")
        self.assertIsNone(seen["margin"])
        self.assertIsNone(seen["tax"])

    def test_no_process_report_still_yields_global_rates(self):
        self.kb(effective_rate=lambda rate_type, **kw: rate(
            f"RATE-{rate_type}", rate_type, 10.0))
        report = cost_lookup.lookup_part(PART)
        self.assertTrue(report["rates"])
        self.assertFalse(any(item["fallback"] for item in report["rates"]))

    def test_every_step_is_reported_for_the_chat_timeline(self):
        self.kb(effective_rate=lambda rate_type, **kw: rate("R", rate_type, 1.0),
                effective_factor=lambda factor_type, **kw: factor("F", factor_type, 0.9))
        lines: list[str] = []
        cost_lookup.lookup_part(PART, quantity=50, progress=lines.append)
        joined = "\n".join(lines)
        self.assertIn("检索成本库：P-001 设备安装底板（批量 50", joined)
        self.assertIn("现价 4.2", joined)
        self.assertIn("成本库检索完成", joined)

    def test_progress_failures_do_not_break_the_lookup(self):
        def boom(_message):
            raise RuntimeError("SSE 断了")

        self.kb()
        self.assertIsNotNone(cost_lookup.lookup_part(PART, progress=boom))


class CostPromptTests(_KbCase):
    module = cost_lookup

    def test_prompt_pins_the_price_and_its_id(self):
        self.kb()
        text = cost_lookup.as_prompt(cost_lookup.lookup_part(PART, quantity=50))
        self.assertIn("MAT-STL-Q235", text)
        self.assertIn("price_id=1", text)
        self.assertIn("必须原值采用", text)

    def test_prompt_tells_the_model_to_search_only_for_gaps(self):
        self.kb(current_price=None)
        text = cost_lookup.as_prompt(cost_lookup.lookup_part(PART))
        self.assertIn("库内无有效价格", text)
        self.assertIn("联网检索", text)

    def test_fallback_rates_are_called_out_in_the_prompt(self):
        self.kb(effective_rate=lambda rate_type, **kw: rate("R-G", rate_type, 60.0))
        text = cost_lookup.as_prompt(
            cost_lookup.lookup_part(PART, process_report=PROCESS_REPORT))
        self.assertIn("已回退全厂通用值", text)


# --------------------------------------------------------------------------- #
# 接线
# --------------------------------------------------------------------------- #
class WiringTests(unittest.TestCase):
    """检索必须真的接进推荐链路，且失败时降级而不是把推荐一起拖垮。"""

    from pathlib import Path as _Path
    ROOT = _Path(__file__).resolve().parents[1]
    MAIN_PY = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    PROCESS_PY = (ROOT / "backend" / "services" / "process.py").read_text(encoding="utf-8")
    COST_PY = (ROOT / "backend" / "services" / "cost.py").read_text(encoding="utf-8")
    INLINE_JS = (ROOT / "frontend" / "inline-analysis.js").read_text(encoding="utf-8")
    AGENT_JS = (ROOT / "frontend" / "agent-chat.js").read_text(encoding="utf-8")

    def test_lookup_runs_before_the_model_call(self):
        for marker in ("_process_lookup_for(project_id, part_id",
                       "_cost_lookup_for(project_id, part_id"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.MAIN_PY)
        for call, lookup in (("process.decompose_process", "_process_lookup_for"),
                             ("cost.analyze_cost", "_cost_lookup_for")):
            job = self.MAIN_PY.split(call, 1)[0]
            with self.subTest(call=call):
                # 检索必须排在模型调用之前，否则拿不到依据。
                self.assertIn(f"lookup = {lookup}(", job.rsplit("def job():", 1)[-1])

    def test_lookup_failure_degrades_instead_of_blocking(self):
        """库是辅助依据，不是前置条件 —— 查不到也得让推荐跑完。"""
        for helper in ("def _process_lookup_for", "def _cost_lookup_for"):
            block = self.MAIN_PY.split(helper, 1)[1].split("\n\n\n", 1)[0]
            with self.subTest(helper=helper):
                self.assertIn("except Exception", block)
                self.assertIn("return None", block)

    def test_prompts_receive_the_library_summary(self):
        self.assertIn("library=process_lookup.as_prompt(lookup) if lookup else \"\"", self.MAIN_PY)
        self.assertIn("library=cost_lookup.as_prompt(lookup) if lookup else \"\"", self.MAIN_PY)
        self.assertIn("library: str = \"\"", self.PROCESS_PY)
        self.assertIn("library: str = \"\"", self.COST_PY)

    def test_no_library_keeps_the_old_no_enterprise_data_warning(self):
        """库为空时仍必须禁止编造企业资源编号 —— 这条老约束不能被新分支绕过。"""
        self.assertIn("当前没有企业设备/刀具/标准工时库", self.PROCESS_PY)

    def test_reports_are_readable_over_http(self):
        for path in ("process-lookup", "cost-lookup"):
            with self.subTest(path=path):
                self.assertIn(f"/parts/{{part_id}}/{path}", self.MAIN_PY)
        # 知识库更新后可以只重查依据，不必重跑要花钱的推荐。
        self.assertIn("/parts/{part_id}/library-lookup", self.MAIN_PY)

    def test_agent_can_query_both_libraries_itself(self):
        from backend.services import oc_agent

        self.assertIn("LookupProcessLibrary", oc_agent.PLATFORM_TOOL_NAMES)
        self.assertIn("LookupCostLibrary", oc_agent.PLATFORM_TOOL_NAMES)
        self.assertIn("LookupProcessLibrary", self.AGENT_JS)
        self.assertIn("LookupCostLibrary", self.AGENT_JS)
        # 零部件库同样要能被 Agent 直接查，否则"这个零件有没有现成件"只能靠
        # 解析时跑过的那一份缓存，库里新录了件也看不见。
        self.assertIn("LookupComponentLibrary", oc_agent.PLATFORM_TOOL_NAMES)
        self.assertIn("LookupComponentLibrary", self.AGENT_JS)

    def test_lookup_progress_reaches_the_chat(self):
        self.assertIn("agent:task-progress", self.INLINE_JS)
        block = self.INLINE_JS.split("agent:task-progress", 1)[1][:400]
        for field in ("label", "status", "progress", "log", "error"):
            with self.subTest(field=field):
                self.assertIn(field, block)

    def test_panels_show_what_the_numbers_are_based_on(self):
        for fn in ("processLibraryCard", "costLibraryCard"):
            with self.subTest(fn=fn):
                self.assertIn(f"function {fn}", self.INLINE_JS)
        self.assertIn("库内依据 · 工艺库", self.INLINE_JS)
        self.assertIn("库内依据 · 成本库", self.INLINE_JS)
        self.assertIn("price_id=", self.INLINE_JS)
        # 回退的费率在界面上也要看得出来。
        self.assertIn("已回退全厂通用值", self.INLINE_JS)

    def test_report_is_kept_separate_from_the_editable_plan(self):
        """人工改了工艺路线，依据不该跟着变。"""
        from backend.storage import store

        for name in ("save_process_lookup", "load_process_lookup",
                     "save_cost_lookup", "load_cost_lookup"):
            with self.subTest(name=name):
                self.assertTrue(hasattr(store, name))
        self.assertIn("lookupPath(state.mode)", self.INLINE_JS)


if __name__ == "__main__":
    unittest.main()

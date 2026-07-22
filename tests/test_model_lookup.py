"""型号候选采集的离线回归；不触发联网调用。"""
from __future__ import annotations

import unittest

from backend.models.ir import DesignIR
from backend.models.model_lookup import ModelIdentification, ModelLookupResult
from backend.main import WorkbenchPartEdit, _apply_workbench_chat_edit
from backend.services.model_lookup import _candidate_context, apply_lookup_results
from backend.services.versioning import diff_ir


class ModelLookupTests(unittest.TestCase):
    def test_collects_explicit_part_model_and_document_model(self):
        ir = DesignIR.model_validate({
            "device_name": "真空总成",
            "design_intent": "测试",
            "parts": [{
                "part_id": "P-001", "name": "电磁阀", "model_no": "VQ110-5M",
                "features": [], "quantity": 1,
            }],
        })
        candidates, text, _ = _candidate_context(ir, [("requirements.txt", "真空开关型号 ZSE30A-01-N-L".encode())])
        found = {item["candidate_model"] for item in candidates}
        self.assertIn("VQ110-5M", found)
        self.assertIn("ZSE30A-01-N-L", found)
        self.assertIn("ZSE30A-01-N-L", text)

    def test_skips_generic_part_ids_and_thread_sizes(self):
        ir = DesignIR.model_validate({
            "device_name": "测试", "design_intent": "测试",
            "parts": [{"part_id": "P-001", "name": "底板 M8x25", "features": []}],
        })
        candidates, _, _ = _candidate_context(ir, [])
        self.assertEqual(candidates, [])

    def test_collects_product_style_identifier_lines_from_technical_document(self):
        ir = DesignIR.model_validate({
            "device_name": "电视", "design_intent": "测试", "parts": [],
        })
        candidates, _, _ = _candidate_context(
            ir, [("requirements.txt", "海信RGB-MiniLED电视UX 2026\n信芯AI画质芯片 H7 Pro".encode())],
        )
        found = {item["candidate_model"] for item in candidates}
        self.assertIn("H7 Pro", found)
        self.assertIn("海信RGB-MiniLED电视UX 2026", found)

    def test_normalizes_natural_language_confidence_levels(self):
        self.assertEqual(ModelIdentification(candidate_model="H7 Pro", confidence="high").confidence, 0.80)
        self.assertEqual(ModelIdentification(candidate_model="H7 Pro", confidence="very_high").confidence, 0.92)
        self.assertEqual(ModelIdentification(candidate_model="H7 Pro", confidence="60%").confidence, 0.60)

    def test_reliable_lookup_is_applied_to_related_part_or_bom(self):
        ir = DesignIR.model_validate({
            "device_name": "测试", "design_intent": "测试",
            "parts": [{"part_id": "P-001", "name": "未知外购件", "features": []}],
        })
        report = {"identifications": [
            {"candidate_model": "VQ110-5M", "related_part_id": "P-001", "status": "matched",
             "identified_part_name": "5通电磁阀", "manufacturer": "SMC", "category": "电磁阀",
             "specification_summary": "5口阀", "evidence_summary": "官网目录"},
            {"candidate_model": "ZSE30A-01-N-L", "status": "matched", "identified_part_name": "数字式压力开关",
             "manufacturer": "SMC", "category": "压力开关"},
            {"candidate_model": "猜测项", "status": "ambiguous", "identified_part_name": "不应写入"},
        ]}
        updated, changes = apply_lookup_results(ir, report)
        self.assertEqual(updated.parts[0].name, "5通电磁阀")
        self.assertEqual(updated.parts[0].model_no, "VQ110-5M")
        self.assertEqual(updated.parts[0].manufacturer, "SMC")
        self.assertEqual(len(updated.standard_parts), 1)
        self.assertEqual(updated.standard_parts[0].model_no, "ZSE30A-01-N-L")
        self.assertEqual(len(changes), 2)
        diff = diff_ir(ir.model_dump(), updated.model_dump())
        self.assertIn("model_no", [change["field"] for change in diff["parts"]["modified"][0]["changes"]])
        self.assertEqual(len(diff["standard_parts"]["added"]), 1)

    def test_product_level_research_proposal_is_kept_as_pending_bom_component(self):
        ir = DesignIR.model_validate({"device_name": "电视", "design_intent": "测试", "parts": []})
        updated, changes = apply_lookup_results(ir, {"proposed_components": [{
            "name": "玲珑4芯 RGB MiniLED 背光模组", "category": "背光模组",
            "role": "提供分区 RGB 背光", "confidence": "medium", "evidence_summary": "产品公开资料",
        }]})
        self.assertEqual(updated.standard_parts[0].spec, "【联网推演·待图纸确认】玲珑4芯 RGB MiniLED 背光模组")
        self.assertEqual(updated.standard_parts[0].model_specification, "提供分区 RGB 背光")
        self.assertEqual(changes[0]["target"], "web_component")

    def test_natural_language_product_research_lists_are_normalized(self):
        result = ModelLookupResult.model_validate({
            "proposed_components": ["信芯 H7 Pro：负责画质与背光协同控制"],
            "process_designs": ["四色 Mini LED 封装：波长与热管理控制"],
        })
        self.assertEqual(result.proposed_components[0].name, "信芯 H7 Pro")
        self.assertIn("背光协同", result.proposed_components[0].role)
        self.assertEqual(result.process_designs[0].name, "四色 Mini LED 封装")

    def test_chat_edit_changes_only_whitelisted_selected_part_parameters(self):
        ir = DesignIR.model_validate({
            "device_name": "测试", "design_intent": "测试",
            "parts": [{"part_id": "P-001", "name": "底板", "features": [{
                "type": "plate", "length": 100, "width": 80, "thickness": 10,
            }]}],
        })
        changes, needs_regen = _apply_workbench_chat_edit(ir.parts[0], WorkbenchPartEdit(
            should_apply=True, material_spec="6061-T6",
            feature_updates=[{"feature_index": 0, "field": "thickness", "value": 12}],
        ))
        self.assertTrue(needs_regen)
        self.assertEqual(ir.parts[0].features[0].thickness, 12)
        self.assertEqual(ir.parts[0].material.spec, "6061-T6")
        self.assertEqual(len(changes), 2)


if __name__ == "__main__":
    unittest.main()

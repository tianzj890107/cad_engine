"""AI SOP、证据门、补丁校核和通用工艺规则的离线回归测试。"""
from __future__ import annotations

import unittest

from backend.models.ai import EvidenceLevel, FieldEvidence, VerificationChange, VerificationPatch
from backend.models.ir import DesignIR, OpenQuestion
from backend.models.process import ProcessPlan
from backend.services import ai_governance, approval, model_lookup, process, sop, vision


def sample_ir(*, confidence: float = .8, evidence=None) -> DesignIR:
    return DesignIR.model_validate({
        "device_name": "通用钣金箱体", "design_intent": "设备防护",
        "parts": [{
            "part_id": "P-001", "name": "盖板", "quantity": 1,
            "confidence": confidence,
            "material": {"spec": "SUS304"},
            "features": [{"type": "plate", "length": 100, "width": 80, "thickness": 2}],
        }],
        "evidence_ledger": evidence or [],
    })


class AiSopTests(unittest.TestCase):
    def test_industry_router_does_not_default_to_electronic_ceramics(self):
        self.assertEqual(sop.industry_profile(["冰箱钣金盖板", "SUS304"]), "general")
        self.assertEqual(sop.industry_profile(["粉末冶金烧结齿轮", "表面金属化处理"]), "general")
        self.assertEqual(sop.industry_profile(["AlN 陶瓷基板金属化"]), "electronic_ceramics")

    def test_drawing_finalize_keeps_weak_evidence_without_blocking_generation(self):
        ir = vision.finalize_ir(sample_ir(confidence=.55), "drawing.png")
        self.assertEqual(ir.ai_status.value, "PARTIAL")
        self.assertTrue(ir.evidence_ledger)
        self.assertFalse(vision.generation_gate(ir))
        self.assertTrue(any("证据置信度较低" in item for item in ir.validation.warnings))

    def test_human_save_promotes_current_critical_values_to_strong_evidence(self):
        ir = vision.finalize_ir(sample_ir(confidence=.55), "drawing.png")
        confirmed = vision.mark_human_confirmed(ir, "admin")
        self.assertFalse(vision.generation_gate(confirmed))
        self.assertTrue(all(
            item.level == EvidenceLevel.strong for item in confirmed.evidence_ledger
            if item.field in vision.critical_field_paths(confirmed)
        ))

    def test_missing_material_is_advisory_for_cad_generation(self):
        ir = sample_ir(confidence=.8)
        ir.parts[0].material = None
        ir = vision.finalize_ir(ir, "drawing.png")
        confirmed = vision.mark_human_confirmed(ir, "admin")
        self.assertEqual(confirmed.ai_status.value, "PARTIAL")
        self.assertFalse(vision.generation_gate(confirmed))
        self.assertTrue(any("未识别材料" in item for item in confirmed.validation.warnings))

    def test_human_save_recomputes_stale_missing_material_error(self):
        ir = sample_ir(confidence=.8)
        ir.parts[0].material = None
        ir.open_questions = [OpenQuestion(field="P-001.material", reason="材料缺失")]
        blocked = vision.finalize_ir(ir, "drawing.png")
        payload = blocked.model_dump()
        payload["parts"][0]["material"] = {"spec": "SUS304"}
        corrected = DesignIR.model_validate(payload)
        confirmed = vision.mark_human_confirmed(corrected, "admin")
        self.assertFalse(any("material.spec 缺失" in item for item in confirmed.validation.errors))
        self.assertFalse(confirmed.open_questions)
        self.assertFalse(vision.generation_gate(confirmed))

    def test_human_save_resolves_previous_contradictory_local_evidence(self):
        ir = sample_ir(evidence=[{
            "field": "parts[P-001].features[0].width", "level": "CONTRADICTORY",
            "confidence": .9, "requires_confirmation": True,
        }])
        blocked = vision.finalize_ir(ir, "drawing.png")
        self.assertFalse(any("冲突证据" in item for item in blocked.validation.errors))
        self.assertTrue(any("证据冲突" in item for item in blocked.validation.warnings))
        self.assertFalse(vision.generation_gate(blocked))
        confirmed = vision.mark_human_confirmed(blocked, "admin")
        self.assertFalse(any("冲突证据" in item for item in confirmed.validation.errors))
        self.assertFalse(vision.generation_gate(confirmed))

    def test_missing_base_dimension_still_blocks_generation(self):
        ir = sample_ir()
        ir.parts[0].features[0].width = None
        finalized = vision.finalize_ir(ir, "drawing.png")
        self.assertEqual(finalized.ai_status.value, "BLOCKED")
        self.assertTrue(any("features[0].width 缺失" in item for item in vision.generation_gate(finalized)))

    def test_pipeline_report_exposes_each_real_stage(self):
        ir = vision.finalize_ir(sample_ir(confidence=.8), "drawing.png")
        report = vision.pipeline_report(ir, vision.build_input_manifest(
            "drawing.png", b"image", [("spec.txt", b"requirements")]
        ))
        self.assertEqual(
            [item["id"] for item in report["stages"]],
            ["manifest", "local_extraction", "view_and_candidate_detection",
             "dimension_binding", "ir_assembly", "rule_validation"],
        )

    def test_verification_applies_only_strong_whitelisted_scalar_patch(self):
        ir = sample_ir()
        field = "parts[P-001].features[0].width"
        patch = VerificationPatch(changes=[VerificationChange(
            field=field, old_value="80", new_value="82", reason="俯视图明确标注",
            confidence=.96, requires_confirmation=False,
            evidence=FieldEvidence(
                field=field, source_file="drawing.png", view="俯视图",
                level=EvidenceLevel.strong, confidence=.96,
            ),
        )])
        updated, applied, pending = vision.apply_verification_patch(ir, patch)
        self.assertEqual(updated.parts[0].features[0].width, 82)
        self.assertEqual(len(applied), 1)
        self.assertEqual(pending, [])

    def test_weak_verification_patch_stays_pending(self):
        ir = sample_ir()
        field = "parts[P-001].features[0].width"
        patch = VerificationPatch(changes=[VerificationChange(
            field=field, old_value="80", new_value="82", reason="比例估算",
            confidence=.6, requires_confirmation=True,
            evidence=FieldEvidence(
                field=field, source_file="drawing.png", view="未细分",
                evidence_type="estimated", level=EvidenceLevel.weak, confidence=.6,
                requires_confirmation=True,
            ),
        )])
        updated, applied, pending = vision.apply_verification_patch(ir, patch)
        self.assertEqual(updated.parts[0].features[0].width, 80)
        self.assertEqual(applied, [])
        self.assertEqual(len(pending), 1)

    def test_invalid_verification_patch_does_not_mutate_ir(self):
        ir = sample_ir()
        field = "parts[P-001].features[0].width"
        patch = VerificationPatch(changes=[VerificationChange(
            field=field, old_value="80", new_value='"not-a-number"', reason="无效值",
            confidence=.99, requires_confirmation=False,
            evidence=FieldEvidence(field=field, level=EvidenceLevel.strong, confidence=.99),
        )])
        updated, applied, pending = vision.apply_verification_patch(ir, patch)
        self.assertEqual(updated.parts[0].features[0].width, 80)
        self.assertEqual(applied, [])
        self.assertIn("新值未通过 IR 校验", pending[0]["rejected_reason"])

    def test_verification_patch_accepts_numeric_model_values(self):
        patch = VerificationPatch.model_validate({"changes": [{
            "field": "parts[P-001].quantity", "old_value": 1, "new_value": 2,
            "evidence": {"field": "parts[P-001].quantity", "level": "STRONG"},
        }]})
        self.assertEqual(patch.changes[0].old_value, "1")
        self.assertEqual(patch.changes[0].new_value, "2")

    def test_process_classification_and_rules_are_deterministic(self):
        part = sample_ir().parts[0]
        self.assertEqual(process.classify_part(part), "sheet_metal")
        warnings = process.validate_rules({
            "part_class": "sheet_metal",
            "steps": [{"step_no": 10, "type": "milling"}],
        }, part)
        self.assertTrue(any("不常见工序" in item for item in warnings))
        self.assertTrue(any("最终检验" in item for item in warnings))

    def test_process_classification_does_not_treat_every_valve_body_as_purchased(self):
        part = sample_ir().parts[0]
        part.name = "阀体"
        self.assertNotEqual(process.classify_part(part), "standard_part")
        part.name = "外购电磁阀"
        part.model_no = "VQ110-5M"
        self.assertEqual(process.classify_part(part), "standard_part")

    def test_empty_ai_process_plan_gets_deterministic_route(self):
        part = sample_ir().parts[0]
        part.material = None
        plan = process.ensure_minimum_route(ProcessPlan(
            part_id=part.part_id, part_name=part.name, summary="模型只返回了概览",
            part_class="sheet_metal", steps=[],
        ), part)
        types = [step.type.value for step in plan.steps]
        self.assertGreaterEqual(len(plan.steps), 4)
        self.assertIn("blank", types)
        self.assertIn("inspection", types)
        self.assertTrue(plan.overall_note)
        self.assertFalse(any("缺少最终检验" in item for item in process.validate_rules(plan.model_dump(), part)))

    def test_rule_validation_accepts_process_type_enum(self):
        part = sample_ir().parts[0]
        plan = ProcessPlan.model_validate({
            "part_class": "sheet_metal",
            "steps": [{
                "step_no": 10, "name": "最终检验", "type": "inspection",
                "description": "检验", "confidence": .8,
            }],
        })
        self.assertFalse(any(
            "缺少最终检验" in item for item in process.validate_rules({"part_class": "sheet_metal", "steps": plan.steps}, part)
        ))

    def test_model_lookup_candidate_keeps_exact_part_binding(self):
        ir = sample_ir()
        ir.parts[0].model_no = "VQ110-5M"
        candidates, _, _ = model_lookup._candidate_context(ir, [])
        self.assertEqual(candidates[0]["related_part_id"], "P-001")

    def test_approval_level_uses_local_conservative_matrix(self):
        level, _ = approval.determine_level(
            {"suggested_price": 100, "final_price": 80, "costs": {"base_cost": 79}}, {}, ""
        )
        self.assertEqual(level, 3)
        level, _ = approval.determine_level(
            {"suggested_price": 100, "final_price": 100, "costs": {"base_cost": 70}}, {}, ""
        )
        self.assertEqual(level, 1)

    def test_ai_task_metadata_keeps_original_result_shape_out_of_band(self):
        metadata = ai_governance.metadata("process", {
            "plan": {"open_questions": [{"field": "material", "reason": "缺失"}]},
            "validation": {"warnings": ["缺少最终检验"]},
        })
        self.assertEqual(metadata["status"], "PARTIAL")
        self.assertEqual(metadata["sop_version"], "process-1.0")


if __name__ == "__main__":
    unittest.main()

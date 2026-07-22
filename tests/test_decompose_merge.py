"""拆解推荐本地合并测试：不调用任何模型。"""
from __future__ import annotations

import unittest

from backend.models.ir import DesignIR
from backend.services.decompose import DecompositionAdvice, PartRecommendation, _merge_advice


class DecomposeMergeTests(unittest.TestCase):
    def test_only_allowed_recommendation_fields_are_merged(self):
        original = DesignIR.model_validate({
            "device_name": "测试装置", "design_intent": "测试",
            "parts": [{
                "part_id": "P-001", "name": "底板",
                "features": [{"type": "plate", "length": 100, "width": 80, "thickness": 10}],
            }],
            "standard_parts": [{"spec": "M8x25", "category": "bolt", "quantity": 4}],
        })
        advice = DecompositionAdvice(
            recommendations=[PartRecommendation(part_id="P-001", recommendation="CNC 铣削，孔边距待复核")],
            assembly_notes="先装底板，再锁紧螺栓。",
            additional_standard_parts=[
                {"spec": "M8x25", "category": "bolt", "quantity": 4},
                {"spec": "GB/T 93 M8", "category": "spring washer", "quantity": 4},
            ],
        )

        merged = _merge_advice(original, advice)

        self.assertEqual(merged.parts[0].recommendation, "CNC 铣削，孔边距待复核")
        self.assertEqual(merged.parts[0].features[0].length, 100)
        self.assertEqual(merged.assembly_notes, "先装底板，再锁紧螺栓。")
        self.assertEqual(len(merged.standard_parts), 2)


if __name__ == "__main__":
    unittest.main()

"""IR 模型的离线兼容性回归：不调用任何模型或网络。"""
from __future__ import annotations

import unittest

from backend.models.ir import DesignIR


class IRNormalizationTests(unittest.TestCase):
    def test_common_model_variants_are_normalized_without_losing_the_ir(self):
        ir = DesignIR.model_validate({
            "device_name": "测试装置",
            "design_intent": "验证常见字段变体",
            "overall_dims": {"length": 320, "width": 180, "height": 95, "unit": "mm"},
            "standard_parts": [
                {"name": "GB/T 5783 M8x25", "category": "bolt", "quantity": 4},
                {"category": "washer", "quantity": 4},
            ],
            "open_questions": ["板厚在图中未标注"],
            "parts": [{
                "part_id": "P-001", "name": "底板", "features": [],
                "provenance": "明细表第 1 项及爆炸图标注",
            }, {
                "part_id": "P-002", "name": "接头", "features": [
                    {"type": "cylinder", "diameter": 6, "length": 20},
                    {"type": "box", "length": 60, "width": 60, "thickness": 20},
                ],
            }],
        })

        self.assertEqual(ir.overall_dims, "320 × 180 × 95 mm")
        self.assertEqual(ir.standard_parts[0].spec, "GB/T 5783 M8x25")
        self.assertEqual(ir.standard_parts[1].spec, "washer")
        self.assertEqual(ir.open_questions[0].field, "待确认项")
        self.assertEqual(ir.open_questions[0].reason, "板厚在图中未标注")
        self.assertEqual(ir.parts[0].provenance.note, "明细表第 1 项及爆炸图标注")
        self.assertEqual(ir.parts[1].features[0].height, 20)
        self.assertEqual(ir.parts[1].features[1].height, 20)


if __name__ == "__main__":
    unittest.main()

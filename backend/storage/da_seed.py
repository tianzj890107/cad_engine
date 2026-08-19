"""知识库种子数据。

目的不是"填满数据库",而是让四条链路在没有历史数据时也能跑通并被验证:
  物料 -> 价格 -> 费率 -> 成本测算;特征 -> 工序 -> 路线 -> 工艺推荐;
  参数/特征 -> 零部件推荐;图库文件夹 -> 图纸索引。
真实数据由工艺/采购部门通过导入或界面维护,种子只提供最小可用集合。
"""
from __future__ import annotations

from . import da_db as db
from . import kb_library as lib
from . import kb_repo as kb

SEED_DATE = "2026-01-01 00:00:00"


EQUIPMENT_CLASSES = [
    ("EQC-CNC-VMC", "立式加工中心", "机加工"),
    ("EQC-LATHE", "数控车床", "机加工"),
    ("EQC-DRILL", "钻床", "机加工"),
    ("EQC-GRIND", "平面磨床", "机加工"),
    ("EQC-LASER", "激光切割机", "下料"),
    ("EQC-BEND", "数控折弯机", "钣金"),
    ("EQC-WELD", "焊机", "焊接"),
    ("EQC-HEAT", "热处理炉", "热处理"),
    ("EQC-SURF", "表面处理线", "表面处理"),
    ("EQC-CMM", "三坐标测量机", "检测"),
]

EQUIPMENT = [
    {"equipment_code": "EQ-VMC850", "name": "立式加工中心 VMC850", "model_no": "VMC850",
     "equipment_class": "EQC-CNC-VMC", "hourly_rate": 85.0, "depreciation_per_hour": 42.0,
     "power_kw": 15.0, "workshop": "机加车间",
     "capability": {"travel_x": 800, "travel_y": 500, "travel_z": 500,
                    "max_weight_kg": 600, "spindle_rpm": 8000, "accuracy_mm": 0.01}},
    {"equipment_code": "EQ-CK6140", "name": "数控车床 CK6140", "model_no": "CK6140",
     "equipment_class": "EQC-LATHE", "hourly_rate": 65.0, "depreciation_per_hour": 28.0,
     "power_kw": 7.5, "workshop": "机加车间",
     "capability": {"max_diameter": 400, "max_length": 1000, "spindle_rpm": 2000}},
    {"equipment_code": "EQ-LASER3015", "name": "光纤激光切割机 3015", "model_no": "HS-G3015A",
     "equipment_class": "EQC-LASER", "hourly_rate": 120.0, "depreciation_per_hour": 60.0,
     "power_kw": 30.0, "workshop": "钣金车间",
     "capability": {"sheet_x": 3000, "sheet_y": 1500, "max_thickness_carbon": 20}},
    {"equipment_code": "EQ-BEND110", "name": "数控折弯机 110T", "model_no": "WE67K-110",
     "equipment_class": "EQC-BEND", "hourly_rate": 70.0, "depreciation_per_hour": 25.0,
     "power_kw": 11.0, "workshop": "钣金车间",
     "capability": {"tonnage": 110, "max_length": 3200}},
    {"equipment_code": "EQ-CMM564", "name": "三坐标测量机", "model_no": "Global S 5-6-4",
     "equipment_class": "EQC-CMM", "hourly_rate": 95.0, "depreciation_per_hour": 45.0,
     "power_kw": 2.0, "workshop": "计量室",
     "capability": {"range_x": 500, "range_y": 600, "range_z": 400, "accuracy_um": 1.7}},
]

# 工序原子:applicable_feature 与 models/ir.py::FeatureType 对齐,
# 因而"拆解出什么特征"可以直接召回"该做哪些工序"。
PROCESS_STEPS = [
    {"step_code": "PS-BLANK-LASER", "name": "激光下料", "process_type": "blank", "category": "下料",
     "description_tpl": "按展开图激光切割外形与工艺孔", "default_equipment_class": "EQC-LASER",
     "applicable_material": ["金属"], "applicable_feature": ["plate"],
     "setup_min": 15, "unit_min_formula": "0.0008*perimeter_mm + 1.5", "yield_rate": 0.98,
     "quality_items": ["外形尺寸 ±0.2", "切口无挂渣"]},
    {"step_code": "PS-BLANK-SAW", "name": "锯切下料", "process_type": "blank", "category": "下料",
     "description_tpl": "按下料规格锯切棒料/板料", "default_equipment_class": "EQC-LATHE",
     "applicable_material": ["金属"], "applicable_feature": ["box", "cylinder"],
     "setup_min": 10, "unit_min_formula": "0.02*area_mm2/100 + 2", "yield_rate": 0.97,
     "quality_items": ["长度 +1/-0"]},
    {"step_code": "PS-MILL-ROUGH", "name": "粗铣基准面", "process_type": "milling", "category": "机加工",
     "description_tpl": "粗铣六面,留精加工余量 0.5mm", "default_equipment_class": "EQC-CNC-VMC",
     "default_tooling": "φ16 立铣刀", "applicable_material": ["金属"],
     "applicable_feature": ["plate", "box"],
     "setup_min": 20, "unit_min_formula": "0.00002*area_mm2 + 5", "yield_rate": 0.99,
     "quality_items": ["平面度 0.1"]},
    {"step_code": "PS-MILL-FINISH", "name": "精铣成型", "process_type": "milling", "category": "机加工",
     "description_tpl": "精铣至图纸尺寸,保证平面度与粗糙度", "default_equipment_class": "EQC-CNC-VMC",
     "default_tooling": "φ12 立铣刀", "applicable_material": ["金属"],
     "applicable_feature": ["plate", "box", "fillet", "chamfer"],
     "setup_min": 15, "unit_min_formula": "0.00003*area_mm2 + 8", "yield_rate": 0.99,
     "is_critical": 1, "quality_items": ["平面度 0.05", "Ra1.6"]},
    {"step_code": "PS-TURN-ROUGH", "name": "车外圆", "process_type": "turning", "category": "机加工",
     "description_tpl": "车削外圆与端面至图纸尺寸", "default_equipment_class": "EQC-LATHE",
     "applicable_material": ["金属"], "applicable_feature": ["cylinder"],
     "setup_min": 15, "unit_min_formula": "0.0004*diameter_mm*height_mm/10 + 4", "yield_rate": 0.98,
     "quality_items": ["外径公差 h7", "Ra3.2"]},
    {"step_code": "PS-DRILL-HOLE", "name": "钻孔", "process_type": "drilling", "category": "机加工",
     "description_tpl": "按孔位图钻孔,必要时扩铰", "default_equipment_class": "EQC-CNC-VMC",
     "default_tooling": "麻花钻 + 铰刀", "applicable_material": ["金属"],
     "applicable_feature": ["hole", "hole_pattern"],
     "setup_min": 10, "unit_min_formula": "0.35*hole_count + 2", "yield_rate": 0.99,
     "quality_items": ["孔径 H8", "孔位 ±0.1"]},
    {"step_code": "PS-BEND-SHEET", "name": "折弯成型", "process_type": "sheet_metal", "category": "钣金",
     "description_tpl": "按折弯图逐道折弯,注意折弯顺序与回弹补偿",
     "default_equipment_class": "EQC-BEND", "applicable_material": ["金属"],
     "applicable_feature": ["plate"],
     "setup_min": 20, "unit_min_formula": "0.5*bend_count + 2", "yield_rate": 0.96,
     "quality_items": ["折弯角度 ±0.5°", "折弯尺寸 ±0.3"]},
    {"step_code": "PS-WELD-MIG", "name": "焊接", "process_type": "welding", "category": "焊接",
     "description_tpl": "按焊接符号施焊,控制焊接变形",
     "default_equipment_class": "EQC-WELD", "applicable_material": ["金属"],
     "applicable_feature": ["plate", "box"],
     "setup_min": 25, "unit_min_formula": "0.02*weld_length_mm + 5", "yield_rate": 0.95,
     "is_critical": 1, "quality_items": ["焊缝无咬边/气孔", "变形量 ≤1mm"]},
    {"step_code": "PS-HEAT-QT", "name": "调质处理", "process_type": "heat_treat", "category": "热处理",
     "description_tpl": "淬火 + 高温回火,达到图纸硬度要求",
     "default_equipment_class": "EQC-HEAT", "applicable_material": ["金属"],
     "applicable_feature": ["box", "cylinder"],
     "setup_min": 60, "unit_min_formula": "12", "yield_rate": 0.97,
     "quality_items": ["硬度 HB220-250"]},
    {"step_code": "PS-SURF-PLATE", "name": "表面处理", "process_type": "surface", "category": "表面处理",
     "description_tpl": "按图纸要求做发黑/镀锌/阳极氧化",
     "default_equipment_class": "EQC-SURF", "applicable_material": ["金属"],
     "applicable_feature": ["plate", "box", "cylinder"],
     "setup_min": 30, "unit_min_formula": "0.000005*area_mm2 + 3", "yield_rate": 0.98,
     "quality_items": ["镀层厚度 8-12µm", "外观均匀无漏镀"]},
    {"step_code": "PS-BENCH-DEBURR", "name": "钳工去毛刺", "process_type": "bench", "category": "钳工",
     "description_tpl": "全周去毛刺、倒钝锐边,攻丝",
     "applicable_material": ["金属"], "applicable_feature": ["hole", "hole_pattern", "chamfer"],
     "setup_min": 5, "unit_min_formula": "0.2*hole_count + 3", "yield_rate": 0.995,
     "quality_items": ["无毛刺,锐边倒钝 C0.3"]},
    {"step_code": "PS-INSP-FINAL", "name": "终检", "process_type": "inspection", "category": "检测",
     "description_tpl": "按图纸全尺寸检验并出具报告", "default_equipment_class": "EQC-CMM",
     "applicable_material": ["金属", "陶瓷"],
     "applicable_feature": ["plate", "box", "cylinder", "hole", "hole_pattern"],
     "setup_min": 10, "unit_min_formula": "0.5*key_dim_count + 5", "yield_rate": 1.0,
     "is_critical": 1, "quality_items": ["全尺寸符合图纸", "出具检验报告"]},
]

ROUTES = [
    {
        "route": {"route_code": "RT-PLATE-MACHINED", "name": "板类机加工零件典型路线",
                  "applicable_category": "结构件", "applicable_material": ["金属"],
                  "batch_min": 1, "batch_max": 500,
                  "summary": "下料 → 粗铣 → 精铣 → 钻孔 → 去毛刺 → 表面处理 → 终检"},
        "steps": [
            {"seq": 10, "step_code": "PS-BLANK-LASER"},
            {"seq": 20, "step_code": "PS-MILL-ROUGH", "depends_on": [10]},
            {"seq": 30, "step_code": "PS-MILL-FINISH", "depends_on": [20]},
            {"seq": 40, "step_code": "PS-DRILL-HOLE", "depends_on": [30]},
            {"seq": 50, "step_code": "PS-BENCH-DEBURR", "depends_on": [40]},
            {"seq": 60, "step_code": "PS-SURF-PLATE", "is_optional": 1, "depends_on": [50],
             "condition_expr": "图纸要求表面处理"},
            {"seq": 70, "step_code": "PS-INSP-FINAL", "depends_on": [50]},
        ],
    },
    {
        "route": {"route_code": "RT-SHEET-BOX", "name": "钣金箱体典型路线",
                  "applicable_category": "钣金件", "applicable_material": ["金属"],
                  "batch_min": 1, "batch_max": 2000,
                  "summary": "激光下料 → 折弯 → 焊接 → 去毛刺 → 表面处理 → 终检"},
        "steps": [
            {"seq": 10, "step_code": "PS-BLANK-LASER"},
            {"seq": 20, "step_code": "PS-BEND-SHEET", "depends_on": [10]},
            {"seq": 30, "step_code": "PS-WELD-MIG", "depends_on": [20]},
            {"seq": 40, "step_code": "PS-BENCH-DEBURR", "depends_on": [30]},
            {"seq": 50, "step_code": "PS-SURF-PLATE", "depends_on": [40]},
            {"seq": 60, "step_code": "PS-INSP-FINAL", "depends_on": [50]},
        ],
    },
    {
        "route": {"route_code": "RT-SHAFT-TURNED", "name": "回转类零件典型路线",
                  "applicable_category": "回转件", "applicable_material": ["金属"],
                  "batch_min": 1, "batch_max": 1000,
                  "summary": "锯切下料 → 车外圆 → 调质 → 钻孔 → 去毛刺 → 终检"},
        "steps": [
            {"seq": 10, "step_code": "PS-BLANK-SAW"},
            {"seq": 20, "step_code": "PS-TURN-ROUGH", "depends_on": [10]},
            {"seq": 30, "step_code": "PS-HEAT-QT", "is_optional": 1, "depends_on": [20],
             "condition_expr": "图纸有硬度要求"},
            {"seq": 40, "step_code": "PS-DRILL-HOLE", "is_optional": 1, "depends_on": [20]},
            {"seq": 50, "step_code": "PS-BENCH-DEBURR", "depends_on": [40]},
            {"seq": 60, "step_code": "PS-INSP-FINAL", "depends_on": [50]},
        ],
    },
]

MATERIALS = [
    {"material": {"material_code": "MAT-STL-Q235", "name": "碳素结构钢板", "grade": "Q235",
                  "category": "金属", "form": "板材", "spec": "热轧板 t1~t20",
                  "density": 7.85, "base_unit": "kg", "standard_loss_rate": 0.08},
     "properties": [{"prop_key": "tensile", "prop_name": "抗拉强度", "value_num": 375, "unit": "MPa"},
                    {"prop_key": "yield", "prop_name": "屈服强度", "value_num": 235, "unit": "MPa"}],
     "price": {"price": 4.2, "unit": "kg", "price_type": "internal_purchase",
               "source_name": "2026 年度采购框架协议"}},
    {"material": {"material_code": "MAT-STL-45", "name": "优质碳素结构钢", "grade": "45",
                  "category": "金属", "form": "棒材", "spec": "φ20~φ200 热轧圆钢",
                  "density": 7.85, "base_unit": "kg", "standard_loss_rate": 0.12},
     "properties": [{"prop_key": "tensile", "prop_name": "抗拉强度", "value_num": 600, "unit": "MPa"},
                    {"prop_key": "hardness", "prop_name": "调质硬度", "value_text": "HB220-250"}],
     "price": {"price": 5.1, "unit": "kg", "price_type": "internal_purchase",
               "source_name": "2026 年度采购框架协议"}},
    {"material": {"material_code": "MAT-SUS-304", "name": "奥氏体不锈钢板", "grade": "304",
                  "category": "金属", "form": "板材", "spec": "冷轧板 t0.5~t6",
                  "density": 7.93, "base_unit": "kg", "standard_loss_rate": 0.10},
     "properties": [{"prop_key": "tensile", "prop_name": "抗拉强度", "value_num": 520, "unit": "MPa"},
                    {"prop_key": "corrosion", "prop_name": "耐蚀性", "value_text": "耐一般大气与食品级介质"}],
     "price": {"price": 18.6, "unit": "kg", "price_type": "market",
               "source_name": "长江有色/不锈钢现货均价", "confidence": 0.8}},
    {"material": {"material_code": "MAT-AL-6061", "name": "铝合金板", "grade": "6061-T6",
                  "category": "金属", "form": "板材", "spec": "t2~t50",
                  "density": 2.70, "base_unit": "kg", "standard_loss_rate": 0.10},
     "properties": [{"prop_key": "tensile", "prop_name": "抗拉强度", "value_num": 310, "unit": "MPa"},
                    {"prop_key": "thermal_conductivity", "prop_name": "热导率",
                     "value_num": 167, "unit": "W/m·K"}],
     "price": {"price": 22.4, "unit": "kg", "price_type": "market",
               "source_name": "长江有色 A00 铝 + 加工费", "confidence": 0.8}},
    {"material": {"material_code": "MAT-CER-AL2O3-96", "name": "氧化铝陶瓷粉", "grade": "Al2O3 96%",
                  "category": "陶瓷粉体", "form": "粉末", "spec": "纯度≥96%,D50 1~3µm",
                  "density": 3.72, "base_unit": "kg", "standard_loss_rate": 0.05},
     "properties": [{"prop_key": "purity", "prop_name": "纯度", "value_num": 96, "unit": "%"},
                    {"prop_key": "d50", "prop_name": "D50 粒径", "value_num": 2.0, "unit": "µm"},
                    {"prop_key": "thermal_conductivity", "prop_name": "热导率",
                     "value_num": 24, "unit": "W/m·K"}],
     "price": {"price": 68.0, "unit": "kg", "price_type": "contract",
               "source_name": "陶瓷粉体年度合同价"}},
]

COST_RATES = [
    {"rate_code": "RATE-LABOR-GLOBAL", "name": "综合人工费率", "rate_type": "labor",
     "scope_type": "global", "value": 60.0, "unit": "元/小时", "source": "财务 2026 年度核定"},
    {"rate_code": "RATE-LABOR-CNC", "name": "机加工人工费率", "rate_type": "labor",
     "scope_type": "equipment_class", "scope_ref": "EQC-CNC-VMC", "value": 85.0,
     "unit": "元/小时", "source": "财务 2026 年度核定"},
    {"rate_code": "RATE-DEP-CNC", "name": "加工中心折旧费率", "rate_type": "equipment_dep",
     "scope_type": "equipment_class", "scope_ref": "EQC-CNC-VMC", "value": 42.0,
     "unit": "元/小时", "source": "固定资产折旧表"},
    {"rate_code": "RATE-ENERGY", "name": "工业电价", "rate_type": "energy",
     "scope_type": "global", "value": 0.78, "unit": "元/kWh", "source": "供电局工商业电价"},
    {"rate_code": "RATE-OVERHEAD", "name": "制造费用分摊", "rate_type": "overhead",
     "scope_type": "global", "value": 18.0, "unit": "元/小时", "source": "财务 2026 年度核定"},
    {"rate_code": "RATE-LOGISTICS", "name": "公路运输费率", "rate_type": "logistics",
     "scope_type": "global", "value": 0.65, "unit": "元/kg·百公里", "source": "物流招标价"},
    {"rate_code": "RATE-WAREHOUSE", "name": "仓储费率", "rate_type": "warehouse",
     "scope_type": "global", "value": 0.9, "unit": "元/m³·天", "source": "仓储合同"},
]

COST_FACTORS = [
    {"factor_code": "FCT-YIELD-DEFAULT", "name": "默认成品率", "factor_type": "yield",
     "value": 0.96, "note": "无工序良率数据时的兜底"},
    {"factor_code": "FCT-SCRAP-METAL", "name": "金属加工损耗率", "factor_type": "scrap",
     "applicable_scope": "金属", "value": 0.08},
    {"factor_code": "FCT-MARGIN-STD", "name": "标准毛利率", "factor_type": "margin", "value": 0.25},
    {"factor_code": "FCT-TAX-VAT13", "name": "增值税率", "factor_type": "tax", "value": 0.13},
]

SUPPLIERS = [
    {"supplier": {"name": "华东金属材料有限公司", "supplier_type": "原材料", "region": "江苏苏州",
                  "qualification": "ISO9001", "rating": 4.5, "contact": "021-0000-0001"},
     "capabilities": [
         {"material_code": "MAT-STL-Q235", "moq": "1 吨", "lead_time": "3 天", "price_ref": 4.2, "qualified": 1},
         {"material_code": "MAT-SUS-304", "moq": "500 kg", "lead_time": "5 天", "price_ref": 18.9, "qualified": 1},
     ]},
    {"supplier": {"name": "先进陶瓷粉体科技股份", "supplier_type": "原材料", "region": "山东淄博",
                  "qualification": "IATF16949", "rating": 4.2, "contact": "0533-0000-0002"},
     "capabilities": [
         {"material_code": "MAT-CER-AL2O3-96", "max_purity_pct": 99.5, "d50_min_um": 0.5,
          "d50_max_um": 5.0, "moq": "100 kg", "lead_time": "15 天", "price_ref": 68.0, "qualified": 1},
     ]},
]

STANDARD_PARTS = [
    {"standard_no": "GB/T 5783", "designation": "M8x25", "category": "bolt",
     "material": "8.8 级碳钢", "surface_treatment": "镀锌", "unit_price_ref": 0.42,
     "size_params": {"thread": "M8", "length": 25, "pitch": 1.25}},
    {"standard_no": "GB/T 5783", "designation": "M6x20", "category": "bolt",
     "material": "8.8 级碳钢", "surface_treatment": "镀锌", "unit_price_ref": 0.26,
     "size_params": {"thread": "M6", "length": 20, "pitch": 1.0}},
    {"standard_no": "GB/T 6170", "designation": "M8", "category": "nut",
     "material": "8 级碳钢", "surface_treatment": "镀锌", "unit_price_ref": 0.18,
     "size_params": {"thread": "M8"}},
    {"standard_no": "GB/T 97.1", "designation": "8", "category": "washer",
     "material": "碳钢", "surface_treatment": "镀锌", "unit_price_ref": 0.06,
     "size_params": {"nominal": 8}},
]

# 两个示例零部件:验证"包络粗筛 -> 参数精筛 -> 特征相似"三级漏斗。
COMPONENTS = [
    {
        "component": {
            "component_code": "CMP-PLT-0001", "name": "设备安装底板", "category": "结构件",
            "subcategory": "板类", "source_type": "self_made",
            "spec_summary": "Q235 板 200×120×10,4-φ9 安装孔",
            "default_material_code": "MAT-STL-Q235", "default_route_code": "RT-PLATE-MACHINED",
            "envelope_l": 200, "envelope_w": 120, "envelope_h": 10, "mass_kg": 1.88,
            "manufacturability": "mature", "rev": "A", "tags": ["底板", "通用"],
        },
        "params": [
            {"param_key": "length", "param_name": "长", "value_num": 200, "unit": "mm",
             "tol_lower": 5, "tol_upper": 5, "is_key": 1},
            {"param_key": "width", "param_name": "宽", "value_num": 120, "unit": "mm",
             "tol_lower": 5, "tol_upper": 5, "is_key": 1},
            {"param_key": "thickness", "param_name": "厚", "value_num": 10, "unit": "mm",
             "tol_lower": 1, "tol_upper": 1, "is_key": 1},
            {"param_key": "hole_diameter", "param_name": "安装孔径", "value_num": 9, "unit": "mm",
             "tol_lower": 0.5, "tol_upper": 0.5, "is_key": 1},
            {"param_key": "tolerance_grade", "param_name": "一般公差", "value_text": "ISO 2768-m"},
        ],
        "features": [
            {"feature_type": "plate", "length": 200, "width": 120, "thickness": 10},
            {"feature_type": "hole_pattern", "diameter": 9, "count_x": 2, "count_y": 2,
             "spacing_x": 160, "spacing_y": 80, "purpose": "M8 安装孔"},
            {"feature_type": "chamfer", "distance": 1.0},
        ],
    },
    {
        "component": {
            "component_code": "CMP-BRK-0001", "name": "L 型支架", "category": "钣金件",
            "subcategory": "支架", "source_type": "self_made",
            "spec_summary": "SUS304 板 t3,展开 180×90,单折 90°",
            "default_material_code": "MAT-SUS-304", "default_route_code": "RT-SHEET-BOX",
            "envelope_l": 180, "envelope_w": 90, "envelope_h": 3, "mass_kg": 0.39,
            "manufacturability": "mature", "rev": "A", "tags": ["支架", "钣金"],
        },
        "params": [
            {"param_key": "length", "param_name": "展开长", "value_num": 180, "unit": "mm",
             "tol_lower": 3, "tol_upper": 3, "is_key": 1},
            {"param_key": "width", "param_name": "展开宽", "value_num": 90, "unit": "mm",
             "tol_lower": 3, "tol_upper": 3, "is_key": 1},
            {"param_key": "thickness", "param_name": "板厚", "value_num": 3, "unit": "mm",
             "tol_lower": 0.2, "tol_upper": 0.2, "is_key": 1},
            {"param_key": "bend_count", "param_name": "折弯道数", "value_num": 1},
        ],
        "features": [
            {"feature_type": "plate", "length": 180, "width": 90, "thickness": 3},
            {"feature_type": "hole", "diameter": 6.5, "x": -60, "y": 0, "purpose": "M6 过孔"},
            {"feature_type": "hole", "diameter": 6.5, "x": 60, "y": 0, "purpose": "M6 过孔"},
        ],
    },
]


# --------------------------------------------------------------------------- #
def seed_all(*, overwrite: bool = False) -> dict:
    """写入种子数据(幂等)。overwrite=False 时已存在的记录不会被覆盖。"""
    counts: dict[str, int] = {}

    for code, name, category in EQUIPMENT_CLASSES:
        db.upsert("kb_equipment_class", {"class_code": code, "name": name, "category": category},
                  keys=("class_code",))
    counts["kb_equipment_class"] = len(EQUIPMENT_CLASSES)

    for item in EQUIPMENT:
        existing = db.query_one(
            "SELECT equipment_id FROM kb_equipment WHERE equipment_code = ?",
            (item["equipment_code"],),
        )
        if existing and not overwrite:
            continue
        row = dict(item)
        if existing:
            row["equipment_id"] = existing["equipment_id"]
        kb.save_equipment(row)
    counts["kb_equipment"] = len(EQUIPMENT)

    for step in PROCESS_STEPS:
        if db.query_one("SELECT step_code FROM kb_process_step WHERE step_code = ?",
                        (step["step_code"],)) and not overwrite:
            continue
        kb.save_process_step({**step, "effective_from": SEED_DATE})
    counts["kb_process_step"] = len(PROCESS_STEPS)

    for entry in ROUTES:
        code = entry["route"]["route_code"]
        if db.query_one("SELECT route_code FROM kb_process_route WHERE route_code = ?",
                        (code,)) and not overwrite:
            continue
        kb.save_route(entry["route"], entry["steps"])
    counts["kb_process_route"] = len(ROUTES)

    for entry in MATERIALS:
        code = entry["material"]["material_code"]
        exists = db.query_one("SELECT material_code FROM kb_material WHERE material_code = ?", (code,))
        if exists and not overwrite:
            continue
        kb.save_material(entry["material"], properties=entry.get("properties"))
        lib.material_dir(code, create=True)
        if not db.query_one("SELECT price_id FROM kb_material_price WHERE material_code = ?", (code,)):
            kb.add_material_price({**entry["price"], "material_code": code, "valid_from": SEED_DATE})
    counts["kb_material"] = len(MATERIALS)

    for rate in COST_RATES:
        if db.query_one("SELECT rate_code FROM kb_cost_rate WHERE rate_code = ?",
                        (rate["rate_code"],)) and not overwrite:
            continue
        kb.save_cost_rate({**rate, "effective_from": SEED_DATE})
    counts["kb_cost_rate"] = len(COST_RATES)

    for factor in COST_FACTORS:
        if db.query_one("SELECT factor_code FROM kb_cost_factor WHERE factor_code = ?",
                        (factor["factor_code"],)) and not overwrite:
            continue
        kb.save_cost_factor({**factor, "effective_from": SEED_DATE})
    counts["kb_cost_factor"] = len(COST_FACTORS)

    for entry in SUPPLIERS:
        existing = db.query_one("SELECT supplier_id FROM kb_supplier WHERE name = ?",
                                (entry["supplier"]["name"],))
        if existing and not overwrite:
            continue
        row = dict(entry["supplier"])
        if existing:
            row["supplier_id"] = existing["supplier_id"]
        kb.save_supplier(row, capabilities=entry["capabilities"])
    counts["kb_supplier"] = len(SUPPLIERS)

    for part in STANDARD_PARTS:
        existing = db.query_one(
            "SELECT std_id FROM kb_standard_part WHERE standard_no = ? AND designation = ?",
            (part["standard_no"], part["designation"]),
        )
        if existing and not overwrite:
            continue
        row = dict(part)
        if existing:
            row["std_id"] = existing["std_id"]
        row["drawing_path"] = lib.rel_path(
            lib.standard_part_dir(part["standard_no"], part["designation"], create=True)
        )
        kb.save_standard_part(row)
    counts["kb_standard_part"] = len(STANDARD_PARTS)

    for entry in COMPONENTS:
        code = entry["component"]["component_code"]
        if db.query_one("SELECT component_id FROM kb_component WHERE component_code = ?",
                        (code,)) and not overwrite:
            continue
        kb.save_component(entry["component"], params=entry["params"], features=entry["features"])
    counts["kb_component"] = len(COMPONENTS)

    return counts

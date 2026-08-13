"""
工艺拆解服务: 把单个零件拆成结构化工艺路线(CAPP)。

延续平台思路 —— Claude 只产出**结构化**工艺规程(ProcessPlan),平台做确定性的
归一化与校验(按工序号排序、依赖合法性、工时合计、缺口提示)。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from ..models.ir import DesignIR, FeatureType, OpenQuestion, Part
from ..models.process import ProcessPlan, ProcessStep, ProcessType
from . import llm_client as claude_client
from . import sop

SYSTEM_PROMPT = """你是资深制造工艺工程师(工艺规程编制 / CAPP)。给定一个已经由本地规则分类的零件
(特征、材料、尺寸、公差、装配角色),请严格采用指定分类模板编制有序工艺路线，不要把所有零件都默认成机加工件。

要求:
1. 工序号 step_no 按 10 递增(10/20/30...),顺序符合工艺逻辑:
   下料/备料 → 粗加工 → 半精加工 →(必要的热处理)→ 精加工 → 钳工去毛刺 → 表面处理 → 检验。
2. 每道工序给出: 类型(type)、内容描述、设备/机床、工装夹具、刀具/量具、
   切削或工艺参数、质量/检验要求、单件工时估算(分钟)、依赖的前序工序号(depends_on)。
3. 给出毛坯 blank(毛坯类型 + 下料规格,合理留加工余量)与工艺方案概述 summary
   (选材、定位基准的确定、整体加工思路)。
4. 只依据给定信息推断。凡缺关键尺寸/公差/材料而影响定工艺参数的,写进 open_questions
   并相应降低该工序的 confidence,**不要臆造精确的切削参数或公差数值**。
5. 工序数量与零件复杂度匹配(简单板件约 3~6 道,复杂件可更多),不要遗漏去毛刺与最终检验。
6. 全程用中文填写各字段；但 type 必须严格使用以下英文枚举之一：
   blank、turning、milling、drilling、boring、grinding、bench、sheet_metal、
   welding、heat_treat、surface、assembly、inspection、other。
   step_no、depends_on、duration_min、confidence 必须分别输出整数、整数数组、数值、0~1 数值，
   不要输出“高/中/低”、百分号或“约 10 分钟”这类文字。"""


def classify_part(part: Part) -> str:
    """使用通用、可解释规则选择工艺模板，不依赖企业设备数据。"""
    rules = sop.process_rules()
    text = " ".join(filter(None, [part.name, part.role, part.model_no])).lower()
    standard_keywords = rules["class_keywords"]["standard_part"]
    explicit_purchase = any(keyword.lower() in text for keyword in ("标准件", "外购"))
    looks_like_catalog_item = bool(part.model_no) or not part.features
    if explicit_purchase or (
        looks_like_catalog_item
        and any(keyword.lower() in text for keyword in standard_keywords)
    ):
        return "standard_part"
    for part_class in ("welded", "sheet_metal"):
        if any(keyword.lower() in text for keyword in rules["class_keywords"][part_class]):
            return part_class
    if part.features:
        base = part.features[0]
        if base.type.value == "plate" and base.thickness is not None:
            if base.thickness <= float(rules["thin_plate_max_mm"]):
                return "sheet_metal"
    return "machining"


def input_gaps(part: Part) -> list[str]:
    gaps: list[str] = []
    if not part.material:
        gaps.append("材料牌号缺失，不能确定精确工艺参数")
    if not part.features:
        gaps.append("零件没有几何特征")
    if not part.tolerance_general:
        gaps.append("未提供一般公差，精加工与检验等级需确认")
    return gaps


def _part_prompt(part: Part, overall: Optional[DesignIR], geom: Optional[dict],
                 part_class: str, gaps: list[str]) -> str:
    lines = [f"零件编号: {part.part_id}", f"零件名称: {part.name}"]
    if part.role:
        lines.append(f"装配角色/功能: {part.role}")
    if part.material:
        m = part.material
        lines.append(f"材料: {m.spec}" + (f" (密度 {m.density} g/cm³)" if m.density else ""))
    if part.tolerance_general:
        lines.append(f"一般公差: {part.tolerance_general}")
    lines.append(f"数量: {part.quantity}")
    lines.append(f"本地规则确定的零件分类/模板: {part_class}")
    if gaps:
        lines.append("输入缺口（不得自行编造）: " + "；".join(gaps))

    if part.features:
        lines.append("特征(几何构成):")
        for i, f in enumerate(part.features, 1):
            dims = ", ".join(
                f"{k}={v}" for k, v in f.model_dump().items()
                if v is not None and k not in ("type", "purpose")
            )
            purpose = f" — {f.purpose}" if f.purpose else ""
            lines.append(f"  {i}. {f.type.value}: {dims}{purpose}")

    if geom:
        g = []
        if geom.get("bbox"):
            g.append("包围盒 " + "×".join(str(x) for x in geom["bbox"]) + " mm")
        if geom.get("volume_mm3"):
            g.append(f"体积 {geom['volume_mm3']} mm³")
        if geom.get("mass_g"):
            g.append(f"质量 {geom['mass_g']} g")
        if g:
            lines.append("几何属性(CAD 内核实测): " + "; ".join(g))

    if overall and overall.device_name:
        lines.append(f"\n所属设备: {overall.device_name}")
        if overall.design_intent:
            lines.append(f"设备设计意图: {overall.design_intent}")

    lines.append("\n请编制该零件的机械加工工艺路线,调用工具输出结构化 ProcessPlan。")
    return "\n".join(lines)


def decompose_process(
    part: Part, overall: Optional[DesignIR] = None, geom: Optional[dict] = None,
    note: str = "", attachments: Optional[List[Tuple[str, bytes]]] = None,
) -> ProcessPlan:
    part_class = classify_part(part)
    gaps = input_gaps(part)
    profile = sop.industry_profile([
        part.name, part.role or "", part.material.spec if part.material else "",
        overall.device_name if overall else "", overall.design_intent if overall else "", note,
    ])
    knowledge, sop_version = sop.load("process", profile=profile, template=part_class)
    prompt = _part_prompt(part, overall, geom, part_class, gaps)
    content = [claude_client.text_block(prompt)]
    content.insert(0, claude_client.text_block(
        "【本次适用 SOP 与通用规则】\n" + knowledge
        + "\n\n当前没有企业设备/刀具/标准工时库；只能使用通用设备和工具类别，不得编造企业资源编号。"
    ))
    if note and note.strip():
        content.append(claude_client.text_block(f"【用户补充说明(请优先采用)】\n{note.strip()}"))
    content.extend(claude_client.attachment_blocks(attachments))
    plan = claude_client.run(SYSTEM_PROMPT, content, ProcessPlan)
    # 确定性归一: 锚定 part_id/name, 按工序号排序
    plan.part_id = part.part_id
    if not plan.part_name:
        plan.part_name = part.name
    if not plan.material and part.material:
        plan.material = part.material.spec
    plan.part_class = part_class
    plan.sop_version = sop_version
    plan = ensure_minimum_route(plan, part)
    plan.steps.sort(key=lambda s: s.step_no)
    validation = validate_rules(plan.model_dump(), part)
    plan.rule_warnings = validation
    for gap in gaps:
        if gap not in [question.reason for question in plan.open_questions]:
            plan.open_questions.append(OpenQuestion(field=part.part_id, reason=gap))
    return plan


def _step(
    step_no: int, name: str, step_type: ProcessType, description: str,
    *, equipment: str, fixture: str, tooling: str, quality: str,
    duration_min: float, depends_on: Optional[list[int]] = None,
    confidence: float = .68,
) -> ProcessStep:
    """创建不依赖企业资源库的通用工序，不虚构具体机台编号或切削参数。"""
    return ProcessStep(
        step_no=step_no, name=name, type=step_type, description=description,
        equipment=equipment, fixture=fixture, tooling=tooling, params=None,
        quality=quality, duration_min=duration_min,
        depends_on=depends_on or [], confidence=confidence,
        note="通用工艺建议；材料、公差或企业资源补齐后可细化参数",
    )


def _base_route(part: Part, part_class: str) -> list[ProcessStep]:
    """根据零件分类和几何特征给出最小可执行路线。

    这里补的是稳定的工序骨架，不补转速、进给、公差等依赖材料/企业规则的数据。
    """
    if part_class == "standard_part":
        return [
            _step(10, "到货与规格核对", ProcessType.inspection,
                  "按零件名称、型号、数量和随附资料核对到货物料。",
                  equipment="检验工作台", fixture="周转托盘", tooling="目视、卡尺或适用通用量具",
                  quality="型号、数量和外观与采购要求一致", duration_min=3, confidence=.78),
            _step(20, "装配前处理", ProcessType.assembly,
                  "清洁、防护并确认装配接口，无需默认编制自制加工路线。",
                  equipment="装配工作台", fixture="通用定位工装", tooling="清洁工具、适用通用量具",
                  quality="装配面洁净、接口无损伤", duration_min=3, depends_on=[10], confidence=.72),
            _step(30, "最终验收", ProcessType.inspection,
                  "复核规格、关键接口、外观和随附合格文件并记录结果。",
                  equipment="检验工作台", fixture="通用检验工装", tooling="适用通用量具",
                  quality="满足采购规格与装配要求", duration_min=4, depends_on=[20], confidence=.78),
        ]

    if part_class == "sheet_metal":
        route = [
            _step(10, "板料准备与开料", ProcessType.blank,
                  "依据展开尺寸准备板料并完成剪切、激光或冲裁开料，预留必要工艺余量。",
                  equipment="剪板机、激光切割机或冲床", fixture="板料定位靠尺", tooling="通用板料量具",
                  quality="外形完整，毛坯尺寸覆盖成品轮廓", duration_min=8),
            _step(20, "主体成形或外形加工", ProcessType.sheet_metal,
                  "按零件几何完成外形、边缘及必要的折弯/成形；无折弯特征时保持平板加工。",
                  equipment="折弯机、冲床或适用数控设备", fixture="通用定位夹具", tooling="通用成形模具、卡尺",
                  quality="外形与主体尺寸符合当前图纸解析值", duration_min=12, depends_on=[10]),
        ]
    elif part_class == "welded":
        route = [
            _step(10, "构件下料", ProcessType.blank,
                  "按构件尺寸完成板材或型材下料并标识。",
                  equipment="锯床、剪板机或激光切割机", fixture="通用下料定位", tooling="卷尺、卡尺",
                  quality="下料完整、编号清晰并保留必要余量", duration_min=10),
            _step(20, "坡口与焊前清理", ProcessType.bench,
                  "清理焊接区域，按结构需要准备坡口和装配边。",
                  equipment="打磨与清理设备", fixture="装配平台", tooling="角磨机、通用量具",
                  quality="焊接区域洁净，装配边无影响焊接的缺陷", duration_min=8, depends_on=[10]),
            _step(30, "组对定位", ProcessType.assembly,
                  "按基准组对构件并进行定位，控制整体方向和接口位置。",
                  equipment="焊接装配平台", fixture="通用组对夹具", tooling="角尺、卷尺",
                  quality="组对关系与主要接口位置符合图纸", duration_min=12, depends_on=[20]),
            _step(40, "焊接", ProcessType.welding,
                  "采用与最终材料相匹配的焊接方法和顺序完成连接；材料确认后细化参数。",
                  equipment="适用焊接设备", fixture="焊接定位夹具", tooling="焊接工具、焊缝量规",
                  quality="焊缝连续，外观无明显裂纹、未熔合等缺陷", duration_min=18, depends_on=[30], confidence=.6),
        ]
    else:
        base = part.features[0] if part.features else None
        primary_type = ProcessType.turning if base and base.type == FeatureType.cylinder else ProcessType.milling
        primary_name = "车削主体" if primary_type == ProcessType.turning else "建立基准并加工主体"
        primary_equipment = "通用数控车床" if primary_type == ProcessType.turning else "数控铣床或加工中心"
        route = [
            _step(10, "毛坯准备", ProcessType.blank,
                  "按成品包络准备适用毛坯并预留必要加工余量。",
                  equipment="锯床、切割设备或毛坯准备工位", fixture="通用下料定位", tooling="卡尺、卷尺",
                  quality="毛坯覆盖成品包络，无影响后续加工的明显缺陷", duration_min=8),
            _step(20, primary_name, primary_type,
                  "建立稳定定位基准，完成主体轮廓和主要尺寸加工；材料确认后细化切削参数。",
                  equipment=primary_equipment, fixture="通用夹具或软爪", tooling="适用通用刀具、卡尺",
                  quality="主体几何与当前图纸解析值一致", duration_min=18, depends_on=[10], confidence=.62),
        ]

    feature_types = {feature.type for feature in part.features[1:]}
    if feature_types & {FeatureType.hole, FeatureType.hole_pattern}:
        previous = route[-1].step_no
        route.append(_step(
            previous + 10, "孔系加工", ProcessType.drilling,
            "按孔特征完成定位、钻孔及必要的扩孔/铰孔/攻丝，具体方式按孔要求确定。",
            equipment="钻床或加工中心", fixture="沿用基准定位夹具", tooling="适用钻具、塞规或卡尺",
            quality="孔位、孔径和数量符合当前图纸解析值", duration_min=10,
            depends_on=[previous], confidence=.64,
        ))

    previous = route[-1].step_no
    route.extend([
        _step(previous + 10, "去毛刺与清理", ProcessType.bench,
              "去除锐边、毛刺和加工残留，清洁零件并保护关键表面。",
              equipment="钳工工作台", fixture="软质防护垫", tooling="锉刀、刮刀、去毛刺工具",
              quality="无可见毛刺和锐边，表面洁净无明显损伤", duration_min=6,
              depends_on=[previous], confidence=.82),
        _step(previous + 20, "最终检验", ProcessType.inspection,
              "按当前图纸和解析尺寸检验外形、关键尺寸、特征数量与外观；未给公差时记录实测值待确认。",
              equipment="检验工作台", fixture="通用检验平台", tooling="卡尺、卷尺及适用通用量具",
              quality="检验项目完成并形成记录，不以未知材料阻断工序输出", duration_min=8,
              depends_on=[previous + 10], confidence=.76),
    ])
    return route


def ensure_minimum_route(plan: ProcessPlan, part: Part) -> ProcessPlan:
    """模型未给步骤时补完整路线；步骤过少时至少补收尾和终检。"""
    if not plan.steps:
        plan.steps = _base_route(part, plan.part_class or classify_part(part))
        note = "AI 未返回结构化工序明细，系统已按零件分类与几何特征补齐通用工序骨架"
        plan.overall_note = f"{plan.overall_note}；{note}" if plan.overall_note else note
        return plan

    # 有可用 AI 工序时尊重原路线，只补平台 SOP 强制要求的收尾/终检。
    existing_types = {_type_value(step.type) for step in plan.steps}
    next_no = max(step.step_no for step in plan.steps) + 10
    previous = max(step.step_no for step in plan.steps)
    if not existing_types.intersection({"bench", "surface", "assembly"}):
        plan.steps.append(_step(
            next_no, "去毛刺与清理", ProcessType.bench,
            "完成去毛刺、清理和必要防护。", equipment="钳工工作台",
            fixture="通用防护工装", tooling="去毛刺与清理工具",
            quality="无明显毛刺、锐边和加工残留", duration_min=5,
            depends_on=[previous], confidence=.8,
        ))
        previous, next_no = next_no, next_no + 10
    if "inspection" not in existing_types:
        plan.steps.append(_step(
            next_no, "最终检验", ProcessType.inspection,
            "按当前图纸和工艺要求完成关键尺寸、特征及外观检验并记录结果。",
            equipment="检验工作台", fixture="通用检验平台", tooling="适用通用量具",
            quality="检验记录完整；未知公差项目记录实测值待确认", duration_min=8,
            depends_on=[previous], confidence=.76,
        ))
    return plan


def _type_value(value) -> str:
    return value.value if isinstance(value, ProcessType) else str(value or "")


def _step_value(step, field: str, default=None):
    """同时兼容持久化字典和 Pydantic ProcessStep 对象。"""
    return step.get(field, default) if isinstance(step, dict) else getattr(step, field, default)


def validate_rules(plan: dict, part: Optional[Part] = None) -> list[str]:
    """通用特征—工序和模板适用性检查；企业规则以后可叠加而无需改模型提示词。"""
    rules = sop.process_rules()
    part_class = str(plan.get("part_class") or (classify_part(part) if part else "machining"))
    steps = plan.get("steps") or []
    types = [_type_value(_step_value(step, "type")) for step in steps]
    warnings: list[str] = []
    allowed = set(rules["class_allowed_operations"].get(part_class, []))
    unexpected = sorted({item for item in types if item and item not in allowed})
    if unexpected:
        warnings.append(f"{part_class} 模板包含不常见工序：{'、'.join(unexpected)}")
    if part_class == "standard_part" and any(item in types for item in ("turning", "milling", "drilling", "welding")):
        warnings.append("标准件/外购件不应默认编制自制加工路线")
    if "inspection" not in types:
        warnings.append("工艺路线缺少最终检验")
    if part:
        for feature in part.features[1:]:
            expected = rules["feature_operations"].get(feature.type.value, [])
            if expected and not any(operation in types for operation in expected):
                warnings.append(f"特征 {feature.type.value} 未找到对应加工/处置工序")
        material = part.material.spec.lower() if part.material else ""
        for family, rule in rules.get("material_process_compatibility", {}).items():
            if not any(marker.lower() in material for marker in rule.get("markers", [])):
                continue
            text = " ".join(
                str(_step_value(step, key) or "") for step in steps
                for key in ("name", "description", "params", "note")
            )
            for forbidden in rule.get("avoid", []):
                # 通用禁忌默认作为检查提示；没有企业参数时不武断判定工艺必然失败。
                if forbidden in text:
                    warnings.append(f"材料 {part.material.spec} 的工艺路线命中通用禁忌：{forbidden}")
    return list(dict.fromkeys(warnings))


def compute(plan: dict) -> dict:
    """确定性派生量: 工序数、合计工时、依赖/顺序校验告警。"""
    steps = plan.get("steps", []) or []
    nos = [s.get("step_no") for s in steps]
    noset = set(nos)
    warnings: list[str] = []

    if len(noset) != len(nos):
        warnings.append("存在重复的工序号")

    total = 0.0
    has_dur = False
    for s in steps:
        d = s.get("duration_min")
        if isinstance(d, (int, float)):
            total += d
            has_dur = True
        sn = s.get("step_no")
        for dep in s.get("depends_on") or []:
            if dep == sn:
                warnings.append(f"工序 {sn} 依赖自身")
            elif dep not in noset:
                warnings.append(f"工序 {sn} 依赖了不存在的工序 {dep}")
            elif dep > sn:
                warnings.append(f"工序 {sn} 依赖了后续工序 {dep}(工序顺序可疑)")

    warnings.extend(plan.get("rule_warnings") or [])
    return {
        "step_count": len(steps),
        "total_duration_min": round(total, 1) if has_dur else None,
        "warnings": list(dict.fromkeys(warnings)),
    }

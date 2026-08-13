"""工艺评估需求单的零依赖 PDF 渲染。

使用 PDF 标准 CJK 字体 STSong-Light（Adobe-GB1 / UniGB-UCS2-H），不依赖
ReportLab、系统中文字体或网络安装包，适合内网精简容器部署。
"""
from __future__ import annotations

import json
from typing import Any


_LABELS = {
    "requirement_type": "需求类型", "priority": "优先级", "bu": "BU", "disclosure": "披露口径", "description": "需求描述",
    "customer_type": "新旧客户", "customer_industry": "客户行业分类", "account_manager": "客户经理", "final_customer_name": "最终客户名称", "transaction_customer_name": "交易客户名称", "customer_credit": "客户信用等级", "project_name": "项目名称", "project_code": "项目编码", "product_iteration": "全新/迭代", "project_manager": "项目经理", "technical_contact": "技术对接人",
    "product_name": "产品名称", "product_model": "产品型号", "wafer_size": "晶圆尺寸", "chuck_type": "静电吸盘类型", "temperature_zones": "温区数量", "ceramic_material": "陶瓷基体材料", "electrode_material": "电极材料", "base_material": "金属基座材质", "product_weight": "产品重量", "overall_dimensions": "外形尺寸", "ttv": "平面度（TTV）要求", "roughness": "表面粗糙度（Ra）要求", "micro_hole_diameter": "微孔孔径", "micro_hole_diameter_tolerance": "微孔孔径公差", "micro_hole_depth_tolerance": "微孔深度公差", "mesa_height": "微凸台高度", "adsorption_uniformity": "吸附力均匀性", "temperature_range": "工作温度范围", "max_voltage": "最高使用电压", "leakage_current": "漏电流要求", "helium_leak_rate": "氦气漏率要求", "cleanliness": "洁净度等级", "service_life": "使用寿命要求", "target_equipment": "目标设备类型", "process_stage": "适用工艺环节", "vacuum_environment": "真空环境要求", "heating": "是否含加热功能",
    "annual_forecast": "年预测量", "lifetime_forecast": "生命周期总预测", "first_sample_due": "期望首样交付时间", "mass_production_due": "期望量产时间", "target_price": "目标售价", "competitors": "竞争对手情况", "current_situation": "目前状况说明",
    "project_k0": "预估项目 K0 时间", "evaluation_due": "期望工艺评估完成日期", "project_start_due": "项目启动预计时间", "milestones": "关键里程碑节点", "category_a": "类型 A", "category_b": "类型 B", "product_type": "产品类型", "complexity": "工艺复杂度等级", "new_technology": "是否涉及新技术", "technology_source": "技术来源", "notes": "备注", "related_requirement": "关联需求单号",
}

_SECTIONS = [
    ("一、需求基本信息", ["requirement_type", "priority", "bu", "disclosure", "description"]),
    ("二、客户与项目信息", ["customer_type", "customer_industry", "account_manager", "final_customer_name", "transaction_customer_name", "customer_credit", "project_name", "project_code", "product_iteration", "project_manager", "technical_contact"]),
    ("三、产品技术规格", ["product_name", "product_model", "wafer_size", "chuck_type", "temperature_zones", "ceramic_material", "electrode_material", "base_material", "product_weight", "overall_dimensions", "ttv", "roughness", "micro_hole_diameter", "micro_hole_diameter_tolerance", "micro_hole_depth_tolerance", "mesa_height", "adsorption_uniformity", "temperature_range", "max_voltage", "leakage_current", "helium_leak_rate", "cleanliness", "service_life", "target_equipment", "process_stage", "vacuum_environment", "heating"]),
    ("四、市场与商务信息", ["annual_forecast", "lifetime_forecast", "first_sample_due", "mass_production_due", "target_price", "competitors", "current_situation"]),
    ("五、项目时间计划", ["project_k0", "evaluation_due", "project_start_due", "milestones"]),
    ("六、分类与标签", ["category_a", "category_b", "product_type", "complexity", "new_technology", "technology_source"]),
    ("七、备注与附件", ["notes", "related_requirement"]),
]


def _plain(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)
    return str(value).replace("\r", "").replace("\n", "；").strip() or "—"


def _wrap(text: str, width: int = 46) -> list[str]:
    """按近似 CJK 字符宽度折行，保证内容在 A4 表单内可读。"""
    return [text[index:index + width] for index in range(0, len(text), width)] or ["—"]


def _text_hex(text: str) -> str:
    # UniGB-UCS2-H 直接接受 UTF-16BE 字符码；替换无法编码的孤立 surrogate。
    return "<" + text.encode("utf-16-be", errors="replace").hex().upper() + ">"


def _page_stream(lines: list[tuple[str, int]]) -> bytes:
    commands = ["BT", "0.10 0.18 0.32 rg"]
    y = 806
    for text, size in lines:
        if y < 42:
            break
        commands.extend([f"/F1 {size} Tf", f"1 0 0 1 42 {y} Tm", f"{_text_hex(text)} Tj"])
        y -= 20 if size >= 13 else 15
    commands.append("ET")
    return "\n".join(commands).encode("ascii")


def _pdf(objects: list[bytes]) -> bytes:
    body = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, content in enumerate(objects, 1):
        offsets.append(len(body))
        body.extend(f"{number} 0 obj\n".encode())
        body.extend(content)
        body.extend(b"\nendobj\n")
    xref = len(body)
    body.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        body.extend(f"{offset:010d} 00000 n \n".encode())
    body.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(body)


def build_requirement_pdf(requirement: dict, project: dict | None = None) -> bytes:
    """根据当前已保存的需求字段生成多页中文 PDF 表单。"""
    data = requirement.get("data") or {}
    lines: list[tuple[str, int]] = [
        ("工艺评估需求单", 18),
        (f"需求编号：{_plain(requirement.get('requirement_no'))}    创建时间：{_plain(requirement.get('created_at'))}", 9),
        (f"需求名称：{_plain(requirement.get('title'))}", 10),
        (f"创建人：{_plain(requirement.get('created_by'))}    当前状态：{_plain(requirement.get('status'))}", 9),
        (f"项目图纸：{_plain((project or {}).get('source_filename'))}", 9),
        ("", 9),
    ]
    for title, keys in _SECTIONS:
        lines.append((title, 13))
        for key in keys:
            label = _LABELS[key]
            wrapped = _wrap(f"{label}：{_plain(data.get(key))}")
            lines.extend((part, 9) for part in wrapped)
        lines.append(("", 9))
    lines.append(("本表单由 AI 工艺平台根据当前已保存的需求数据实时生成，供确认、审核、归档与下载使用。", 8))
    # 每页最多约 50 行（标题行高度更大，这里保守切分保证不溢出）。
    pages = [lines[index:index + 47] for index in range(0, len(lines), 47)] or [[("工艺评估需求单", 18)]]
    page_ids = [4 + index * 2 for index in range(len(pages))]
    content_ids = [page_id + 1 for page_id in page_ids]
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode(),
        b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light /Encoding /UniGB-UCS2-H /DescendantFonts [ << /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light /CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 2 >> /DW 1000 >> ] >>",
    ]
    for page_id, content_id, page in zip(page_ids, content_ids, pages):
        stream = _page_stream(page)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>".encode())
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    return _pdf(objects)

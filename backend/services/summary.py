"""
技术工艺总结服务(第 8 步)。

- aggregate(): 汇总前面各步(材料/制造工艺/清洗/组装检测/产线)的已存结果 + 项目信息。
- render_markdown()/render_html(): 由汇总数据生成可导出文档(含执行摘要 + 各步全部结构化内容)。
- recommend(): Claude 依据汇总数据生成执行摘要(纯文字结论)。
各步表格的渲染由统一的 SECTIONS 规格驱动,新增/调整列只改规格即可。
"""
from __future__ import annotations

import html as _html
import time
from typing import Any, Dict, List, Optional

from ..models.summary import SummaryRecommendation
from . import claude_client
from ..storage import store

# 每一步: (doc 键, 标题, 标量字段[(点路径,标签)], 表格[(标签,列表点路径,列[(键,表头)])])
SECTIONS: List[dict] = [
    {
        "key": "material", "title": "材料定性与供应链拆解",
        "fields": [("body.selected", "选定主体材料"), ("body.rationale", "选材理由"),
                   ("metallization.rationale", "金属化方案"), ("supply.conclusion", "供应商结论")],
        "tables": [
            ("陶瓷主体材料候选", "body.candidates",
             [("material", "材料"), ("thermal_conductivity", "热导率"), ("cte", "CTE"),
              ("cost_level", "成本"), ("recommended", "推荐")]),
            ("电极浆料", "metallization.paste",
             [("component", "成分"), ("ratio_pct", "占比%"), ("role", "作用")]),
            ("金属化层", "metallization.layers",
             [("layer", "层"), ("material", "材料"), ("thickness_um", "厚度µm"), ("process", "工艺")]),
            ("粉末要求", "supply.requirements",
             [("material", "物料"), ("purity_pct_min", "最低纯度%"), ("d50_um_min", "D50下限"), ("d50_um_max", "D50上限")]),
            ("供应商匹配", "supply.matches",
             [("supplier", "供应商"), ("material", "物料"), ("qualified", "达标"), ("gap_notes", "说明")]),
        ],
    },
    {
        "key": "manufacturing", "title": "制造工艺路径规划和BOM编制",
        "fields": [("path.summary", "路径思路"), ("bom.summary", "BOM结构")],
        "tables": [
            ("核心工艺路径", "path.steps",
             [("seq", "#"), ("name", "工序"), ("equipment", "设备"), ("params", "参数"), ("critical", "关键")]),
            ("其它关键工艺", "additional",
             [("name", "工艺"), ("needed", "需要"), ("reason", "理由")]),
            ("工艺BOM", "bom.items",
             [("ref", "序号"), ("item", "项目"), ("category", "类别"), ("spec", "规格"),
              ("quantity", "数量"), ("unit", "单位"), ("from_step", "来源工序")]),
        ],
    },
    {
        "key": "cleaning", "title": "清洗与洁净度管控方案制定",
        "fields": [("cleanliness_grade", "洁净度等级"), ("grade_source", "等级来源"), ("summary", "方案概述")],
        "tables": [
            ("化学清洗方案", "chemical_steps",
             [("seq", "#"), ("name", "工步"), ("agent", "清洗剂/酸洗液"), ("concentration", "浓度"),
              ("temperature", "温度"), ("target", "去除对象")]),
            ("高纯水终漂洗", "rinse_steps",
             [("seq", "#"), ("medium", "介质"), ("method", "方式"), ("spec", "终点指标")]),
            ("洁净度管控", "controls",
             [("item", "管控项"), ("method", "方法"), ("criteria", "判定标准")]),
        ],
    },
    {
        "key": "assembly", "title": "组装与检测方案制定",
        "fields": [("assembly.method", "连接方式"), ("assembly.rationale", "选择理由"), ("summary", "方案概述")],
        "tables": [
            ("组装工艺", "assembly.steps",
             [("seq", "#"), ("name", "工步"), ("method", "方式"), ("material", "焊料/胶"),
              ("glue_thickness", "涂胶厚度"), ("cure_temperature", "固化/焊接温度")]),
            ("检测方案", "inspection.tests",
             [("name", "检测项"), ("category", "类别"), ("method", "方法"), ("criteria", "判定标准")]),
        ],
    },
    {
        "key": "production", "title": "产线匹配与产能评估",
        "fields": [("capacity_summary", "产能评估"), ("conclusion", "结论")],
        "tables": [
            ("设备需求", "requirements",
             [("process", "工序"), ("equipment_type", "设备类型"), ("key_requirement", "关键要求")]),
            ("自有产线匹配", "inhouse.matches",
             [("equipment_type", "设备类型"), ("matched_equipment", "匹配设备"), ("satisfied", "满足"), ("capacity", "产能")]),
            ("外协安排", "outsourcing.plans",
             [("equipment_type", "设备类型"), ("vendor", "厂商"), ("satisfied", "满足"),
              ("cost", "成本"), ("recommend", "建议外协")]),
        ],
    },
]

_LOADERS = {
    "material": store.load_material,
    "manufacturing": store.load_manufacturing,
    "cleaning": store.load_cleaning,
    "assembly": store.load_assembly,
    "production": store.load_production,
}


def _get(obj: Any, dotted: str) -> Any:
    cur = obj
    for k in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            return None
        if cur is None:
            return None
    return cur


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "是" if v else "否"
    return str(v)


def aggregate(project_id: str) -> Dict[str, Any]:
    meta = store.load_meta(project_id) or {}
    ir = store.load_ir(project_id) or {}
    steps = {key: (loader(project_id) or {}) for key, loader in _LOADERS.items()}
    summary = store.load_summary(project_id) or {}
    return {
        "project_id": project_id,
        "device_name": ir.get("device_name") or meta.get("source_filename"),
        "meta": meta, "ir": ir, "steps": steps, "summary": summary,
    }


# --------------------------------------------------------------------------- #
# 文档渲染
# --------------------------------------------------------------------------- #
def _timing_line(doc: dict) -> str:
    t = doc.get("timing") or {}
    st = {"done": "已完成", "in_progress": "进行中"}.get(t.get("status"), "未开始")
    seg = f"状态: {st}"
    if t.get("started_at"):
        seg += f"; 开始: {t['started_at']}"
    if t.get("finished_at"):
        seg += f"; 完成: {t['finished_at']}"
    return seg


def render_markdown(agg: Dict[str, Any]) -> str:
    s = agg.get("summary") or {}
    out: List[str] = []
    out.append(f"# {s.get('title') or '技术工艺总结报告'}")
    out.append(f"- 器件/设备: {agg.get('device_name') or '—'}")
    out.append(f"- 项目编号: {agg.get('project_id')}")
    out.append(f"- 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    out.append("## 执行摘要")
    if s.get("overview"):
        out.append(s["overview"])
    if s.get("highlights"):
        out.append("\n**关键亮点**")
        out += [f"- {x}" for x in s["highlights"]]
    if s.get("risks"):
        out.append("\n**风险与待办**")
        out += [f"- {x}" for x in s["risks"]]
    if s.get("conclusion"):
        out.append(f"\n**结论**: {s['conclusion']}")
    out.append("")

    for sec in SECTIONS:
        doc = agg["steps"].get(sec["key"]) or {}
        out.append(f"## {sec['title']}")
        out.append(f"_{_timing_line(doc)}_\n")
        for path, label in sec["fields"]:
            v = _get(doc, path)
            if v:
                out.append(f"- **{label}**: {_fmt(v)}")
        for label, list_path, cols in sec["tables"]:
            rows = _get(doc, list_path) or []
            if not rows:
                continue
            out.append(f"\n**{label}**\n")
            out.append("| " + " | ".join(h for _, h in cols) + " |")
            out.append("| " + " | ".join("---" for _ in cols) + " |")
            for r in rows:
                out.append("| " + " | ".join(_fmt(r.get(k)) for k, _ in cols) + " |")
        out.append("")
    return "\n".join(out)


def render_html(agg: Dict[str, Any]) -> str:
    s = agg.get("summary") or {}
    e = _html.escape

    def table(label: str, rows: list, cols: list) -> str:
        if not rows:
            return ""
        th = "".join(f"<th>{e(h)}</th>" for _, h in cols)
        trs = ""
        for r in rows:
            tds = "".join(f"<td>{e(_fmt(r.get(k)))}</td>" for k, _ in cols)
            trs += f"<tr>{tds}</tr>"
        return f"<h4>{e(label)}</h4><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"

    parts: List[str] = []
    parts.append(f"<h1>{e(s.get('title') or '技术工艺总结报告')}</h1>")
    parts.append("<p class='meta'>器件/设备: " + e(_fmt(agg.get("device_name"))) +
                 " ｜ 项目编号: " + e(_fmt(agg.get("project_id"))) +
                 " ｜ 生成时间: " + time.strftime("%Y-%m-%d %H:%M:%S") + "</p>")

    parts.append("<h2>执行摘要</h2>")
    if s.get("overview"):
        parts.append(f"<p>{e(s['overview'])}</p>")
    if s.get("highlights"):
        parts.append("<p><b>关键亮点</b></p><ul>" + "".join(f"<li>{e(x)}</li>" for x in s["highlights"]) + "</ul>")
    if s.get("risks"):
        parts.append("<p><b>风险与待办</b></p><ul>" + "".join(f"<li>{e(x)}</li>" for x in s["risks"]) + "</ul>")
    if s.get("conclusion"):
        parts.append(f"<p><b>结论</b>: {e(s['conclusion'])}</p>")

    for sec in SECTIONS:
        doc = agg["steps"].get(sec["key"]) or {}
        parts.append(f"<h2>{e(sec['title'])}</h2>")
        parts.append(f"<p class='timing'>{e(_timing_line(doc))}</p>")
        fields = [(label, _get(doc, path)) for path, label in sec["fields"]]
        fields = [(l, v) for l, v in fields if v]
        if fields:
            parts.append("<ul>" + "".join(f"<li><b>{e(l)}</b>: {e(_fmt(v))}</li>" for l, v in fields) + "</ul>")
        for label, list_path, cols in sec["tables"]:
            parts.append(table(label, _get(doc, list_path) or [], cols))

    style = (
        "body{font-family:'Microsoft YaHei',Arial,sans-serif;color:#1f2329;max-width:980px;"
        "margin:24px auto;padding:0 20px;line-height:1.6}"
        "h1{font-size:24px;border-bottom:2px solid #1677ff;padding-bottom:8px}"
        "h2{font-size:18px;margin-top:28px;border-left:4px solid #1677ff;padding-left:10px}"
        "h4{margin:14px 0 6px;color:#345}"
        ".meta,.timing{color:#646a73;font-size:13px}"
        "table{border-collapse:collapse;width:100%;margin:6px 0 14px;font-size:13px}"
        "th,td{border:1px solid #d9d9d9;padding:6px 8px;text-align:left}"
        "th{background:#f5f7fa}"
        "@media print{a{display:none}}"
    )
    return (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'>"
        f"<title>{e(s.get('title') or '技术工艺总结报告')}</title><style>{style}</style></head>"
        f"<body>{''.join(parts)}"
        "<p style='margin-top:24px'><a href='javascript:window.print()'>🖨 打印 / 另存为 PDF</a></p>"
        "</body></html>"
    )


# --------------------------------------------------------------------------- #
# Claude 执行摘要
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """你是资深技术工艺总工。下面是某器件从材料定性、制造工艺、清洗、组装检测到
产线匹配各阶段的结构化结论汇总。请据此写一份**执行摘要**:overview(总体概述)、highlights
(关键亮点/已确定的方案要点)、risks(风险与待办/未决项)、conclusion(能否量产/下一步建议)。
只做提炼总结,不要编造未给出的数据。全程中文,调用工具输出结构化 SummaryRecommendation。"""


def recommend(agg: Dict[str, Any], web: bool = False) -> SummaryRecommendation:
    import json
    brief = {
        "device": agg.get("device_name"),
        "steps": {k: v for k, v in (agg.get("steps") or {}).items()},
    }
    text = "各阶段结论汇总(JSON):\n" + json.dumps(brief, ensure_ascii=False)[:18000]
    content = [claude_client.text_block(text)]
    extra_tools = [claude_client.WEB_SEARCH_TOOL] if web else None
    return claude_client.run(SYSTEM_PROMPT, content, SummaryRecommendation, extra_tools=extra_tools)

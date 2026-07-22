const API = "";
const $ = (id) => document.getElementById(id);
const pid = new URLSearchParams(location.search).get("project");
const authToken = localStorage.getItem("authToken") || "";

function api(path) {
  const headers = authToken ? { Authorization: `Bearer ${authToken}` } : {};
  return fetch(`${API}${path}`, { headers }).then(async (response) => {
    const text = await response.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
    if (!response.ok) throw new Error(data.detail || data.raw || `HTTP ${response.status}`);
    return data;
  });
}
function media(path) { return authToken ? `${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(authToken)}` : path; }
function esc(value) { return String(value ?? "").replace(/[&<>\"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[char])); }
function stage(meta) { const stages = meta?.stages || {}; if (stages.geometry && stages.drawings) return "CAD 几何与 2D 图纸已生成"; if (stages.geometry) return "CAD 几何已生成"; if (stages.parsed) return "图纸解析已完成"; return "已创建，等待解析"; }
function featureText(feature) { const keys = ["length", "width", "thickness", "height", "diameter", "radius", "distance", "count_x", "count_y", "spacing_x", "spacing_y"]; const values = keys.filter((key) => feature[key] != null).map((key) => `${key}=${feature[key]}`); return `${feature.type || "特征"}${values.length ? `（${values.join("，")} mm）` : ""}${feature.purpose ? ` · ${feature.purpose}` : ""}`; }
function auditName(action) { return ({ create_project:"创建项目", parse:"图纸解析", verified:"AI 校验", decomposed:"拆解推荐", geometry:"生成 CAD 几何", drawings:"生成 2D 工程图", export_bom_csv:"导出 BOM", restore_version:"恢复版本" }[action] || action || "系统操作"); }
function icon(symbol, tone) { return `<span class="section-icon ${tone}">${symbol}</span>`; }
function section(symbol, tone, title, content) { return `<section class="section-card"><header class="section-header">${icon(symbol, tone)}<h2 class="section-title">${title}</h2></header><div class="section-content">${content}</div></section>`; }
function collapsedSection(symbol, tone, title, content) { return `<details class="section-card collapse-section"><summary class="section-header">${icon(symbol, tone)}<h2 class="section-title">${title}</h2><span class="collapse-arrow">⌄</span></summary><div class="section-content">${content}</div></details>`; }
function evidenceDisclosure(title, content) { return `<details class="evidence-disclosure"><summary>${esc(title)}<span>⌄</span></summary><div class="evidence-content">${content}</div></details>`; }

function parameterRows(parts, ir) {
  const rows = [];
  if (ir.overall_dims) rows.push(["基础规格", "总体尺寸", ir.overall_dims, "作为工艺建模与检验的总体尺寸依据"]);
  parts.forEach((part) => {
    const material = part.material?.spec;
    if (material) rows.push([`${part.part_id} ${part.name}`, "材料", material, "按图纸材料牌号进行来料确认与工艺适配"]);
    (part.features || []).forEach((feature) => rows.push([`${part.part_id} ${part.name}`, feature.type || "几何特征", featureText(feature), "以结构化特征作为制造、检测与复核依据"]));
  });
  (ir.standard_parts || []).forEach((item) => rows.push(["标准件", item.category || "标准件", `${item.spec || "待确认"} ×${item.quantity ?? 1}`, "按标准规格采购并在装配前核验"]));
  return rows;
}
function draftSteps(parts, ir) {
  if (!parts.length) return [];
  const types = new Set(parts.flatMap((part) => (part.features || []).map((feature) => feature.type)));
  const steps = [
    ["图纸与材料确认", `核对 ${parts.length} 个已识别零件、材料牌号及总体尺寸；未确认项进入人工复核。`],
    ["基体制造与尺寸加工", "根据零件基体特征完成下料、粗加工与精加工，并保留尺寸检测记录。"],
  ];
  if (types.has("hole") || types.has("hole_pattern")) steps.push(["孔系与功能特征加工", "按图纸特征参数加工孔、孔阵列及相关功能结构，完成位置度和尺寸复核。"]);
  if (types.has("fillet") || types.has("chamfer")) steps.push(["边缘处理与去毛刺", "对倒圆、倒角及锐边进行工艺处理，避免装配和使用风险。"]);
  steps.push(["清洗、检验与装配准备", "按材料和使用环境完成清洗、外观/尺寸检验，并核验标准件与装配关系。"]);
  if (ir.assembly_notes) steps.push(["装配与终检", ir.assembly_notes]);
  return steps;
}

async function render() {
  if (!pid) { $("reportRoot").innerHTML = `<section class="report-header"><h1 class="report-title">图纸解析报告</h1><p class="loading">缺少项目编号。请从图纸工作台打开报告。</p></section>`; return; }
  try {
    const encoded = encodeURIComponent(pid);
    const [project, bomResult, auditResult, versionsResult, requirementResult] = await Promise.all([
      api(`/api/projects/${encoded}`), api(`/api/projects/${encoded}/bom`).catch(() => ({ rows: [] })),
      api(`/api/projects/${encoded}/audit`).catch(() => ({ audit: [] })), api(`/api/projects/${encoded}/versions`).catch(() => ({ versions: [] })),
      api(`/api/projects/${encoded}/requirement`).catch(() => ({ requirement: null })),
    ]);
    const meta = project.meta || {}, ir = project.ir || {}, requirement = requirementResult.requirement || {};
    const reqData = requirement.data || {}, parts = ir.parts || [], assemblies = ir.assemblies || [], questions = ir.open_questions || [];
    const rows = bomResult.rows || [], versions = versionsResult.versions || [], lowConfidence = parts.filter((part) => Number(part.confidence ?? 0) < .75);
    const avgConfidence = parts.length ? Math.round(parts.reduce((sum, part) => sum + Number(part.confidence ?? 0), 0) / parts.length * 100) : null;
    const source = media(`/api/projects/${encoded}/source`);
    const reportNo = `PR-${pid.toUpperCase()}`;
    const reportMeta = [["图纸解析报告编号", reportNo], ["对应需求单号", requirement.requirement_no || `REQ-${pid.toUpperCase()}`], ["解析人/日期", `${meta.owner || "系统"} / ${meta.created_at || "—"}`], ["客户代号", reqData.final_customer_name || reqData.transaction_customer_name || "—"], ["产品名称", ir.device_name || reqData.product_name || meta.source_filename || "—"]];
    const overviewRows = [["1. 图纸概览与完整性检查", `已加载原始文件「${meta.source_filename || "—"}」。当前项目状态：${stage(meta)}；已识别 ${parts.length} 个零件。`], ["2. 关键结构解析", parts.length ? `识别零件：${parts.map((part) => `${part.part_id} ${part.name}`).join("；")}。` : "尚未生成可用的零件结构数据。"], ["3. 设计意图与工艺难点解析", ir.design_intent || "尚未生成结构化设计意图；请先在图纸工作台执行解析。"], ["4. 初步风险评估", questions.length || lowConfidence.length ? `待复核项 ${questions.length + lowConfidence.length} 个，详见报告末尾“待澄清项与复核重点”。` : "当前未发现自动识别出的待澄清项，仍建议工程师完成最终复核。"]];
    const overview = `<table class="info-table"><tbody>${overviewRows.map(([label, value]) => `<tr><td>${esc(label)}</td><td>${esc(value)}</td></tr>`).join("")}</tbody></table>`;
    const parameterData = parameterRows(parts, ir);
    const parameters = parameterData.length ? `<table class="param-table"><thead><tr><th>参数类别</th><th>参数名称</th><th>图纸标注值/要求</th><th>工艺转化说明/内部标准</th></tr></thead><tbody>${parameterData.map((row) => `<tr>${row.map((cell) => `<td>${esc(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table>` : `<div class="empty-state">尚无可提取参数。完成图纸解析后将根据零件特征自动汇总。</div>`;
    const drafts = draftSteps(parts, ir);
    const draftContent = drafts.length ? `<p class="section-intro">基于当前已保存的图纸解析结果，初步确定的工艺路线概要如下：</p><div class="process-steps">${drafts.map(([title, desc], index) => `<div class="process-step"><span class="step-number">${index + 1}</span><div><div class="step-title">${esc(title)}</div><div class="step-desc">${esc(desc)}</div></div></div>`).join("")}</div>` : `<div class="empty-state">尚无可生成的工艺转化初稿，请先完成图纸解析。</div>`;

    const partTable = parts.length ? `<table class="param-table"><thead><tr><th>编号</th><th>零件</th><th>数量</th><th>材料</th><th>识别特征</th><th>置信度</th></tr></thead><tbody>${parts.map((part) => `<tr><td>${esc(part.part_id)}</td><td><strong>${esc(part.name)}</strong>${part.role ? `<br><small>${esc(part.role)}</small>` : ""}</td><td>${esc(part.quantity ?? 1)}</td><td>${esc(part.material?.spec || "待确认")}</td><td>${esc((part.features || []).map(featureText).join("；") || "—")}</td><td>${Math.round(Number(part.confidence ?? 0) * 100)}%</td></tr>`).join("")}</tbody></table>` : `<div class="empty-state">尚无零件结构数据。</div>`;
    const assemblyList = assemblies.length ? `<ul class="content-list">${assemblies.map((item) => `<li><strong>${esc(item.assembly_id)} ${esc(item.name)}</strong>${item.role ? ` · ${esc(item.role)}` : ""} · 数量 ×${esc(item.quantity ?? 1)}</li>`).join("")}</ul>` : `<div class="empty-state">当前项目未识别独立总成层级。</div>`;
    const structure = `<div class="report-metrics"><div class="metric"><span class="metric-value">${parts.length}</span><span class="metric-label">个已识别零件</span></div><div class="metric"><span class="metric-value">${avgConfidence == null ? "—" : `${avgConfidence}%`}</span><span class="metric-label">平均识别置信度</span></div></div><h3>总成结构</h3>${assemblyList}<h3>零件明细</h3>${partTable}`;
    const issues = [...questions.map((item) => ({ label:item.field, detail:item.reason, guess:item.guess })), ...lowConfidence.map((part) => ({ label:`${part.part_id} ${part.name}`, detail:`识别置信度 ${Math.round(Number(part.confidence ?? 0) * 100)}%，建议人工核对图纸标注。`, guess:part.provenance?.note }))];
    const risk = issues.length ? `<ul class="content-list warning">${issues.map((item) => `<li><strong>${esc(item.label || "待确认项")}</strong><br>${esc(item.detail || "需人工确认")}${item.guess ? `<br><small>当前依据：${esc(item.guess)}</small>` : ""}</li>`).join("")}</ul>` : `<div class="empty-state">暂无待澄清问题；本报告仍应由工程师进行最终复核。</div>`;
    const bomTable = rows.length ? `<div class="table-scroll"><table class="param-table"><thead><tr>${Object.keys(rows[0]).map((key) => `<th>${esc(key)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${Object.keys(rows[0]).map((key) => `<td>${esc(typeof row[key] === "object" ? JSON.stringify(row[key]) : row[key] ?? "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : `<div class="empty-state">尚无可导出的 BOM。</div>`;
    const drawings = (project.drawings?.parts || []).filter((part) => part.ok);
    const drawingLinks = drawings.flatMap((part) => Object.entries(part.views || {}).map(([view, url]) => `<a class="source-link" target="_blank" rel="noopener" href="${media(url)}">${esc(part.name || part.part_id)} · ${esc(view)}视图</a>`));
    const evidence = `${evidenceDisclosure("原始图纸", `<img class="source-image" src="${source}" alt="项目原始图纸" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'empty-image',textContent:'原始图纸暂不可预览'}))" />`)}${evidenceDisclosure("可用导出", `<div class="source-links"><a class="source-link" target="_blank" rel="noopener" href="${media(`/api/projects/${encoded}/bom.csv`)}">导出 BOM（CSV）</a>${drawingLinks.join("") || '<span class="metric-label">尚未生成 2D 工程图</span>'}</div>`)}${evidenceDisclosure("BOM 预览", bomTable)}`;
    const audit = (auditResult.audit || []).slice().reverse();
    const auditContent = audit.length ? `<div class="audit-list">${audit.map((entry) => `<div class="audit-item"><div class="audit-time">${esc(entry.ts || "—")}</div><div class="audit-action">${esc(auditName(entry.action))}</div>${entry.detail ? `<div class="audit-detail">${esc(typeof entry.detail === "string" ? entry.detail : JSON.stringify(entry.detail))}</div>` : ""}</div>`).join("")}</div><p class="version-note">当前共保留 ${versions.length} 个可追溯版本。</p>` : `<div class="empty-state">暂无项目审计记录。</div>`;

    $("reportRoot").innerHTML = `<section class="report-header"><h1 class="report-title">图纸解析报告</h1><div class="report-meta">${reportMeta.map(([label, value]) => `<div class="meta-item"><span class="meta-label">${esc(label)}</span><span class="meta-value">${esc(value)}</span></div>`).join("")}</div></section>${section("▣", "blue", "一、图纸解析报告", overview)}${section("◫", "green", "二、关键参数提取表", parameters)}${section("⌁", "purple", "三、工艺转化初稿（概要）", draftContent)}${collapsedSection("◫", "purple", "四、装配与零件结构", structure)}${collapsedSection("!", "orange", "五、待澄清项与复核重点", risk)}${collapsedSection("⌁", "green", "六、BOM、原图与工程图证据", evidence)}${collapsedSection("◷", "blue", "七、处理记录、版本与校核留痕", auditContent)}<footer class="report-footer">本报告以项目 ${esc(pid)} 当前已保存的数据为准；图纸解析、修改、生成与导出均可在审计记录中追溯。</footer>`;
  } catch (error) { $("reportRoot").innerHTML = `<section class="report-header"><h1 class="report-title">图纸解析报告</h1><p class="loading">报告加载失败：${esc(error.message)}</p></section>`; }
}

$("btnFlowBack").onclick = () => {
  location.href = pid ? `requirement-detail.html?project=${encodeURIComponent(pid)}` : "home.html";
};
$("btnPrevious").onclick = () => { location.href = pid ? `index.html?project=${encodeURIComponent(pid)}` : "index.html"; };
$("btnPrint").onclick = () => window.print();
// 2.2—2.6 尚未完成对外展示，报告的下一步先直接进入 3.1 汇总结果。
// 按钮文案保留，避免影响既有业务术语与演示稿。
$("btnOpenTech").onclick = () => { location.href = `summary.html${pid ? `?project=${encodeURIComponent(pid)}` : ""}`; };
render();

// 成本分析页: 读取 ?project=&part=, 生成/展示/编辑该零件的结构化成本拆解(支持联网检索行情)。
const API = "";
const authToken = localStorage.getItem("authToken") || "";

const _fetch = window.fetch.bind(window);
window.fetch = (url, opts = {}) => {
  const hasAuth = opts.headers && (opts.headers.Authorization || opts.headers.authorization);
  if (typeof url === "string" && url.indexOf("/api/") !== -1 && authToken && !hasAuth) {
    opts = Object.assign({}, opts, {
      headers: Object.assign({}, opts.headers, { Authorization: "Bearer " + authToken }),
    });
  }
  return _fetch(url, opts).then(r => { if (r.status === 401) location.href = "index.html"; return r; });
};

const $ = (id) => document.getElementById(id);
const status = (m, busy = false) => { $("status").textContent = (busy ? "⏳ " : "") + m; };
const esc = (s) => String(s ?? "").replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const money = (v) => (v == null ? "—" : Number(v).toLocaleString("zh-CN", { maximumFractionDigits: 2 }));

const CAT_LABEL = {
  material: "材料费", machining: "机加工", standard_part: "标准件/外购", heat_treat: "热处理",
  surface: "表面处理", welding: "焊接", assembly: "装配", inspection: "检验",
  tooling: "工装摊销", logistics: "物流包装", overhead: "管理费", profit: "利润", other: "其他",
};
const CATS = Object.keys(CAT_LABEL);

const qs = new URLSearchParams(location.search);
const PID = qs.get("project");
const PART = qs.get("part");

let part = null, analysis = null, summary = null, editing = false;

$("extraFiles").addEventListener("change", () => {
  const names = [...$("extraFiles").files].map(file => file.name);
  $("extraFilesName").textContent = names.length ? names.join("、") : "未选择文件";
});

async function init() {
  try { const h = await fetch(`${API}/api/health`).then(r => r.json()); $("health").textContent = ""; }
  catch { $("health").textContent = "后端未连接"; }

  if (!PID || !PART) { status("缺少 project / part 参数,请从工作台点击零件进入。"); return; }
  $("backLink").href = `index.html?project=${encodeURIComponent(PID)}&part=${encodeURIComponent(PART)}`;

  try {
    const data = await fetch(`${API}/api/projects/${PID}`).then(r => r.json());
    part = (data.ir && data.ir.parts || []).find(p => p.part_id === PART);
  } catch { /* ignore */ }
  if (!part) { $("partName").textContent = "(未找到该零件)"; status("未找到零件,请确认已解析。"); return; }
  $("partId").textContent = part.part_id;
  $("partName").textContent = part.name || part.part_id;
  $("partMat").textContent = (part.material ? "材料 " + part.material.spec : "");

  await loadCost();
}

async function loadCost() {
  try {
    const d = await fetch(`${API}/api/projects/${PID}/parts/${PART}/cost`).then(r => r.json());
    analysis = d.analysis; summary = d.summary;
  } catch { analysis = null; }
  if (analysis) {
    $("btnGen").textContent = "🔄 重新生成";
    $("btnEdit").disabled = false;
    if (analysis.quantity) $("qtyInput").value = analysis.quantity;
    render();
    status("已加载成本分析。可「编辑」改价后保存,或「重新生成」。");
  } else {
    $("costArea").innerHTML = `<div class="panel-card"><div class="row">尚未生成成本分析。` +
      `设定批量后点击「💰 生成成本分析」,AI 将<b>联网检索当前材料/外购/加工行情</b>,` +
      `并按材料/加工/表面/管理/利润等拆解结构化成本。</div></div>`;
    status("尚未生成成本分析。");
  }
}

$("btnGen").onclick = async () => {
  const qty = Math.max(1, parseInt($("qtyInput").value) || 1);
  $("btnGen").disabled = true;
  status("已提交成本分析任务，AI 正在拆解成本；仅在当前模型支持时检索公开行情（较耗时）…", true);
  try {
    const fd = new FormData();
    fd.append("note", $("extraNote").value || "");
    for (const f of $("extraFiles").files) fd.append("attachments", f);
    const sub = await fetch(`${API}/api/projects/${PID}/parts/${PART}/cost?quantity=${qty}`,
      { method: "POST", body: fd });
    const sd = await sub.json();
    if (!sub.ok) throw new Error(sd.detail || sub.status);
    const res = await pollTask(sd.task_id);
    analysis = res.analysis; summary = res.summary; editing = false;
    $("btnGen").textContent = "🔄 重新生成"; $("btnEdit").disabled = false;
    render();
    status(`成本分析完成:${summary.item_count} 个分项,单件约 ${money(summary.computed_total)} 元`);
  } catch (e) { status("成本分析失败: " + e.message); }
  finally { $("btnGen").disabled = false; }
};

async function pollTask(taskId) {
  while (true) {
    await sleep(1500);
    let t;
    try { t = await fetch(`${API}/api/projects/${PID}/tasks/${taskId}`).then(r => r.json()); }
    catch { continue; }
    if (t.status === "succeeded") return t.result;
    if (t.status === "failed") throw new Error(t.error || "任务失败");
    status(`成本分析：${t.progress || "正在处理"}…`, true);
  }
}

$("btnEdit").onclick = () => { editing = true; $("btnEdit").style.display = "none"; $("btnSave").style.display = ""; render(); };

$("btnSave").onclick = async () => {
  collectEdits();
  status("保存成本分析…", true);
  try {
    const r = await fetch(`${API}/api/projects/${PID}/parts/${PART}/cost`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(analysis),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.status);
    analysis = d.analysis; summary = d.summary;
    editing = false; $("btnSave").style.display = "none"; $("btnEdit").style.display = "";
    render(); status("已保存(金额与合计已重算)。");
  } catch (e) { status("保存失败: " + e.message); }
};

function render() {
  if (!analysis) return;
  const cur = (summary && summary.currency) || analysis.currency || "CNY";
  let h = "";

  // 概览
  h += `<section class="panel-card"><div class="card-h">成本概览</div>`;
  h += `<div class="cost-total"><span class="v">${money(summary && summary.computed_total)}</span>` +
    `<span class="u">元 / 件 (${esc(cur)})</span><span class="q">核算批量 ${analysis.quantity || 1} 件</span></div>`;
  if (analysis.summary) h += `<div class="row">${esc(analysis.summary)}</div>`;
  // 分类汇总条
  const bc = (summary && summary.by_category) || {};
  const maxv = Math.max(1, ...Object.values(bc));
  const cats = Object.keys(bc).sort((a, b) => bc[b] - bc[a]);
  if (cats.length) {
    h += `<div class="cat-bars">`;
    cats.forEach(cat => {
      const v = bc[cat];
      h += `<div class="cat-bar"><div class="cb-top"><span>${CAT_LABEL[cat] || cat}</span><span>${money(v)} 元</span></div>` +
        `<div class="cb-track"><div class="cb-fill" style="width:${(v / maxv * 100) | 0}%"></div></div></div>`;
    });
    h += `</div>`;
  }
  (summary && summary.warnings || []).forEach(w => h += `<div class="proc-warn">⚠ ${esc(w)}</div>`);
  h += `</section>`;

  // 成本明细表
  h += `<section class="panel-card"><div class="card-h">成本明细</div><table class="cost-table"><thead><tr>` +
    `<th>类别</th><th>分项</th><th>计算依据</th><th>数量</th><th>单位</th><th>单价</th><th>金额(元)</th><th>来源</th><th>置信</th>` +
    `</tr></thead><tbody>`;
  (analysis.items || []).forEach((it, i) => h += itemRow(it, i));
  h += `</tbody></table>`;
  if (editing) h += `<div class="row edit-hint">提示：保存后平台会按 数量×单价 重算金额与合计。</div>`;
  h += `</section>`;

  // 价格依据(联网检索,带可点击链接)
  if ((analysis.price_references || []).length) {
    h += `<section class="panel-card"><div class="card-h">价格依据(联网检索)</div>`;
    analysis.price_references.forEach(r => {
      const link = r.url ? ` · <a href="${esc(r.url)}" target="_blank" rel="noopener">查看来源 ↗</a>` : "";
      h += `<div class="priceref"><span class="pi">${esc(r.item)}</span> — <span class="pp">${esc(r.price)}</span>` +
        `<div class="ps">${esc(r.source || "")}${r.date ? " · " + esc(r.date) : ""}${link}</div></div>`;
    });
    h += `</section>`;
  }

  // 检索来源(平台自动收集的网页证据)
  if ((analysis.search_sources || []).length) {
    h += `<section class="panel-card"><div class="card-h">检索来源(可点击核查)</div>`;
    analysis.search_sources.forEach(s => {
      h += `<div class="srclink">🔗 <a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || s.url)}</a></div>`;
    });
    h += `</section>`;
  }

  // 假设 + 待澄清
  if ((analysis.assumptions || []).length || (analysis.open_questions || []).length) {
    h += `<section class="panel-card"><div class="card-h">估算假设 / 待澄清</div>`;
    (analysis.assumptions || []).forEach(a => h += `<div class="assump">• ${esc(a)}</div>`);
    (analysis.open_questions || []).forEach(q =>
      h += `<div class="proc-q">❓ ${esc(q.field)}: ${esc(q.reason)}${q.guess ? " (猜测: " + esc(q.guess) + ")" : ""}</div>`);
    h += `</section>`;
  }

  $("costArea").innerHTML = h;
}

function itemRow(it, i) {
  const conf = ((it.confidence || 0) * 100 | 0) + "%";
  if (editing) {
    const opts = CATS.map(c => `<option value="${c}" ${c === it.category ? "selected" : ""}>${CAT_LABEL[c]}</option>`).join("");
    return `<tr data-i="${i}">` +
      `<td><select data-f="category" style="font-size:12px">${opts}</select></td>` +
      `<td><input data-f="name" value="${esc(it.name || "")}"/></td>` +
      `<td><input data-f="basis" value="${esc(it.basis || "")}"/></td>` +
      `<td><input class="num" data-f="quantity" type="number" step="any" value="${it.quantity != null ? it.quantity : ""}"/></td>` +
      `<td><input data-f="unit" value="${esc(it.unit || "")}" style="width:48px"/></td>` +
      `<td><input class="num" data-f="unit_price" type="number" step="any" value="${it.unit_price != null ? it.unit_price : ""}"/></td>` +
      `<td><input class="num" data-f="amount" type="number" step="any" value="${it.amount != null ? it.amount : ""}"/></td>` +
      `<td><input data-f="source" value="${esc(it.source || "")}"/></td>` +
      `<td>${conf}</td></tr>`;
  }
  return `<tr>` +
    `<td><span class="cat-tag">${CAT_LABEL[it.category] || it.category}</span></td>` +
    `<td>${esc(it.name)}</td><td class="src">${esc(it.basis || "")}</td>` +
    `<td class="num">${it.quantity != null ? it.quantity : ""}</td><td>${esc(it.unit || "")}</td>` +
    `<td class="num">${it.unit_price != null ? money(it.unit_price) : ""}</td>` +
    `<td class="num amt">${it.amount != null ? money(it.amount) : ""}</td>` +
    `<td class="src">${esc(it.source || "")}</td><td class="num">${conf}</td></tr>`;
}

function collectEdits() {
  document.querySelectorAll(".cost-table tr[data-i]").forEach(el => {
    const it = analysis.items[+el.dataset.i];
    el.querySelectorAll("[data-f]").forEach(inp => {
      const f = inp.dataset.f, v = inp.value;
      if (["quantity", "unit_price", "amount"].includes(f)) it[f] = v.trim() === "" ? null : parseFloat(v);
      else it[f] = v.trim() === "" ? (f === "category" ? "other" : null) : v;
    });
  });
}

init();

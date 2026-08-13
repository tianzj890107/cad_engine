// 工艺拆解页: 读取 ?project=&part=, 生成/展示/编辑该零件的结构化工艺路线。
const API = "";
const authToken = localStorage.getItem("authToken") || "";

// 同 app.js: 给 /api 请求自动带令牌; 401 时回登录页
const _fetch = window.fetch.bind(window);
window.fetch = (url, opts = {}) => {
  const hasAuth = opts.headers && (opts.headers.Authorization || opts.headers.authorization);
  if (typeof url === "string" && url.indexOf("/api/") !== -1 && authToken && !hasAuth) {
    opts = Object.assign({}, opts, {
      headers: Object.assign({}, opts.headers, { Authorization: "Bearer " + authToken }),
    });
  }
  return _fetch(url, opts).then(r => {
    if (r.status === 401) { location.href = "index.html"; }
    return r;
  });
};

const $ = (id) => document.getElementById(id);
const status = (m, busy = false) => { $("status").textContent = (busy ? "⏳ " : "") + m; };
const esc = (s) => String(s ?? "").replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

const TYPE_LABEL = {
  blank: "下料/备料", turning: "车", milling: "铣", drilling: "钻", boring: "镗",
  grinding: "磨", bench: "钳工", sheet_metal: "钣金", welding: "焊接",
  heat_treat: "热处理", surface: "表面处理", assembly: "装配", inspection: "检验", other: "其他",
};
const TYPES = Object.keys(TYPE_LABEL);

const qs = new URLSearchParams(location.search);
const PID = qs.get("project");
const PART = qs.get("part");

let part = null;       // 零件信息(来自 IR)
let plan = null;       // 当前工艺路线
let validation = null;
let editing = false;

$("extraFiles").addEventListener("change", () => {
  const names = [...$("extraFiles").files].map(file => file.name);
  $("extraFilesName").textContent = names.length ? names.join("、") : "未选择文件";
});

async function init() {
  try {
    await fetch(`${API}/api/health`).then(r => r.json());
    $("health").textContent = "";
  } catch { $("health").textContent = "后端未连接"; }

  if (!PID || !PART) { status("缺少 project / part 参数,请从工作台点击零件进入。"); return; }
  // 返回工作台时带上 project/part,让工作台自动重开该项目并选中该零件(不丢内容)
  $("backLink").href = `index.html?project=${encodeURIComponent(PID)}&part=${encodeURIComponent(PART)}`;

  // 取零件信息
  try {
    const data = await fetch(`${API}/api/projects/${PID}`).then(r => r.json());
    part = (data.ir && data.ir.parts || []).find(p => p.part_id === PART);
  } catch { /* ignore */ }
  if (!part) {
    $("partName").textContent = "(未找到该零件)";
    status("未找到零件,请确认已解析。");
    return;
  }
  $("partId").textContent = part.part_id;
  $("partName").textContent = part.name || part.part_id;
  $("partMat").textContent = (part.material ? "材料 " + part.material.spec : "") +
    (part.quantity ? "　数量 ×" + part.quantity : "");

  // 取已存工艺路线
  await loadPlan();
}

async function loadPlan() {
  try {
    const d = await fetch(`${API}/api/projects/${PID}/parts/${PART}/process`).then(r => r.json());
    plan = d.plan; validation = d.validation;
  } catch { plan = null; }
  if (plan) {
    $("btnGen").textContent = "🔄 重新生成";
    $("btnEdit").disabled = false;
    render();
    status("已加载工艺路线。可「编辑」改参后保存,或「重新生成」。");
  } else {
    $("planArea").innerHTML = `<div class="proc-summary"><div class="row">` +
      `尚未生成工艺拆解。点击「⚙️ 生成工艺拆解」,由 AI 依据该零件的特征/材料/尺寸` +
      `编制结构化加工工艺路线。</div></div>`;
    status("尚未生成工艺路线。");
  }
}

function extraForm() {
  const fd = new FormData();
  fd.append("note", $("extraNote").value || "");
  for (const f of $("extraFiles").files) fd.append("attachments", f);
  return fd;
}

$("btnGen").onclick = async () => {
  $("btnGen").disabled = true;
  status("已提交工艺拆解任务，AI 正在编制工艺路线…", true);
  try {
    const sub = await fetch(`${API}/api/projects/${PID}/parts/${PART}/process`,
      { method: "POST", body: extraForm() });
    const sd = await sub.json();
    if (!sub.ok) throw new Error(sd.detail || sub.status);
    const res = await pollTask(sd.task_id);
    plan = res.plan; validation = res.validation;
    editing = false;
    $("btnGen").textContent = "🔄 重新生成";
    $("btnEdit").disabled = false;
    render();
    status(`工艺拆解完成:${validation.step_count} 道工序` +
      (validation.total_duration_min != null ? `,合计工时 ${validation.total_duration_min} 分钟` : ""));
  } catch (e) { status("工艺拆解失败: " + e.message); }
  finally { $("btnGen").disabled = false; }
};

async function pollTask(taskId) {
  while (true) {
    await sleep(1200);
    let t;
    try { t = await fetch(`${API}/api/projects/${PID}/tasks/${taskId}`).then(r => r.json()); }
    catch { continue; }
    if (t.status === "succeeded") return t.result;
    if (t.status === "failed") throw new Error(t.error || "任务失败");
    status(`工艺拆解：${t.progress || "正在处理"}…`, true);
  }
}

$("btnEdit").onclick = () => { editing = true; $("btnEdit").style.display = "none"; $("btnSave").style.display = ""; render(); };

$("btnSave").onclick = async () => {
  collectEdits();
  status("保存工艺路线…", true);
  try {
    const r = await fetch(`${API}/api/projects/${PID}/parts/${PART}/process`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(plan),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.status);
    plan = d.plan; validation = d.validation;
    editing = false; $("btnSave").style.display = "none"; $("btnEdit").style.display = "";
    render();
    status("已保存。");
  } catch (e) { status("保存失败: " + e.message); }
};

function confClass(c) { return c >= 0.75 ? "" : c >= 0.5 ? "mid" : "lo"; }

function render() {
  if (!plan) return;
  let h = "";

  // 三列布局: 左=概览+流程图 / 中=工序明细 / 右=待澄清 (顶部对齐)
  h += `<div class="proc-grid">`;

  // —— 左列: 工艺概览 + 工艺流程图 ——
  h += `<div class="pcol pcol-left">`;
  h += `<section class="panel-card"><div class="card-h">工艺概览</div>`;
  if (plan.blank) h += `<div class="row"><b>毛坯</b> ${esc(plan.blank)}</div>`;
  if (plan.material) h += `<div class="row"><b>材料</b> ${esc(plan.material)}</div>`;
  if (plan.summary) h += `<div class="row"><b>工艺方案</b> ${esc(plan.summary)}</div>`;
  if (plan.overall_note) h += `<div class="row"><b>备注</b> ${esc(plan.overall_note)}</div>`;
  if (validation) {
    h += `<div class="proc-totals">` +
      `<div class="stat"><div class="n">${validation.step_count}</div><div class="l">工序数</div></div>` +
      (validation.total_duration_min != null
        ? `<div class="stat"><div class="n">${validation.total_duration_min}</div><div class="l">合计工时(分钟)</div></div>` : "") +
      `</div>`;
    (validation.warnings || []).forEach(w => h += `<div class="proc-warn">⚠ ${esc(w)}</div>`);
  }
  h += `</section>`;
  h += `<section class="panel-card"><div class="card-h">工艺流程图</div>` +
    `<div class="rail-hint">点节点定位到中间工序</div>` + vflow(plan.steps || []) + `</section>`;
  h += `</div>`;

  // —— 中列: 工序明细 ——
  h += `<div class="pcol pcol-mid"><section class="panel-card"><div class="card-h">工序明细</div>` +
    `<div class="steps">`;
  (plan.steps || []).forEach((s, i) => h += stepCard(s, i));
  h += `</div></section></div>`;

  // —— 右列: 待澄清 ——
  h += `<div class="pcol pcol-right"><section class="panel-card"><div class="card-h">待澄清</div>`;
  const qs2 = plan.open_questions || [];
  if (qs2.length) {
    qs2.forEach(q =>
      h += `<div class="proc-q">❓ ${esc(q.field)}: ${esc(q.reason)}${q.guess ? " (猜测: " + esc(q.guess) + ")" : ""}</div>`);
  } else {
    h += `<div class="rail-hint">暂无待澄清问题</div>`;
  }
  h += `</section></div>`;

  h += `</div>`;

  $("planArea").innerHTML = h;

  // 流程图节点点击 -> 滚动并高亮对应工序卡片
  document.querySelectorAll(".vnode[data-step]").forEach(n => {
    n.onclick = () => {
      const card = document.getElementById("step-" + n.dataset.step);
      if (!card) return;
      card.scrollIntoView({ behavior: "smooth", block: "center" });
      card.classList.add("hl");
      setTimeout(() => card.classList.remove("hl"), 1200);
    };
  });
}

// 工序类型 -> 流程图节点配色分类
function nodeCat(type) {
  if (type === "blank") return "cat-blank";
  if (type === "heat_treat" || type === "surface") return "cat-thermal";
  if (type === "inspection") return "cat-inspect";
  return "cat-machining";
}

// 竖向流程图(节点自上而下,带连接线)
function vflow(steps) {
  if (!steps.length) return `<div class="rail-hint">暂无工序</div>`;
  let h = `<div class="vflow">`;
  steps.forEach(s => {
    const dur = s.duration_min != null ? ` · ${s.duration_min}分` : "";
    h += `<div class="vnode ${nodeCat(s.type)}" data-step="${esc(s.step_no)}" title="${esc(s.name || "")}">` +
      `<div class="vno">${esc(s.step_no)}</div>` +
      `<div class="vbody"><div class="vname">${esc(s.name || "")}</div>` +
      `<div class="vtype">${TYPE_LABEL[s.type] || s.type}${dur}</div></div></div>`;
  });
  h += `</div>`;
  return h;
}

function stepCard(s, i) {
  const cls = confClass(s.confidence || 0);
  if (editing) {
    const opts = TYPES.map(t => `<option value="${t}" ${t === s.type ? "selected" : ""}>${TYPE_LABEL[t]}</option>`).join("");
    return `<div class="step ${cls}" id="step-${esc(s.step_no)}" data-i="${i}"><div class="sno">${esc(s.step_no)}</div><div class="sbody">` +
      `<div class="sedit">` +
      `<label>工序号</label><input data-f="step_no" type="number" value="${s.step_no}"/>` +
      `<label>名称</label><input data-f="name" value="${esc(s.name || "")}"/>` +
      `<label>类型</label><select data-f="type">${opts}</select>` +
      `<label>内容</label><textarea data-f="description" rows="2">${esc(s.description || "")}</textarea>` +
      `<label>设备</label><input data-f="equipment" value="${esc(s.equipment || "")}"/>` +
      `<label>工装</label><input data-f="fixture" value="${esc(s.fixture || "")}"/>` +
      `<label>刀具/量具</label><input data-f="tooling" value="${esc(s.tooling || "")}"/>` +
      `<label>参数</label><input data-f="params" value="${esc(s.params || "")}"/>` +
      `<label>质量要求</label><input data-f="quality" value="${esc(s.quality || "")}"/>` +
      `<label>工时(分)</label><input data-f="duration_min" type="number" step="0.1" value="${s.duration_min != null ? s.duration_min : ""}"/>` +
      `<label>依赖工序号</label><input data-f="depends_on" value="${(s.depends_on || []).join(",")}"/>` +
      `</div></div></div>`;
  }
  const kv = (k, v) => v ? `<div><span class="k">${k}:</span> ${esc(v)}</div>` : "";
  return `<div class="step ${cls}" id="step-${esc(s.step_no)}"><div class="sno">${esc(s.step_no)}</div><div class="sbody">` +
    `<div class="stitle">${esc(s.name)}<span class="stype">${TYPE_LABEL[s.type] || s.type}</span>` +
    `<span class="sconf">置信 ${((s.confidence || 0) * 100 | 0)}%</span></div>` +
    (s.description ? `<div class="sdesc">${esc(s.description)}</div>` : "") +
    `<div class="sgrid">` +
    kv("设备", s.equipment) + kv("工装", s.fixture) + kv("刀具/量具", s.tooling) +
    kv("参数", s.params) + kv("质量", s.quality) +
    (s.duration_min != null ? `<div><span class="k">工时:</span> ${s.duration_min} 分</div>` : "") +
    `</div>` +
    ((s.depends_on || []).length ? `<div class="sdep">前序依赖: 工序 ${s.depends_on.join("、")}</div>` : "") +
    (s.note ? `<div class="sdep">备注: ${esc(s.note)}</div>` : "") +
    `</div></div>`;
}

function collectEdits() {
  document.querySelectorAll(".step[data-i]").forEach(el => {
    const i = +el.dataset.i;
    const s = plan.steps[i];
    el.querySelectorAll("[data-f]").forEach(inp => {
      const f = inp.dataset.f;
      const v = inp.value;
      if (f === "step_no") s.step_no = parseInt(v) || s.step_no;
      else if (f === "duration_min") s.duration_min = v.trim() === "" ? null : parseFloat(v);
      else if (f === "depends_on") s.depends_on = v.split(",").map(x => parseInt(x.trim())).filter(n => !isNaN(n));
      else s[f] = v.trim() === "" ? null : v;
    });
  });
  plan.steps.sort((a, b) => a.step_no - b.step_no);
}

init();

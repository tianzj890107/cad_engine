import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";

const API = "";  // 同源
let authToken = localStorage.getItem("authToken") || "";
let currentUser = null;
let authEnabled = false;

// 给同源 /api 请求自动带上令牌(鉴权开启时)
const _fetch = window.fetch.bind(window);
window.fetch = (url, opts = {}) => {
  const hasAuth = opts.headers && (opts.headers.Authorization || opts.headers.authorization);
  if (typeof url === "string" && url.indexOf("/api/") !== -1 && authToken && !hasAuth) {
    opts = Object.assign({}, opts, {
      headers: Object.assign({}, opts.headers, { Authorization: "Bearer " + authToken }),
    });
  }
  return _fetch(url, opts);
};
// 媒体 URL(<img>/STL 无法带请求头)用 ?token= 透传
const mediaUrl = (u) =>
  authToken ? u + (u.indexOf("?") >= 0 ? "&" : "?") + "token=" + encodeURIComponent(authToken) : u;

let currentProject = null;
let currentIR = null;
let currentGeometry = null;
let currentDrawings = null;
let currentIsImg = true;       // 是否为"图→IR"项目(3D 导入项目不可改参重生)
let currentSelectedId = null;  // 当前选中的零件 id
let diffPick = [];             // 版本对比已选的两个版本号

const $ = (id) => document.getElementById(id);
const status = (msg, busy = false) => { $("status").textContent = (busy ? "⏳ " : "") + msg; };

// --------------------------------------------------------------------------- //
// 健康检查 + 历史项目
// --------------------------------------------------------------------------- //
const ROLE_LABEL_CN = { viewer: "只读", engineer: "工程师", reviewer: "校核/审签", admin: "管理员" };

async function init() {
  initViewer();
  let h;
  try { h = await fetch(`${API}/api/health`).then(r => r.json()); }
  catch { $("health").textContent = "后端未连接"; return; }
  $("health").textContent =
    `CAD内核 ${h.cadquery_available ? "✓" : "✗(未装cadquery)"}`;
  authEnabled = !!h.auth_enabled;
  if (authEnabled && !(await refreshMe())) { $("loginOverlay").style.display = "flex"; return; }
  afterAuth();
}

async function refreshMe() {
  if (!authToken) return false;
  try {
    const r = await fetch(`${API}/api/me`);
    if (!r.ok) return false;
    currentUser = (await r.json()).user;
    return true;
  } catch { return false; }
}

function afterAuth() {
  renderUserBox();
  loadProjects();
  // 深链/恢复: 从 URL ?project=&part= 或上次打开的项目自动重开,避免从工艺页返回后丢内容
  const q = new URLSearchParams(location.search);
  const pid = q.get("project") || localStorage.getItem("lastProject");
  if (!pid) return;
  openProject(pid).then(() => {
    const partId = q.get("part");
    if (!partId) return;
    const p = (currentIR && currentIR.parts || []).find(x => x.part_id === partId);
    if (p) selectPart(p);
  }).catch(() => { localStorage.removeItem("lastProject"); });
}

function renderUserBox() {
  if (!authEnabled) { $("userBox").style.display = "none"; return; }
  $("userBox").style.display = "flex";
  $("userInfo").textContent =
    `${currentUser.display_name}（${ROLE_LABEL_CN[currentUser.role] || currentUser.role}）`;
  $("btnAddUser").style.display = currentUser.role === "admin" ? "" : "none";
}

$("loginForm").onsubmit = async (e) => {
  e.preventDefault();
  $("loginErr").textContent = "";
  const username = $("loginUser").value.trim();
  const password = $("loginPass").value;
  try {
    const r = await fetch(`${API}/api/login`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const d = await r.json();
    if (!r.ok) { $("loginErr").textContent = d.detail || "登录失败"; return; }
    authToken = d.token; currentUser = d.user;
    localStorage.setItem("authToken", authToken);
    $("loginOverlay").style.display = "none";
    afterAuth();
  } catch { $("loginErr").textContent = "网络错误"; }
};

$("btnLogout").onclick = () => {
  authToken = ""; currentUser = null;
  localStorage.removeItem("authToken");
  location.reload();
};

$("btnAddUser").onclick = async () => {
  const username = prompt("新用户 用户名:"); if (!username) return;
  const password = prompt("初始密码:"); if (!password) return;
  const role = prompt("角色 (viewer / engineer / reviewer / admin):", "engineer"); if (!role) return;
  const display_name = prompt("显示名(可选):", username) || username;
  const r = await fetch(`${API}/api/users`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, role, display_name }),
  });
  const d = await r.json();
  status(r.ok ? `已创建用户 ${username}（${ROLE_LABEL_CN[role] || role}）` : "创建失败: " + (d.detail || r.status));
};

async function loadProjects() {
  const list = await fetch(`${API}/api/projects`).then(r => r.json());
  $("projectList").innerHTML = "";
  list.forEach(p => {
    const li = document.createElement("li");
    li.textContent = `${p.device_name || "(未解析)"} · ${p.created_at}`;
    li.onclick = () => openProject(p.project_id);
    $("projectList").appendChild(li);
  });
}

// --------------------------------------------------------------------------- //
// 流程: 上传 -> 解析 -> 拆解 -> 生成
// --------------------------------------------------------------------------- //
$("btnUpload").onclick = async () => {
  const f = $("fileInput").files[0];
  if (!f) { status("请先选择图片"); return; }
  status("上传中...", true);
  const fd = new FormData();
  fd.append("file", f);
  fd.append("note", $("noteInput").value || "");
  for (const a of $("attachInput").files) fd.append("attachments", a);
  const res = await fetch(`${API}/api/projects`, { method: "POST", body: fd }).then(r => r.json());
  currentProject = res.project_id;
  currentIR = null; currentGeometry = null; currentDrawings = null;
  currentIsImg = true; currentSelectedId = null;
  diffPick = []; $("versions").innerHTML = ""; $("diffView").innerHTML = "";
  const phEl = document.querySelector(".image-wrap .placeholder");
  if (phEl) phEl.remove();
  $("sourceImg").style.display = "";
  $("sourceImg").src = mediaUrl(`${API}/api/projects/${currentProject}/source?t=${Date.now()}`);
  $("btnParse").disabled = false;
  $("btnVerify").disabled = true;
  $("btnDecompose").disabled = true;
  $("btnGenerate").disabled = true;
  $("btnDrawings").disabled = true;
  $("btnBom").disabled = true;
  const nAtt = $("attachInput").files.length;
  status(`已创建项目 ${currentProject}（补充说明${$("noteInput").value ? "✓" : "—"}，佐证文件 ${nAtt} 个），可解析`);
  loadProjects();
};

$("btnParse").onclick = async () => {
  if (!currentProject) return;
  status("Claude 正在解析图纸为结构化 IR（含视觉理解，稍候）...", true);
  try {
    currentIR = await runTask(currentProject, `/api/projects/${currentProject}/parse`, "解析");
    renderIR(currentIR);
    $("btnVerify").disabled = false;
    $("btnDecompose").disabled = false;
    $("btnGenerate").disabled = false;
    $("btnDrawings").disabled = false;
    $("btnBom").disabled = false;
    status(`解析完成（平均置信度 ${avgConfidence(currentIR)}）`);
  } catch (e) { status("解析失败: " + e.message); }
};

$("btnVerify").onclick = async () => {
  if (!currentProject) return;
  const before = avgConfidence(currentIR);
  status("Claude 正在对照原图自校验、修正尺寸并重估置信度...", true);
  try {
    currentIR = await runTask(currentProject, `/api/projects/${currentProject}/verify`, "校验");
    renderIR(currentIR);
    status(`校验完成（平均置信度 ${before} → ${avgConfidence(currentIR)}）`);
  } catch (e) { status("自校验失败: " + e.message); }
};

$("btnDecompose").onclick = async () => {
  if (!currentProject) return;
  status("Claude 正在做拆解推荐增强...", true);
  try {
    currentIR = await runTask(currentProject, `/api/projects/${currentProject}/decompose`, "拆解推荐");
    renderIR(currentIR);
    status("拆解推荐完成");
  } catch (e) { status("拆解失败: " + e.message); }
};

$("btnGenerate").onclick = async () => {
  if (!currentProject) return;
  status("CAD 内核正在生成几何(STEP/STL)并校验...", true);
  try {
    currentGeometry = await runTask(currentProject, `/api/projects/${currentProject}/generate`, "几何生成");
    renderIR(currentIR);  // 重渲染以挂上几何状态
    status("几何生成完成，点击零件查看 3D");
  } catch (e) { status("几何生成失败: " + e.message); }
};

$("btnDrawings").onclick = async () => {
  if (!currentProject) return;
  status("CAD 内核正在投影 2D 工程图(三视图 SVG + 下料 DXF)...", true);
  try {
    currentDrawings = await runTask(currentProject, `/api/projects/${currentProject}/drawings`, "2D 工程图");
    renderIR(currentIR);
    const ok = currentDrawings.parts.filter(p => p.ok).length;
    status(`2D 工程图完成(${ok}/${currentDrawings.parts.length} 件)，点击零件查看`);
  } catch (e) { status("2D 工程图生成失败: " + e.message); }
};

$("btnBom").onclick = () => {
  if (!currentProject) return;
  window.open(mediaUrl(`${API}/api/projects/${currentProject}/bom.csv`), "_blank");
};

$("btnImport3d").onclick = async () => {
  const f = $("file3d").files[0];
  if (!f) { status("请选择 STEP/STP 文件"); return; }
  status("OCCT 正在解析 3D 模型并生成结构树/几何/2D 工程图...", true);
  const fd = new FormData();
  fd.append("file", f);
  try {
    const r = await fetch(`${API}/api/projects/3d`, { method: "POST", body: fd });
    const res = await r.json();
    if (!r.ok) throw new Error(res.detail || r.status);
    const out = await pollTask(res.project_id, res.task_id, "3D 解析");
    await openProject(res.project_id);
    loadProjects();
    status(`3D 导入完成：解析出 ${out.parts} 个零件，已生成 3D/2D/结构树/BOM`);
  } catch (e) { status("3D 导入失败: " + e.message); }
};

async function postJSON(path) {
  const r = await fetch(`${API}${path}`, { method: "POST" });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
  return r.json();
}

// --------------------------------------------------------------------------- //
// 异步任务: 提交 -> 轮询状态/进度 -> 取结果(替代原来的阻塞式调用)
// --------------------------------------------------------------------------- //
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function pollTask(projectId, taskId, label) {
  while (true) {
    await sleep(1200);
    let t;
    try { t = await fetch(`${API}/api/projects/${projectId}/tasks/${taskId}`).then(r => r.json()); }
    catch { continue; }  // 网络抖动则继续轮询
    if (t.status === "succeeded") return t.result;
    if (t.status === "failed") throw new Error(t.error || "任务失败");
    status(`${label}: ${t.progress || "处理中"}…`, true);
  }
}

async function runTask(projectId, submitPath, label) {
  const r = await fetch(`${API}${submitPath}`, { method: "POST" });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || r.status);
  status(`${label}已提交(任务 ${String(d.task_id).slice(0, 6)})，处理中…`, true);
  return pollTask(projectId, d.task_id, label);
}

// --------------------------------------------------------------------------- //
// 打开历史项目
// --------------------------------------------------------------------------- //
async function openProject(pid) {
  currentProject = pid;
  currentSelectedId = null;
  diffPick = []; $("diffView").innerHTML = "";
  const data = await fetch(`${API}/api/projects/${pid}`).then(r => r.json());
  if (!data || !data.meta) {  // 项目不存在(可能已删)
    localStorage.removeItem("lastProject");
    throw new Error("项目不存在");
  }
  localStorage.setItem("lastProject", pid);  // 记住,供刷新/返回时恢复
  currentIR = data.ir;
  currentGeometry = data.geometry;
  currentDrawings = data.drawings;

  // 原图区: 图片项目显示原图; 3D 导入项目无 2D 原图,显示占位
  const fname = (data.meta && data.meta.source_filename) || "";
  const isImg = /\.(png|jpe?g|webp|gif|bmp)$/i.test(fname);
  currentIsImg = isImg;
  const img = $("sourceImg");
  const wrap = document.querySelector(".image-wrap");
  const ph = wrap.querySelector(".placeholder");
  if (isImg) {
    img.style.display = "";
    img.src = mediaUrl(`${API}/api/projects/${pid}/source?t=${Date.now()}`);
    if (ph) ph.remove();
  } else {
    img.style.display = "none";
    if (!ph) {
      const d = document.createElement("div");
      d.className = "placeholder";
      d.textContent = `3D 模型导入项目（${fname}）：无 2D 原图，请查看结构树与 3D/2D 视图。`;
      wrap.appendChild(d);
    }
  }

  // 3D 导入项目: 几何/2D 已由原始实体生成,禁用"基于图/特征重建"的按钮,避免覆盖精确几何
  $("btnParse").disabled = !isImg;
  $("btnVerify").disabled = !isImg || !data.ir;
  $("btnDecompose").disabled = !data.ir;
  $("btnGenerate").disabled = !isImg || !data.ir;
  $("btnDrawings").disabled = !isImg || !data.ir;
  $("btnBom").disabled = !data.ir;
  if (data.ir) renderIR(data.ir);
  loadVersions();
  const note = data.meta && data.meta.note ? data.meta.note : "";
  const atts = data.meta && data.meta.attachments ? data.meta.attachments.length : 0;
  status(`已打开项目 ${pid}（补充说明${note ? "✓" : "—"}，佐证文件 ${atts} 个）`);
}

function avgConfidence(ir) {
  const ps = (ir && ir.parts) || [];
  if (!ps.length) return "—";
  const avg = ps.reduce((s, p) => s + (p.confidence || 0), 0) / ps.length;
  return (avg * 100 | 0) + "%";
}

// --------------------------------------------------------------------------- //
// 渲染拆解树 + 标准件 + 待澄清
// --------------------------------------------------------------------------- //
function geomFor(partId) {
  if (!currentGeometry) return null;
  return currentGeometry.parts.find(p => p.part_id === partId);
}

function drawingsFor(partId) {
  if (!currentDrawings) return null;
  return currentDrawings.parts.find(p => p.part_id === partId);
}

function confClass(c) { return c >= 0.75 ? "hi" : c >= 0.5 ? "mid" : "lo"; }

// 由 ir.assemblies + parts.parent_id 构建层级树(含环路保护)
function buildClientTree(ir) {
  const asms = ir.assemblies || [];
  const byId = {};
  asms.forEach(a => { byId[a.assembly_id] = a; });
  const nodes = {};
  asms.forEach(a => {
    nodes[a.assembly_id] = {
      type: "assembly", id: a.assembly_id, name: a.name,
      role: a.role, quantity: a.quantity, children: [],
    };
  });
  const root = { type: "equipment", name: ir.device_name || "设备", children: [] };
  const safeParent = (id, pid) => {
    if (!pid || pid === id || !byId[pid]) return null;
    let cur = pid, chain = new Set([id]);
    while (cur) { if (chain.has(cur)) return null; chain.add(cur); cur = byId[cur] ? byId[cur].parent_id : null; }
    return pid;
  };
  asms.forEach(a => {
    const pid = safeParent(a.assembly_id, a.parent_id);
    (pid ? nodes[pid].children : root.children).push(nodes[a.assembly_id]);
  });
  (ir.parts || []).forEach(p => {
    const pid = (p.parent_id && byId[p.parent_id]) ? p.parent_id : null;
    (pid ? nodes[pid].children : root.children).push({ type: "part", id: p.part_id });
  });
  return root;
}

function renderNode(node, container, depth, partById) {
  const pad = 6 + depth * 14;
  if (node.type === "assembly") {
    const div = document.createElement("div");
    div.className = "asm";
    div.style.paddingLeft = pad + "px";
    div.innerHTML = `<span class="asm-id">▸ ${esc(node.id)}</span> ${esc(node.name)}` +
      `<span class="asm-meta">${node.role ? esc(node.role) + " · " : ""}总成 ×${node.quantity || 1}</span>`;
    container.appendChild(div);
    (node.children || []).forEach(c => renderNode(c, container, depth + 1, partById));
    return;
  }
  // part
  const p = partById[node.id];
  if (!p) return;
  const g = geomFor(p.part_id);
  const dw = drawingsFor(p.part_id);
  const div = document.createElement("div");
  div.className = "part";
  div.dataset.partId = p.part_id;
  div.style.marginLeft = pad + "px";
  const feats = (p.features || []).map(f => f.type).join(", ");
  const gstat = g ? (g.ok ? " · 几何✓" : " · 几何✗") : "";
  const dstat = dw ? (dw.ok ? " · 2D✓" : " · 2D✗") : "";
  div.innerHTML =
    `<div class="pname">${esc(p.part_id)} ${esc(p.name)}` +
    `<span class="conf ${confClass(p.confidence)}">置信 ${(p.confidence * 100 | 0)}%</span></div>` +
    `<div class="pmeta">${esc(p.role || "")} · 数量×${p.quantity}` +
    `${p.material ? " · " + esc(p.material.spec) : ""}${gstat}${dstat}</div>` +
    `<div class="feats">特征: ${esc(feats)}</div>` +
    (p.recommendation ? `<div class="recommend">💡 ${esc(p.recommendation)}</div>` : "");
  div.onclick = () => selectPart(p);
  container.appendChild(div);
}

// 在原图上叠加各零件的 bbox(provenance.bbox 归一化 [x,y,w,h]),按置信度着色
function renderBboxes(ir) {
  const layer = $("bboxLayer");
  if (!layer) return;
  layer.innerHTML = "";
  if (!currentIsImg) return;
  (ir.parts || []).forEach(p => {
    const bb = p.provenance && p.provenance.bbox;
    if (!bb || bb.length < 4) return;
    const [x, y, w, h] = bb;
    const box = document.createElement("div");
    box.className = "bbox " + confClass(p.confidence);
    box.dataset.partId = p.part_id;
    box.style.left = (x * 100) + "%";
    box.style.top = (y * 100) + "%";
    box.style.width = (w * 100) + "%";
    box.style.height = (h * 100) + "%";
    box.innerHTML = `<span class="tag">${esc(p.part_id)} ${(p.confidence * 100 | 0)}%</span>`;
    box.onclick = () => selectPart(p);
    if (p.part_id === currentSelectedId) box.classList.add("active");
    layer.appendChild(box);
  });
}

function renderIR(ir) {
  $("deviceName").textContent = ir.device_name ? `· ${ir.device_name}` : "";
  $("intent").innerHTML =
    `<b>设计意图:</b> ${esc(ir.design_intent || "")}<br>` +
    (ir.overall_dims ? `<b>总体尺寸:</b> ${esc(ir.overall_dims)}<br>` : "") +
    (ir.assembly_notes ? `<b>装配:</b> ${esc(ir.assembly_notes)}` : "");

  const tree = $("tree");
  tree.innerHTML = "";
  const partById = {};
  (ir.parts || []).forEach(p => { partById[p.part_id] = p; });
  const root = buildClientTree(ir);
  root.children.forEach(c => renderNode(c, tree, 0, partById));

  // 标准件 + 待澄清
  const ex = $("extras");
  ex.innerHTML = "";
  (ir.standard_parts || []).forEach(s => {
    const t = document.createElement("span");
    t.className = "tag";
    t.textContent = `🔩 ${s.spec} ×${s.quantity}`;
    ex.appendChild(t);
  });
  (ir.open_questions || []).forEach(q => {
    const d = document.createElement("div");
    d.className = "q";
    d.textContent = `❓ ${q.field}: ${q.reason}` + (q.guess ? ` (猜测: ${q.guess})` : "");
    ex.appendChild(d);
  });

  renderBboxes(ir);
  // 重渲染后保持选中态(高亮 tree/box)
  if (currentSelectedId) {
    const sel = (ir.parts || []).find(p => p.part_id === currentSelectedId);
    if (sel) markSelection(currentSelectedId);
  }
  loadVersions();
}

// --------------------------------------------------------------------------- //
// 版本与校核审签(PRD 6.5): 每次保存 IR 自动留版,可对比/送审/通过/驳回/恢复
// --------------------------------------------------------------------------- //
const STATUS_LABEL = { draft: "草稿", in_review: "送审中", approved: "已通过", rejected: "已驳回" };

async function loadVersions() {
  if (!currentProject) { $("versions").innerHTML = ""; return; }
  let data;
  try {
    data = await fetch(`${API}/api/projects/${currentProject}/versions`).then(r => r.json());
  } catch { return; }
  renderVersions(data.versions || []);
}

function renderVersions(versions) {
  const box = $("versions");
  box.innerHTML = "";
  if (!versions.length) {
    box.innerHTML = `<div class="none">暂无版本(解析/编辑后自动留版)</div>`;
    return;
  }
  versions.slice().reverse().forEach(v => {  // 最新在上
    const div = document.createElement("div");
    div.className = "ver" + (diffPick.includes(v.version) ? " active" : "");
    const picked = diffPick.includes(v.version);
    const conf = v.avg_confidence != null ? ` · 置信${(v.avg_confidence * 100 | 0)}%` : "";
    const last = (v.review || []).slice(-1)[0];
    const reviewNote = last
      ? `<div class="vmeta">审签: ${esc(last.actor)} · ${STATUS_LABEL[last.status] || last.status}` +
        `${last.comment ? " · " + esc(last.comment) : ""}</div>`
      : "";
    div.innerHTML =
      `<div class="ver-top"><span class="vn">v${v.version}</span>` +
      `<span class="st ${v.status}">${STATUS_LABEL[v.status] || v.status}</span>` +
      `<span class="vstage">${esc(v.stage)}</span></div>` +
      `<div class="vmeta">${esc(v.ts)} · ${esc(v.author)} · ${v.parts}零件${conf}</div>` +
      reviewNote +
      `<div class="vacts">` +
      `<button class="pick ${picked ? "on" : ""}" data-act="pick" data-v="${v.version}">` +
        `${picked ? "✓对比" : "选作对比"}</button>` +
      (v.status === "draft" || v.status === "rejected"
        ? `<button data-act="submit" data-v="${v.version}">送审</button>` : "") +
      (v.status === "in_review"
        ? `<button class="ok" data-act="approve" data-v="${v.version}">通过</button>` +
          `<button class="no" data-act="reject" data-v="${v.version}">驳回</button>` : "") +
      `<button class="rs" data-act="restore" data-v="${v.version}">恢复</button>` +
      `</div>`;
    box.appendChild(div);
  });
  box.querySelectorAll("button[data-act]").forEach(b => {
    b.onclick = () => versionAction(b.dataset.act, +b.dataset.v);
  });
}

async function versionAction(act, v) {
  if (act === "pick") return togglePick(v);
  if (act === "restore") {
    if (!confirm(`恢复 v${v} 为当前 IR?(会另存为新版本,不覆盖历史)`)) return;
    status(`正在恢复 v${v} ...`, true);
    const r = await fetch(`${API}/api/projects/${currentProject}/versions/${v}/restore`, { method: "POST" });
    const ir = await r.json();
    if (!r.ok) { status("恢复失败: " + (ir.detail || r.status)); return; }
    currentIR = ir; renderIR(ir);
    status(`已恢复 v${v}(已另存为新版本)`);
    return;
  }
  let actor = "", comment = "";
  if (act === "approve" || act === "reject") {
    actor = prompt("审签人 姓名/工号:", "") || "";
    comment = prompt("审签意见(可选):", "") || "";
  }
  const r = await fetch(`${API}/api/projects/${currentProject}/versions/${v}/${act}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ actor, comment }),
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    status("操作失败: " + (e.detail || r.status));
    return;
  }
  status(`v${v} ${{ submit: "已送审", approve: "已通过", reject: "已驳回" }[act]}`);
  loadVersions();
}

function togglePick(v) {
  const i = diffPick.indexOf(v);
  if (i >= 0) diffPick.splice(i, 1);
  else { diffPick.push(v); if (diffPick.length > 2) diffPick.shift(); }
  loadVersions();
  if (diffPick.length === 2) showDiff(diffPick[0], diffPick[1]);
  else $("diffView").innerHTML = diffPick.length === 1
    ? `<div class="none">已选 v${diffPick[0]}，再选一个版本进行对比</div>` : "";
}

async function showDiff(a, b) {
  const [lo, hi] = a < b ? [a, b] : [b, a];
  try {
    const r = await fetch(`${API}/api/projects/${currentProject}/versions/${lo}/diff/${hi}`).then(r => r.json());
    renderDiff(r.from, r.to, r.diff);
  } catch (e) { $("diffView").innerHTML = `<div class="none">对比失败</div>`; }
}

function renderDiff(from, to, d) {
  const el = $("diffView");
  let html = `<h4>v${from} → v${to} 差异(共 ${d.total_changes} 处)</h4>`;
  if (!d.total_changes) { el.innerHTML = html + `<div class="none">两版无差异</div>`; return; }
  (d.header || []).forEach(c => {
    html += `<div class="chg"><span class="f">${esc(c.field)}</span>: ` +
      `<span class="o">${esc(c.old)}</span> → <span class="n">${esc(c.new)}</span></div>`;
  });
  const P = d.parts || {};
  (P.added || []).forEach(p =>
    html += `<div class="chg"><span class="add">+ 新增 ${esc(p.part_id)} ${esc(p.name || "")}</span></div>`);
  (P.removed || []).forEach(p =>
    html += `<div class="chg"><span class="del">− 删除 ${esc(p.part_id)} ${esc(p.name || "")}</span></div>`);
  (P.modified || []).forEach(m => {
    html += `<div class="chg"><span class="f">${esc(m.part_id)} ${esc(m.name || "")}</span>`;
    m.changes.forEach(c => {
      html += `<div>· ${esc(c.field)}: <span class="o">${esc(c.old)}</span> → ` +
        `<span class="n">${esc(c.new)}</span></div>`;
    });
    html += `</div>`;
  });
  el.innerHTML = html;
}

// --------------------------------------------------------------------------- //
// 选中零件: 详情 + 3D
// --------------------------------------------------------------------------- //
function markSelection(partId) {
  document.querySelectorAll(".tree .part").forEach(d =>
    d.classList.toggle("active", d.dataset.partId === partId));
  document.querySelectorAll("#bboxLayer .bbox").forEach(b =>
    b.classList.toggle("active", b.dataset.partId === partId));
}

// 可编辑的数值字段(按特征类型)
const FEAT_FIELDS = {
  plate: ["length", "width", "thickness"],
  box: ["length", "width", "height"],
  cylinder: ["diameter", "height"],
  hole: ["diameter", "x", "y"],
  hole_pattern: ["diameter", "count_x", "count_y", "spacing_x", "spacing_y"],
  fillet: ["radius"],
  chamfer: ["distance"],
};

function selectPart(part) {
  if (!part) return;
  currentSelectedId = part.part_id;
  markSelection(part.part_id);
  const g = geomFor(part.part_id);
  $("viewerPartName").textContent = `· ${part.part_id} ${part.name}`;

  // 跳转到工艺拆解 / 成本分析页
  const pq = `project=${encodeURIComponent(currentProject)}&part=${encodeURIComponent(part.part_id)}`;
  let html = `<div class="part-nav">` +
    `<a class="btn-process" href="process.html?${pq}">🔧 工艺拆解 →</a>` +
    `<a class="btn-cost" href="cost.html?${pq}">💰 成本分析 →</a></div>`;

  // 行内可编辑特征(仅"图→IR"项目;3D 导入项目几何来自真实实体,不改参重生)
  if (currentIsImg) {
    html += `<div class="edit-grid">` +
      `<label>名称</label><input data-pf="name" value="${esc(part.name || "")}"/>` +
      `<label>数量</label><input data-pf="quantity" type="number" value="${part.quantity || 1}"/>` +
      `<label>材料</label><input data-pf="material" value="${esc(part.material ? part.material.spec : "")}"/>` +
      `</div>`;
    (part.features || []).forEach((f, fi) => {
      const fields = FEAT_FIELDS[f.type] || [];
      html += `<div class="feat-edit"><div class="feat-type">特征 #${fi + 1}: ${f.type}` +
        `${f.purpose ? " · " + esc(f.purpose) : ""}</div><div class="edit-grid">`;
      fields.forEach(k => {
        const v = f[k];
        html += `<label>${k}</label><input data-fi="${fi}" data-fk="${k}" type="number" ` +
          `value="${v != null ? v : ""}"/>`;
      });
      html += `</div></div>`;
    });
    html += `<button class="btn-regen" id="btnRegen">💾 保存并重生该零件</button>`;
  } else {
    html += "<table>";
    (part.features || []).forEach(f => {
      const dims = Object.entries(f).filter(([k, v]) =>
        v != null && !["type", "purpose"].includes(k)).map(([k, v]) => `${k}=${v}`).join(", ");
      html += `<tr><td>${f.type}</td><td>${dims}${f.purpose ? " · " + esc(f.purpose) : ""}</td></tr>`;
    });
    html += "</table>";
  }

  if (g) {
    if (g.bbox) html += `<div>包围盒: ${g.bbox.join(" × ")} mm</div>`;
    if (g.volume_mm3) html += `<div>体积: ${g.volume_mm3} mm³</div>`;
    if (g.mass_g) html += `<div>质量: ${g.mass_g} g</div>`;
    (g.warnings || []).forEach(w => html += `<div class="warn">⚠ ${esc(w)}</div>`);
    if (g.error) html += `<div class="err">✗ ${esc(g.error)}</div>`;
    if (g.ok) {
      html += `<div class="dl"><a href="${mediaUrl(API + g.step_url)}" download>下载 STEP</a>` +
              `<a href="${mediaUrl(API + g.stl_url)}" download>下载 STL</a></div>`;
      loadSTL(mediaUrl(`${API}${g.stl_url}`));
    }
  } else {
    html += `<div class="warn">尚未生成几何，请点击「5·生成几何」</div>`;
    clearViewer();
  }

  // 2D 工程图(三视图 + 等轴测 SVG + 下料 DXF)
  const dw = drawingsFor(part.part_id);
  if (dw && dw.ok) {
    const labels = { front: "主视图", top: "俯视图", right: "侧视图", iso: "等轴测" };
    html += `<h4 class="dwh">2D 工程图</h4><div class="views">`;
    ["front", "top", "right", "iso"].forEach(v => {
      if (dw.views[v]) {
        html += `<figure class="view"><img src="${mediaUrl(API + dw.views[v])}" alt="${v}"/>` +
                `<figcaption>${labels[v]}</figcaption></figure>`;
      }
    });
    html += `</div>`;
    if (dw.dxf_url) html += `<div class="dl"><a href="${mediaUrl(API + dw.dxf_url)}" download>下载 DXF(下料图)</a></div>`;
    (dw.warnings || []).forEach(w => html += `<div class="warn">⚠ ${esc(w)}</div>`);
  }

  $("partDetail").innerHTML = html;
  const rb = document.getElementById("btnRegen");
  if (rb) rb.onclick = () => savePartEdits(part.part_id);
}

// 读取行内编辑 -> 更新 IR -> 保存 -> 单零件重生 -> 刷新
async function savePartEdits(partId) {
  if (!currentProject || !currentIR) return;
  const part = (currentIR.parts || []).find(p => p.part_id === partId);
  if (!part) return;
  const detail = $("partDetail");

  // 零件级字段
  detail.querySelectorAll("[data-pf]").forEach(inp => {
    const k = inp.dataset.pf;
    if (k === "name") part.name = inp.value;
    else if (k === "quantity") part.quantity = parseInt(inp.value) || 1;
    else if (k === "material") {
      const spec = inp.value.trim();
      part.material = spec ? { spec, density: part.material ? part.material.density : null } : null;
    }
  });
  // 特征数值
  detail.querySelectorAll("[data-fi]").forEach(inp => {
    const fi = +inp.dataset.fi, fk = inp.dataset.fk;
    if (!part.features[fi]) return;
    const raw = inp.value.trim();
    part.features[fi][fk] = raw === "" ? null : (Number.isInteger(+raw) && fk.startsWith("count") ? parseInt(raw) : parseFloat(raw));
  });

  status(`正在保存并重生 ${partId} ...`, true);
  try {
    await fetch(`${API}/api/projects/${currentProject}/ir`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(currentIR),
    }).then(r => { if (!r.ok) throw new Error("保存IR失败"); });

    const res = await fetch(`${API}/api/projects/${currentProject}/parts/${partId}/regenerate`,
      { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.status);

    // 更新本地几何/2D 结果中的该零件条目
    upsertLocal(currentGeometry, data.geometry);
    upsertLocal(currentDrawings, data.drawings);
    renderIR(currentIR);
    selectPart(part);
    const g = data.geometry;
    status(g.ok ? `${partId} 已重生(体积 ${g.volume_mm3} mm³)` : `${partId} 重生失败: ${g.error || ""}`);
  } catch (e) { status("重生失败: " + e.message); }
}

function upsertLocal(coll, entry) {
  if (!coll || !entry) return;
  coll.parts = coll.parts || [];
  const i = coll.parts.findIndex(p => p.part_id === entry.part_id);
  if (i >= 0) coll.parts[i] = entry; else coll.parts.push(entry);
}

// --------------------------------------------------------------------------- //
// three.js STL 查看器
// --------------------------------------------------------------------------- //
let scene, camera, renderer, controls, mesh;

function initViewer() {
  const el = $("viewer");
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0b0f14);
  camera = new THREE.PerspectiveCamera(45, el.clientWidth / el.clientHeight, 0.1, 100000);
  camera.position.set(120, 120, 120);
  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(el.clientWidth, el.clientHeight);
  el.appendChild(renderer.domElement);
  controls = new OrbitControls(camera, renderer.domElement);

  scene.add(new THREE.AmbientLight(0xffffff, 0.6));
  const dir = new THREE.DirectionalLight(0xffffff, 0.8);
  dir.position.set(1, 1, 1);
  scene.add(dir);
  scene.add(new THREE.AxesHelper(50));

  (function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  })();

  window.addEventListener("resize", () => {
    camera.aspect = el.clientWidth / el.clientHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(el.clientWidth, el.clientHeight);
  });
}

function clearViewer() {
  if (mesh) { scene.remove(mesh); mesh.geometry.dispose(); mesh = null; }
}

function loadSTL(url) {
  clearViewer();
  new STLLoader().load(url, (geo) => {
    geo.computeVertexNormals();
    geo.center();
    const mat = new THREE.MeshStandardMaterial({ color: 0x6fa8dc, metalness: 0.3, roughness: 0.6 });
    mesh = new THREE.Mesh(geo, mat);
    scene.add(mesh);
    // 自动取景
    geo.computeBoundingSphere();
    const r = geo.boundingSphere.radius || 50;
    camera.position.set(r * 2, r * 2, r * 2);
    controls.target.set(0, 0, 0);
    controls.update();
  });
}

function esc(s) {
  return String(s ?? "").replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

init();

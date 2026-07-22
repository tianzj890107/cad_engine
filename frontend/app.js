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
const chatSessions = new Map(); // 项目级会话仅保存在当前浏览器内，不写入业务数据
let chatBusy = false;

const $ = (id) => document.getElementById(id);
const status = (msg, busy = false) => {
  $("status").textContent = (busy ? "处理中 · " : "") + msg;
  const card = $("statusCard");
  if (card) card.dataset.busy = String(busy);
};

function fileTypeLabel(name) {
  const suffix = String(name || "").split(".").pop().toUpperCase();
  return suffix && suffix !== String(name || "").toUpperCase() ? suffix : "文件";
}

function renderProjectEvidence(meta = {}) {
  const root = $("projectEvidence");
  if (!root) return;
  const sourceName = meta.source_filename || "需求原图";
  const attachments = Array.isArray(meta.attachments) ? meta.attachments : [];
  const projectId = currentProject;
  const sourceMeta = attachments.length
    ? `原图 + ${attachments.length} 份技术资料将共同参与 AI 解析`
    : "原图将作为 AI 图纸解析依据";
  let html = `<div class="uploaded-file-item blueprint-preview"><span class="evidence-icon">▧</span><div class="file-info"><div class="file-name">${esc(sourceName)}</div><div class="file-meta">${sourceMeta}</div></div><span class="evidence-type">图纸</span></div>`;
  if (attachments.length) {
    html += `<div class="evidence-caption">已带入的技术文档与补充资料</div><div class="evidence-list">`;
    html += attachments.map(name => {
      const href = projectId
        ? mediaUrl(`${API}/api/projects/${encodeURIComponent(projectId)}/attachments/${encodeURIComponent(name)}`)
        : "#";
      return `<a class="uploaded-file-item evidence-file" href="${href}" target="_blank" rel="noopener"><span class="evidence-icon">▤</span><div class="file-info"><div class="file-name">${esc(name)}</div><div class="file-meta">将作为图纸解析的需求与约束依据</div></div><span class="evidence-type">${esc(fileTypeLabel(name))}</span></a>`;
    }).join("");
    html += "</div>";
  }
  root.innerHTML = html;
}

function safeExternalUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

function renderModelLookup(report) {
  const root = $("modelLookupResult");
  if (!root) return;
  root.replaceChildren();
  if (!report || !report.generated_at) {
    root.textContent = "完成图纸解析后，可在“更多功能”中发起型号联网核验。结果只作为人工确认依据，不会自动修改 CAD/BOM。";
    return;
  }
  const intro = document.createElement("div");
  intro.className = "lookup-summary";
  intro.textContent = report.summary || "型号联网核验已完成。";
  root.appendChild(intro);
  const meta = document.createElement("div");
  meta.className = "lookup-meta";
  meta.textContent = `模型：${report.model || "—"} · 搜索 ${report.search_count || 0} 次 · ${report.generated_at}`;
  root.appendChild(meta);
  const confirmations = report.confirmations || {};
  const appliedChanges = report.applied_changes || [];
  (report.identifications || []).forEach(item => {
    const card = document.createElement("div");
    card.className = "lookup-item";
    const title = document.createElement("div");
    title.className = "lookup-title";
    title.textContent = item.candidate_model || "未命名型号";
    const statusTag = document.createElement("span");
    statusTag.className = `lookup-status ${item.status || "ambiguous"}`;
    statusTag.textContent = ({ matched: "已匹配", ambiguous: "待确认", not_found: "未找到", not_a_model: "非型号" })[item.status] || "待确认";
    title.appendChild(statusTag);
    card.appendChild(title);
    const body = document.createElement("div");
    body.className = "lookup-body";
    const name = [item.manufacturer, item.identified_part_name || item.category].filter(Boolean).join(" · ");
    body.textContent = `${name || "未形成可靠零件结论"}${item.specification_summary ? `\n${item.specification_summary}` : ""}${item.evidence_summary ? `\n依据：${item.evidence_summary}` : ""}`;
    card.appendChild(body);
    const confidence = document.createElement("div");
    confidence.className = "lookup-confidence";
    const applied = appliedChanges.find(change => String(change.candidate_model || "").trim().toUpperCase() === String(item.candidate_model || "").trim().toUpperCase());
    confidence.textContent = applied
      ? `联网匹配置信度：${Math.round(Number(item.confidence || 0) * 100)}% · 已自动同步至${applied.target === "part" ? "零件清单" : "BOM"}并创建新版本`
      : `联网匹配置信度：${Math.round(Number(item.confidence || 0) * 100)}% · 未形成可自动写入的可靠匹配`;
    card.appendChild(confidence);
    const confirmation = confirmations[item.candidate_model] || confirmations[String(item.candidate_model || "").trim()] || null;
    const actions = document.createElement("div");
    actions.className = "lookup-actions";
    if (applied) {
      const confirmed = document.createElement("span");
      confirmed.className = "lookup-confirmed confirmed";
      confirmed.textContent = "已自动同步（可在版本记录中回溯）";
      actions.appendChild(confirmed);
    } else if (confirmation) {
      const confirmed = document.createElement("span");
      confirmed.className = `lookup-confirmed ${confirmation.decision}`;
      confirmed.textContent = confirmation.decision === "confirmed" ? `已确认：${confirmation.by || ""}` : `已驳回：${confirmation.by || ""}`;
      actions.appendChild(confirmed);
    } else {
      [
        ["confirmed", "标记已复核"],
        ["rejected", "驳回结论"],
      ].forEach(([decision, text]) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = decision === "confirmed" ? "lookup-confirm" : "lookup-reject";
        button.textContent = text;
        button.onclick = () => confirmModelLookup(item.candidate_model, decision);
        actions.appendChild(button);
      });
    }
    card.appendChild(actions);
    root.appendChild(card);
  });
  if (report.product_summary || (report.proposed_components || []).length || (report.process_designs || []).length) {
    const research = document.createElement("div");
    research.className = "lookup-product-research";
    const heading = document.createElement("div");
    heading.className = "lookup-title";
    heading.textContent = "产品级结构与工艺推演";
    research.appendChild(heading);
    if (report.product_summary) {
      const summary = document.createElement("div");
      summary.className = "lookup-body";
      summary.textContent = report.product_summary;
      research.appendChild(summary);
    }
    if ((report.proposed_components || []).length) {
      const componentTitle = document.createElement("div");
      componentTitle.className = "lookup-research-heading";
      componentTitle.textContent = "联网推演候选部件（已作为待图纸确认项同步至 BOM）";
      research.appendChild(componentTitle);
      report.proposed_components.forEach(item => {
        const line = document.createElement("div");
        line.className = "lookup-research-item";
        line.textContent = `${item.name}${item.category ? ` · ${item.category}` : ""}${item.role ? `：${item.role}` : ""}${item.evidence_summary ? `\n依据：${item.evidence_summary}` : ""}`;
        research.appendChild(line);
      });
    }
    if ((report.process_designs || []).length) {
      const processTitle = document.createElement("div");
      processTitle.className = "lookup-research-heading";
      processTitle.textContent = "公开资料中的技术 / 工艺要点（待工程确认）";
      research.appendChild(processTitle);
      report.process_designs.forEach(item => {
        const line = document.createElement("div");
        line.className = "lookup-research-item";
        line.textContent = `${item.name}${item.related_component ? ` · ${item.related_component}` : ""}\n${item.design_summary || ""}${(item.key_controls || []).length ? `\n需确认：${item.key_controls.join("；")}` : ""}`;
        research.appendChild(line);
      });
    }
    root.appendChild(research);
  }
  if ((report.search_sources || []).length) {
    const sources = document.createElement("div");
    sources.className = "lookup-sources";
    const heading = document.createElement("div");
    heading.textContent = "联网搜索来源";
    sources.appendChild(heading);
    report.search_sources.forEach(source => {
      const link = document.createElement("a");
      const href = safeExternalUrl(source.url);
      link.textContent = source.title || source.url;
      if (href) { link.href = href; link.target = "_blank"; link.rel = "noopener"; }
      else link.removeAttribute("href");
      sources.appendChild(link);
    });
    root.appendChild(sources);
  }
  if ((report.open_questions || []).length) {
    const questions = document.createElement("div");
    questions.className = "lookup-questions";
    questions.textContent = `待确认：${report.open_questions.join("；")}`;
    root.appendChild(questions);
  }
}

async function loadModelLookup(projectId) {
  try {
    const response = await fetch(`${API}/api/projects/${projectId}/model-lookup`);
    if (!response.ok) return renderModelLookup(null);
    let report = await response.json();
    // 兼容上线前已经完成的旧核验：只同步已保存结果，不会再发起联网/模型调用。
    if ((report.identifications || []).some(item => item.status === "matched") && !report.auto_sync_attempted_at) {
      const applied = await fetch(`${API}/api/projects/${projectId}/model-lookup/apply`, { method: "POST" });
      if (applied.ok) report = await applied.json();
    }
    if ((report.applied_changes || []).length && currentProject === projectId) {
      const project = await fetch(`${API}/api/projects/${projectId}`).then(r => r.ok ? r.json() : null);
      if (project?.ir) { currentIR = project.ir; renderIR(currentIR); }
    }
    renderModelLookup(report);
  } catch { renderModelLookup(null); }
}

async function confirmModelLookup(candidateModel, decision) {
  if (!currentProject) return;
  const note = prompt(decision === "confirmed" ? "填写确认说明（可选）" : "填写驳回原因（可选）", "") || "";
  try {
    const response = await fetch(`${API}/api/projects/${currentProject}/model-lookup/confirm`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ candidate_model: candidateModel, decision, note }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || response.status);
    renderModelLookup(payload);
    status(decision === "confirmed" ? `已记录型号 ${candidateModel} 的人工复核意见` : `已驳回型号 ${candidateModel} 的联网结论`);
  } catch (error) { status("记录确认失败: " + error.message); }
}

const WORKFLOW = ["upload", "parse", "generate"];
function setWorkflow(stage, hint) {
  // 当前工作台承载的是 2.1 图纸解析及其校核/几何处理；
  // 只有正式输出报告时才应进入第 3 步，因此生成 CAD 后仍保持第 2 步高亮。
  const normalized = (stage === "review" || stage === "generate") ? "parse" : stage;
  const current = Math.max(0, WORKFLOW.indexOf(normalized));
  document.querySelectorAll(".main-step-wrapper[data-step]").forEach(wrapper => {
    const index = WORKFLOW.indexOf(wrapper.dataset.step);
    const step = wrapper.querySelector(".main-step");
    if (!step) return;
    step.classList.toggle("active", index === current);
    step.classList.toggle("completed", index < current);
    step.classList.toggle("pending", index > current);
  });
  document.querySelectorAll(".main-connector").forEach((line, index) =>
    line.classList.toggle("active", index < current));
  const el = $("workflowHint");
  if (el && hint) el.textContent = hint;
}

function questionStorageKey(question) {
  return [question.field || "", question.reason || "", question.guess || ""].join("\u001f");
}

function readQuestionConfirmations() {
  if (!currentProject) return {};
  try {
    return JSON.parse(localStorage.getItem(`cad-engine:clarifications:${currentProject}`) || "{}") || {};
  } catch {
    return {};
  }
}

function saveQuestionConfirmation(question, note) {
  if (!currentProject) return;
  const confirmations = readQuestionConfirmations();
  confirmations[questionStorageKey(question)] = note;
  try {
    localStorage.setItem(`cad-engine:clarifications:${currentProject}`, JSON.stringify(confirmations));
  } catch {
    // 浏览器禁用本地存储时仍保留本次页面内的确认输入。
  }
}

function renderClarifications(ir) {
  const ex = $("extras");
  ex.innerHTML = "";
  const standards = ir.standard_parts || [];
  const questions = ir.open_questions || [];

  if (standards.length) {
    const heading = document.createElement("div");
    heading.className = "standard-parts-heading";
    heading.textContent = "BOM / 外购及联网推演部件";
    ex.appendChild(heading);
    standards.forEach(s => {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = `🔩 ${s.spec} ×${s.quantity}`;
      ex.appendChild(tag);
    });
  }

  if (!questions.length) {
    const item = document.createElement("div");
    item.className = "issue-item issue-empty";
    const title = document.createElement("div");
    title.className = "issue-title";
    title.textContent = "人工确认说明";
    const desc = document.createElement("div");
    desc.className = "issue-desc";
    desc.textContent = "当前图纸未返回必须澄清的字段；如有补充约束，请在此确认。";
    const suggestion = document.createElement("div");
    suggestion.className = "issue-suggestion";
    suggestion.textContent = "AI 初步判断：现有信息可用于当前解析。";
    const action = document.createElement("div");
    action.className = "issue-action";
    const input = document.createElement("input");
    input.type = "text";
    input.className = "issue-input";
    input.placeholder = "输入确认说明...";
    const emptyQuestion = { field: "人工确认说明", reason: desc.textContent, guess: suggestion.textContent };
    const confirmations = readQuestionConfirmations();
    input.value = confirmations[questionStorageKey(emptyQuestion)] || "";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "issue-btn";
    button.textContent = input.value ? "更新确认" : "确认";
    button.onclick = () => {
      const note = input.value.trim();
      if (!note) { input.focus(); status("请输入确认说明后再提交。"); return; }
      saveQuestionConfirmation(emptyQuestion, note);
      button.textContent = "更新确认";
      status("已记录人工确认说明。");
    };
    action.append(input, button);
    item.append(title, desc, suggestion, action);
    ex.appendChild(item);
    return;
  }

  const heading = document.createElement("div");
  heading.className = "clarification-heading";
  heading.textContent = standards.length ? "待澄清问题" : "请确认以下信息";
  ex.appendChild(heading);

  const confirmations = readQuestionConfirmations();
  questions.forEach(question => {
    const item = document.createElement("div");
    item.className = "issue-item";
    const title = document.createElement("div");
    title.className = "issue-title";
    title.textContent = question.field || "待确认项";
    const desc = document.createElement("div");
    desc.className = "issue-desc";
    desc.textContent = question.reason || "请补充确认说明。";
    item.append(title, desc);

    const suggestion = document.createElement("div");
    suggestion.className = "issue-suggestion";
    suggestion.textContent = `AI 初步判断：${question.guess || "图纸信息不足，需人工确认后继续。"}`;
    item.appendChild(suggestion);

    const action = document.createElement("div");
    action.className = "issue-action";
    const input = document.createElement("input");
    input.type = "text";
    input.className = "issue-input";
    input.placeholder = "输入确认说明...";
    input.value = confirmations[questionStorageKey(question)] || "";
    input.setAttribute("aria-label", `${question.field || "待确认项"}的确认说明`);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "issue-btn";
    button.textContent = input.value ? "更新确认" : "确认";
    button.onclick = () => {
      const note = input.value.trim();
      if (!note) {
        input.focus();
        status("请输入确认说明后再提交。");
        return;
      }
      saveQuestionConfirmation(question, note);
      button.textContent = "更新确认";
      status(`已记录「${question.field || "待确认项"}」的确认说明。`);
    };
    action.append(input, button);
    item.appendChild(action);
    ex.appendChild(item);
  });
}

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

$("btnMoreActions").onclick = () => {
  const sheet = $("actionSheet");
  sheet.hidden = !sheet.hidden;
  $("btnMoreActions").setAttribute("aria-expanded", String(!sheet.hidden));
};
document.addEventListener("click", (event) => {
  const sheet = $("actionSheet"), trigger = $("btnMoreActions");
  if (sheet && !sheet.hidden && !sheet.contains(event.target) && event.target !== trigger) {
    sheet.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
  }
});

$("btnPrevious").onclick = () => {
  if (history.length > 1) history.back();
  else location.href = "index.html";
};
$("btnReport").onclick = () => {
  if (!currentProject) {
    status("请先创建或打开一个项目，再查看工艺解析报告。");
    return;
  }
  location.href = `report.html?project=${encodeURIComponent(currentProject)}`;
};

function openBusinessWorkbench(biz) {
  const project = currentProject ? `&project=${encodeURIComponent(currentProject)}` : "";
  location.href = `/apps/tech-process/?biz=${encodeURIComponent(biz)}${project}`;
}

function activeChatSession() {
  if (!currentProject) return [];
  if (!chatSessions.has(currentProject)) chatSessions.set(currentProject, []);
  return chatSessions.get(currentProject);
}

function chatPartLabel(part) {
  if (!part) return "当前项目整体";
  return `${part.part_id} · ${part.name || "未命名零件"}`;
}

function renderChat() {
  const box = $("chatMessages");
  const messages = activeChatSession();
  box.innerHTML = "";
  if (!messages.length) {
    box.innerHTML = `<div class="chat-empty"><div class="chat-empty-mark">AI</div><strong>图纸工艺助手</strong><p>选择零件后，可以询问结构、材料、工艺路径或待澄清事项。</p></div>`;
    return;
  }
  messages.forEach(message => {
    const row = document.createElement("div");
    row.className = `chat-message ${message.role}${message.error ? " error" : ""}`;
    const avatar = document.createElement("span");
    avatar.className = "chat-avatar";
    avatar.textContent = message.role === "user" ? "U" : "AI";
    const bubble = document.createElement("div");
    bubble.className = "chat-bubble";
    bubble.textContent = message.content;
    row.append(avatar, bubble);
    box.appendChild(row);
  });
  box.scrollTop = box.scrollHeight;
}

function renderChatTyping() {
  const box = $("chatMessages");
  const row = document.createElement("div");
  row.className = "chat-message assistant";
  row.id = "chatTyping";
  row.innerHTML = `<span class="chat-avatar">AI</span><div class="chat-bubble chat-typing"><i></i><i></i><i></i></div>`;
  box.appendChild(row);
  box.scrollTop = box.scrollHeight;
}

function updateChatContext(part = null) {
  const context = $("chatContext");
  const reference = $("chatReference");
  const badge = $("chatModelBadge");
  if (!currentProject) {
    context.textContent = "选择项目后可结合图纸和零件信息提问";
    reference.textContent = "当前未引用图纸";
    badge.textContent = "项目上下文";
    return;
  }
  const device = (currentIR && currentIR.device_name) || "当前图纸";
  context.textContent = `当前上下文：${device} · ${chatPartLabel(part)}`;
  reference.textContent = `已引用：${chatPartLabel(part)}、设计意图与解析结果`;
  badge.textContent = "AI 工艺助手";
}

function appendChatMessage(role, content, error = false) {
  activeChatSession().push({ role, content, error });
  renderChat();
}

async function refreshAfterChatEdit(edit) {
  if (!edit || !currentProject || !edit.part_id) return;
  status(`AI 已保存 ${edit.part_id} 的参数修改，正在刷新工作台…`, true);
  try {
    if (edit.requires_regeneration) {
      const response = await fetch(`${API}/api/projects/${currentProject}/parts/${encodeURIComponent(edit.part_id)}/regenerate`, { method: "POST" });
      const generated = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(generated.detail || "几何重生失败");
      upsertLocal(currentGeometry, generated.geometry);
      upsertLocal(currentDrawings, generated.drawings);
    }
    const project = await fetch(`${API}/api/projects/${currentProject}`).then(r => r.ok ? r.json() : null);
    if (project?.ir) {
      currentIR = project.ir;
      currentGeometry = project.geometry || currentGeometry;
      currentDrawings = project.drawings || currentDrawings;
      renderIR(currentIR);
      const part = (currentIR.parts || []).find(item => item.part_id === edit.part_id);
      if (part) selectPart(part);
    }
    loadVersions();
    status(edit.requires_regeneration ? "AI 修改已保存，3D 与工程图已重新生成。" : "AI 修改已保存，并已创建新版本。");
  } catch (error) {
    status(`AI 参数已保存，但几何/工程图刷新失败：${error.message}`);
  }
}

async function sendWorkbenchChat() {
  const input = $("chatInput");
  const message = input.value.trim();
  if (!message || chatBusy) return;
  if (!currentProject) {
    appendChatMessage("assistant", "请先创建或打开项目，再结合图纸与零件信息提问。", true);
    return;
  }
  const selected = (currentIR && currentIR.parts || []).find(p => p.part_id === currentSelectedId);
  appendChatMessage("user", message);
  input.value = "";
  input.style.height = "auto";
  chatBusy = true;
  $("btnChatSend").disabled = true;
  renderChatTyping();
  try {
    const history = activeChatSession().slice(-7, -1).map(item => ({
      role: item.role,
      content: item.content,
    }));
    const response = await fetch(`${API}/api/projects/${currentProject}/workbench-chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, part_id: selected ? selected.part_id : "", history }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || response.status);
    appendChatMessage("assistant", data.answer || "未获得可用回复，请换一种问法。\n");
    if (data.model) $("chatModelBadge").textContent = data.model;
    if (data.edit_applied) await refreshAfterChatEdit(data.edit_applied);
  } catch (error) {
    appendChatMessage("assistant", `对话暂时失败：${error.message || "请稍后重试"}`, true);
  } finally {
    document.getElementById("chatTyping")?.remove();
    chatBusy = false;
    $("btnChatSend").disabled = false;
    $("chatInput").focus();
  }
}

$("btnTechProcess").onclick = () => openBusinessWorkbench("tech");
$("btnQuoteManagement").onclick = () => openBusinessWorkbench("quote");

$("chatForm").onsubmit = (event) => {
  event.preventDefault();
  sendWorkbenchChat();
};
$("chatInput").addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendWorkbenchChat();
  }
});
$("chatInput").addEventListener("input", event => {
  event.target.style.height = "auto";
  event.target.style.height = `${Math.min(event.target.scrollHeight, 96)}px`;
});
$("btnChatUseDrawing").onclick = () => {
  const selected = (currentIR && currentIR.parts || []).find(p => p.part_id === currentSelectedId);
  const input = $("chatInput");
  input.value = selected
    ? `请结合当前图纸，分析 ${chatPartLabel(selected)} 的制造工艺与风险。`
    : "请结合当前图纸，概述零件结构、关键工艺和待澄清风险。";
  input.focus();
  input.dispatchEvent(new Event("input"));
};

function bindFilePicker(inputId, nameId) {
  const input = $(inputId);
  const name = $(nameId);
  input.addEventListener("change", () => {
    const files = Array.from(input.files || []);
    if (!files.length) name.textContent = "未选择文件";
    else if (files.length === 1) name.textContent = files[0].name;
    else name.textContent = `已选择 ${files.length} 个文件`;
  });
}
bindFilePicker("fileInput", "fileInputName");
bindFilePicker("attachInput", "attachInputName");
bindFilePicker("file3d", "file3dName");

function renderUserBox() {
  // 2.1 工作台不再放账户/登出入口，账户资料和权限统一由首页右上角进入。
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
  updateChatContext();
  renderChat();
  const phEl = document.querySelector(".image-wrap .placeholder");
  if (phEl) phEl.remove();
  $("sourceImg").style.display = "";
  $("sourceImg").src = mediaUrl(`${API}/api/projects/${currentProject}/source?t=${Date.now()}`);
  renderProjectEvidence({
    source_filename: f.name,
    attachments: Array.from($("attachInput").files).map(file => file.name),
  });
  $("btnParse").disabled = false;
  $("btnVerify").disabled = true;
  $("btnDecompose").disabled = true;
  $("btnModelLookup").disabled = true;
  $("btnGenerate").disabled = true;
  $("btnDrawings").disabled = true;
  $("btnBom").disabled = true;
  renderModelLookup(null);
  const nAtt = $("attachInput").files.length;
  status(`已创建项目 ${currentProject}（补充说明${$("noteInput").value ? "✓" : "—"}，佐证文件 ${nAtt} 个），可解析`);
  setWorkflow("parse", "图纸已保存。确认后可开始 AI 解析。");
  loadProjects();
};

$("btnParse").onclick = async () => {
  if (!currentProject) return;
  status("模型正在解析图纸与技术文档需求为结构化 IR（含视觉理解，稍候）...", true);
  try {
    currentIR = await runTask(currentProject, `/api/projects/${currentProject}/parse`, "解析");
    renderIR(currentIR);
    $("btnVerify").disabled = false;
    $("btnDecompose").disabled = false;
    $("btnModelLookup").disabled = false;
    $("btnGenerate").disabled = false;
    $("btnDrawings").disabled = false;
    $("btnBom").disabled = false;
    const documentCount = await fetch(`${API}/api/projects/${currentProject}/attachments`)
      .then(r => r.ok ? r.json() : { attachments: [] })
      .then(payload => (payload.attachments || []).length)
      .catch(() => 0);
    status(`解析完成（已结合 ${documentCount} 份技术资料；平均置信度 ${avgConfidence(currentIR)}）`);
    setWorkflow("review", "AI 已完成解析，请确认零件、材料与待澄清项。");
  } catch (e) { status("解析失败: " + e.message); }
};

$("btnModelLookup").onclick = async () => {
  if (!currentProject || !currentIR) return;
  status("Qwen 正在联网核验型号候选与公开零件资料（会产生联网搜索与模型 token 消耗）...", true);
  try {
    const report = await runTask(currentProject, `/api/projects/${currentProject}/model-lookup`, "型号联网核验");
    renderModelLookup(report);
    $("modelLookupDetails").open = true;
    const applied = (report.applied_changes || []).length;
    if (applied) {
      const project = await fetch(`${API}/api/projects/${currentProject}`).then(r => r.ok ? r.json() : null);
      if (project?.ir) { currentIR = project.ir; renderIR(currentIR); }
      loadVersions();
    }
    status(`型号联网核验完成（${(report.identifications || []).length} 个候选，搜索 ${report.search_count || 0} 次；已同步 ${applied} 项至零件清单/BOM，并生成新版本）`);
  } catch (error) { status("型号联网核验失败: " + error.message); }
};

$("btnVerify").onclick = async () => {
  if (!currentProject) return;
  const before = avgConfidence(currentIR);
  status("模型正在对照原图自校验（会产生一次额外调用费用）...", true);
  try {
    const result = await runTask(currentProject, `/api/projects/${currentProject}/verify`, "校验");
    if (result.verification && result.verification.status === "rejected") {
      currentIR = result.ir;
      renderIR(currentIR);
      status(result.verification.message + " 详情：" + result.verification.detail);
      return;
    }
    currentIR = result;
    renderIR(currentIR);
    status(`校验完成（平均置信度 ${before} → ${avgConfidence(currentIR)}）`);
    setWorkflow("review", "校验完成，可继续确认识别结果或生成 CAD。");
  } catch (e) { status("自校验失败: " + e.message); }
};

$("btnDecompose").onclick = async () => {
  if (!currentProject) return;
  status("模型正在做拆解推荐增强...", true);
  try {
    currentIR = await runTask(currentProject, `/api/projects/${currentProject}/decompose`, "拆解推荐");
    renderIR(currentIR);
    status("拆解推荐完成");
    setWorkflow("review", "拆解建议已更新，可确认后生成 CAD。");
  } catch (e) { status("拆解失败: " + e.message); }
};

$("btnGenerate").onclick = async () => {
  if (!currentProject) return;
  status("CAD 内核正在生成几何(STEP/STL)并校验...", true);
  try {
    currentGeometry = await runTask(currentProject, `/api/projects/${currentProject}/generate`, "几何生成");
    renderIR(currentIR);  // 重渲染以挂上几何状态
    status("几何生成完成，点击零件查看 3D");
    setWorkflow("generate", "CAD 几何已生成，可查看 3D、工程图和 BOM。");
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
// 同一页面中相同任务只允许一个轮询链，双击不会重复提交/重复计费。
const activeTaskPromises = new Map();

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
  const key = `${projectId}:${submitPath}`;
  const active = activeTaskPromises.get(key);
  if (active) {
    status(`${label}已在处理中，复用当前任务…`, true);
    return active;
  }

  const task = (async () => {
    const r = await fetch(`${API}${submitPath}`, { method: "POST" });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || r.status);
    status(`${label}已提交(任务 ${String(d.task_id).slice(0, 6)})，处理中…`, true);
    return pollTask(projectId, d.task_id, label);
  })();
  activeTaskPromises.set(key, task);
  try {
    return await task;
  } finally {
    if (activeTaskPromises.get(key) === task) activeTaskPromises.delete(key);
  }
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
  renderProjectEvidence(data.meta);

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
  $("btnModelLookup").disabled = !data.ir;
  $("btnGenerate").disabled = !isImg || !data.ir;
  $("btnDrawings").disabled = !isImg || !data.ir;
  $("btnBom").disabled = !data.ir;
  if (data.ir) renderIR(data.ir);
  loadModelLookup(pid);
  updateChatContext();
  renderChat();
  loadVersions();
  const note = data.meta && data.meta.note ? data.meta.note : "";
  const atts = data.meta && data.meta.attachments ? data.meta.attachments.length : 0;
  status(`已打开项目 ${pid}（补充说明${note ? "✓" : "—"}，佐证文件 ${atts} 个）`);
  setWorkflow(data.geometry ? "generate" : data.ir ? "review" : "parse");
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
  div.className = "part part-item confirmed";
  div.dataset.partId = p.part_id;
  div.style.marginLeft = pad + "px";
  const feats = (p.features || []).map(f => f.type).join(", ");
  const gstat = g ? (g.ok ? " · 几何✓" : " · 几何✗") : "";
  const dstat = dw ? (dw.ok ? " · 2D✓" : " · 2D✗") : "";
  div.innerHTML =
    `${partThumbnail(p)}<div class="part-info">` +
    `<div class="part-name">${esc(p.part_id)} ${esc(p.name)}` +
    `<span class="part-confidence">${(p.confidence * 100 | 0)}%</span></div>` +
    `<div class="part-type">${esc(feats)} · ${p.material ? esc(p.material.spec) : "材料待确认"} · ${p.quantity}件${gstat}${dstat}</div>` +
    (p.recommendation ? `<div class="recommend">💡 ${esc(p.recommendation)}</div>` : "") +
    `</div>`;
  div.onclick = () => selectPart(p);
  container.appendChild(div);
}

// 根据已经解析出的几何特征显示一个轻量缩略图。它完全由本地 IR 生成，
// 不读取或生成新的图纸，也不会发起模型调用。
function partThumbnail(part) {
  const types = new Set((part.features || []).map(f => f && f.type).filter(Boolean));
  let kind = "generic";
  if (types.has("hole_pattern")) kind = "pattern";
  else if (types.has("cylinder")) kind = "cylinder";
  else if (types.has("box")) kind = "box";
  else if (types.has("plate")) kind = "plate";
  else if (types.has("hole")) kind = "hole";

  const drawings = {
    plate: `<svg viewBox="0 0 32 32" aria-hidden="true"><rect x="6" y="9" width="20" height="14" rx="2"/><path d="M9 13h14M9 19h14"/></svg>`,
    box: `<svg viewBox="0 0 32 32" aria-hidden="true"><path d="M7 11 16 6l9 5v11l-9 5-9-5z"/><path d="M7 11l9 5 9-5M16 16v11"/></svg>`,
    cylinder: `<svg viewBox="0 0 32 32" aria-hidden="true"><ellipse cx="16" cy="9" rx="8" ry="3.5"/><path d="M8 9v13c0 2 16 2 16 0V9"/><path d="M8 22c0 2 16 2 16 0"/></svg>`,
    pattern: `<svg viewBox="0 0 32 32" aria-hidden="true"><rect x="5" y="6" width="22" height="20" rx="3"/><circle cx="11" cy="12" r="1.7"/><circle cx="21" cy="12" r="1.7"/><circle cx="11" cy="20" r="1.7"/><circle cx="21" cy="20" r="1.7"/></svg>`,
    hole: `<svg viewBox="0 0 32 32" aria-hidden="true"><rect x="5" y="8" width="22" height="16" rx="3"/><circle cx="16" cy="16" r="4"/></svg>`,
    generic: `<svg viewBox="0 0 32 32" aria-hidden="true"><path d="m16 5 9 5v12l-9 5-9-5V10z"/><path d="m7 10 9 5 9-5M16 15v12"/></svg>`,
  };
  const labels = { plate: "板件", box: "方体件", cylinder: "圆柱件", pattern: "孔阵列件", hole: "带孔件", generic: "零件" };
  return `<div class="part-icon part-icon-${kind}" title="${labels[kind]}" aria-label="${labels[kind]}">${drawings[kind]}</div>`;
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
  $("deviceName").textContent = ir.device_name || "当前解析任务";
  $("intent").innerHTML =
    `<b>设计意图:</b> ${esc(ir.design_intent || "")}<br>` +
    (ir.overall_dims ? `<b>总体尺寸:</b> ${esc(ir.overall_dims)}<br>` : "") +
    (ir.assembly_notes ? `<b>装配:</b> ${esc(ir.assembly_notes)}` : "");
  $("partsMetric").textContent = `${(ir.parts || []).length}`;
  $("confidenceMetric").textContent = avgConfidence(ir);

  const tree = $("tree");
  tree.innerHTML = "";
  tree.classList.toggle("empty-state", !(ir.parts || []).length);
  const partById = {};
  (ir.parts || []).forEach(p => { partById[p.part_id] = p; });
  const root = buildClientTree(ir);
  root.children.forEach(c => renderNode(c, tree, 0, partById));

  // 标准件 + 待澄清：确认说明只在本地保存，不会触发新的 AI 调用。
  renderClarifications(ir);

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
const VERSION_STAGE_LABEL = {
  parsed: "AI 图纸解析完成", parsed_3d: "导入 3D 模型解析", verified: "AI 校验修正完成",
  decomposed: "AI 拆解推荐已同步", edited: "人工编辑零件参数", ai_chat_edited: "AI 对话修改零件参数",
  model_lookup_applied: "联网型号核验同步清单", restored: "恢复历史版本",
};

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
      `<span class="vstage">${esc(VERSION_STAGE_LABEL[v.stage] || v.stage)}</span></div>` +
      `<div class="vmeta">${esc(v.ts)} · ${esc(v.author)} · ${v.parts}零件${conf}</div>` +
      (v.note ? `<div class="vmeta">本次操作：${esc(v.note)}</div>` : "") +
      `<div class="vmeta">相对上一版本：${esc(v.change_summary || "暂无变更说明")}</div>` +
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
  const S = d.standard_parts || {};
  (S.added || []).forEach(item =>
    html += `<div class="chg"><span class="add">+ 新增 BOM / 外购件 ${esc(item.spec || item.model_no || "")}</span></div>`);
  (S.removed || []).forEach(item =>
    html += `<div class="chg"><span class="del">− 删除 BOM / 外购件 ${esc(item.spec || item.model_no || "")}</span></div>`);
  (S.modified || []).forEach(item => {
    html += `<div class="chg"><span class="f">BOM / 外购件 ${esc(item.spec || item.key || "")}</span>`;
    item.changes.forEach(c => {
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
  $("viewerPartName").textContent = `${part.part_id} ${part.name}`;
  updateChatContext(part);

  // 跳转到工艺拆解 / 成本分析页
  // 页面版本参数用于绕开浏览器对旧深色详情页的历史缓存；后端会忽略 ui 参数。
  const pq = `ui=20260714-unified2&project=${encodeURIComponent(currentProject)}&part=${encodeURIComponent(part.part_id)}`;
  let html = `<div class="part-nav">` +
    `<a class="btn-process" href="process.html?${pq}">🔧 工艺拆解 →</a>` +
    `<a class="btn-cost" href="cost.html?${pq}">💰 成本分析 →</a></div>`;
  if (part.model_no || part.manufacturer || part.model_specification) {
    html += `<div class="model-identity"><b>联网型号核验</b>` +
      `${part.model_no ? `<div>型号：${esc(part.model_no)}</div>` : ""}` +
      `${part.manufacturer ? `<div>制造商：${esc(part.manufacturer)}</div>` : ""}` +
      `${part.model_specification ? `<div>公开规格：${esc(part.model_specification)}</div>` : ""}` +
      `${part.model_lookup_evidence ? `<div>核验依据：${esc(part.model_lookup_evidence)}</div>` : ""}` +
      `</div>`;
  }
  let parameterHtml = "";

  // 行内可编辑特征(仅"图→IR"项目;3D 导入项目几何来自真实实体,不改参重生)
  if (currentIsImg) {
    parameterHtml += `<div class="edit-grid">` +
      `<label>名称</label><input data-pf="name" value="${esc(part.name || "")}"/>` +
      `<label>数量</label><input data-pf="quantity" type="number" value="${part.quantity || 1}"/>` +
      `<label>材料</label><input data-pf="material" value="${esc(part.material ? part.material.spec : "")}"/>` +
      `</div>`;
    (part.features || []).forEach((f, fi) => {
      const fields = FEAT_FIELDS[f.type] || [];
      parameterHtml += `<div class="feat-edit"><div class="feat-type">特征 #${fi + 1}: ${f.type}` +
        `${f.purpose ? " · " + esc(f.purpose) : ""}</div><div class="edit-grid">`;
      fields.forEach(k => {
        const v = f[k];
        parameterHtml += `<label>${k}</label><input data-fi="${fi}" data-fk="${k}" type="number" ` +
          `value="${v != null ? v : ""}"/>`;
      });
      parameterHtml += `</div></div>`;
    });
    parameterHtml += `<button class="btn-regen" id="btnRegen">保存并重新生成该零件</button>`;
  } else {
    parameterHtml += "<table>";
    (part.features || []).forEach(f => {
      const dims = Object.entries(f).filter(([k, v]) =>
        v != null && !["type", "purpose"].includes(k)).map(([k, v]) => `${k}=${v}`).join(", ");
      parameterHtml += `<tr><td>${f.type}</td><td>${dims}${f.purpose ? " · " + esc(f.purpose) : ""}</td></tr>`;
    });
    parameterHtml += "</table>";
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
    html += `<div class="warn">尚未生成几何，请使用底部的「生成 CAD 几何」。</div>`;
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
  $("parameterEditor").innerHTML = parameterHtml || "此零件暂无可编辑参数。";
  const rb = document.getElementById("btnRegen");
  if (rb) rb.onclick = () => savePartEdits(part.part_id);
}

// 读取行内编辑 -> 更新 IR -> 保存 -> 单零件重生 -> 刷新
async function savePartEdits(partId) {
  if (!currentProject || !currentIR) return;
  const part = (currentIR.parts || []).find(p => p.part_id === partId);
  if (!part) return;
  const detail = $("parameterEditor");

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
  scene.background = new THREE.Color(0xf8fafc);
  camera = new THREE.PerspectiveCamera(45, el.clientWidth / el.clientHeight, 0.1, 100000);
  camera.position.set(120, 120, 120);
  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(el.clientWidth, el.clientHeight);
  el.appendChild(renderer.domElement);
  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.06;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 4;

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

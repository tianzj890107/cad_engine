/* 全流程共用的项目级 AI 对话。记录写入项目数据，切换页面后仍连续。 */
(() => {
  const params = new URLSearchParams(location.search);
  const projectId = params.get("project") || "";
  if (!projectId || document.querySelector(".project-chat-launcher")) return;

  const icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="7" width="16" height="12" rx="3"/><path d="M12 3v4M9 12h.01M15 12h.01M8.5 15.5c2 1 5 1 7 0"/></svg>';
  const sendIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></svg>';
  const launcher = document.createElement("button");
  launcher.type = "button";
  launcher.className = "project-chat-launcher";
  launcher.title = "项目 AI 对话";
  launcher.setAttribute("aria-label", "打开项目 AI 对话");
  launcher.innerHTML = icon;
  const panel = document.createElement("section");
  panel.className = "project-chat-panel";
  panel.hidden = true;
  panel.setAttribute("aria-label", "项目 AI 对话");
  panel.setAttribute("aria-hidden", "true");
  panel.innerHTML = `<header class="project-chat-header"><div class="project-chat-title">${icon}<div><div>项目 AI 对话</div><div class="project-chat-project">同一需求单跨页面连续对话</div></div></div><button type="button" class="project-chat-close" aria-label="关闭对话">×</button></header><div class="project-chat-context">结合当前项目的需求、图纸解析和报告内容回答</div><div class="project-chat-messages" aria-live="polite"></div><form class="project-chat-composer"><textarea maxlength="1600" rows="1" placeholder="输入工艺问题或需求…" aria-label="输入工艺问题"></textarea><button class="project-chat-send" type="submit" aria-label="发送">${sendIcon}</button></form>`;
  document.body.append(launcher, panel);
  const messageBox = panel.querySelector(".project-chat-messages");
  const form = panel.querySelector("form");
  const input = panel.querySelector("textarea");
  const sendButton = panel.querySelector(".project-chat-send");
  launcher.setAttribute("aria-expanded", "false");
  let messages = [];
  let loading = false;

  const authHeaders = (json = false) => {
    const headers = {};
    const token = localStorage.getItem("authToken") || "";
    if (token) headers.Authorization = `Bearer ${token}`;
    if (json) headers["Content-Type"] = "application/json";
    return headers;
  };
  const pageContext = () => `${document.title || "项目页面"} · ${location.pathname}`.slice(0, 160);
  const escapeText = value => String(value || "");
  function render() {
    messageBox.replaceChildren();
    if (!messages.length) {
      const empty = document.createElement("div");
      empty.className = "project-chat-empty";
      empty.innerHTML = `<strong>项目工艺助手</strong>可在任何流程页面继续提问。2.1 图纸解析页选中零件后，可提出明确数值让 AI 受控修改参数。`;
      messageBox.append(empty);
      return;
    }
    messages.forEach(item => {
      const row = document.createElement("div");
      row.className = `project-chat-message ${item.role === "assistant" ? "assistant" : "user"}${item.error ? " error" : ""}`;
      const avatar = document.createElement("span");
      avatar.className = "project-chat-avatar";
      avatar.textContent = item.role === "assistant" ? "AI" : "我";
      const bubble = document.createElement("div");
      bubble.className = "project-chat-bubble";
      bubble.textContent = escapeText(item.content);
      row.append(avatar, bubble);
      messageBox.append(row);
    });
    messageBox.scrollTop = messageBox.scrollHeight;
  }
  async function load() {
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/ai-chat`, { headers: authHeaders() });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "读取对话失败");
      messages = Array.isArray(data.messages) ? data.messages.filter(item => item && item.content) : [];
    } catch (error) {
      messages = [{ role: "assistant", content: `暂时无法读取项目对话：${error.message}`, error: true }];
    }
    render();
  }
  function isWorkbench() {
    return /\/index\.html$/.test(location.pathname) && Boolean(window.cadWorkbenchContext);
  }
  async function send(message) {
    const endpoint = isWorkbench() ? "workbench-chat" : "ai-chat";
    const payload = { message, page_context: pageContext() };
    if (endpoint === "workbench-chat") payload.part_id = window.cadWorkbenchContext.selectedPartId || "";
    const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/${endpoint}`, {
      method: "POST", headers: authHeaders(true), body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "AI 对话请求失败");
    return data;
  }
  function autoSize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 84)}px`;
  }
  function setOpen(open) {
    if (open) {
      panel.hidden = false;
      panel.setAttribute("aria-hidden", "false");
      launcher.setAttribute("aria-expanded", "true");
      launcher.setAttribute("aria-label", "收起项目 AI 对话");
      launcher.title = "收起项目 AI 对话";
      launcher.classList.add("is-open");
      requestAnimationFrame(() => panel.classList.add("is-visible"));
      load();
      input.focus();
      return;
    }
    panel.classList.remove("is-visible");
    panel.setAttribute("aria-hidden", "true");
    launcher.setAttribute("aria-expanded", "false");
    launcher.setAttribute("aria-label", "打开项目 AI 对话");
    launcher.title = "打开项目 AI 对话";
    launcher.classList.remove("is-open");
    window.setTimeout(() => {
      if (!panel.classList.contains("is-visible")) panel.hidden = true;
    }, 230);
  }
  launcher.addEventListener("click", () => setOpen(panel.hidden));
  panel.querySelector(".project-chat-close").addEventListener("click", () => setOpen(false));
  input.addEventListener("input", autoSize);
  input.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); form.requestSubmit(); }
  });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message || loading) return;
    loading = true;
    sendButton.disabled = true;
    messages.push({ role: "user", content: message });
    render();
    input.value = "";
    autoSize();
    try {
      const data = await send(message);
      messages.push({ role: "assistant", content: data.answer || "未获得可用回复，请换一种问法。" });
      if (data.edit_applied) window.dispatchEvent(new CustomEvent("cad-engine:workbench-chat-edit", { detail: data.edit_applied }));
    } catch (error) {
      messages.push({ role: "assistant", content: `对话暂时失败：${error.message || "请稍后重试"}`, error: true });
    } finally {
      loading = false;
      sendButton.disabled = false;
      render();
      input.focus();
    }
  });
})();

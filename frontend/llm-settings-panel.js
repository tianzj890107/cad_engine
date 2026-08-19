/* 全局模型设置面板 —— 首页「模型设置」与 2.1 页 Agent 小窗共用同一份实现。
 *
 * 之前两处各写各的表单，字段和口径都对不上；现在渲染逻辑只有这一份，读写都打
 * /api/llm/settings。任何入口改，全局生效。
 *
 * 只暴露六项：多模态模型、语言模型、温度、最大 token、是否思考、API Key。
 * 语言模型同时用于文档分析与 Agent 对话 —— 不再单列「对话模型」，否则又会变成
 * 两个模型设置。API Key 同理只有一个。
 *
 * 用法：
 *   window.LlmSettingsPanel.mount(container, { onSaved })
 * container 可以是弹层（Agent 小窗）也可以是对话框正文（首页），面板自己不管定位。
 */
(() => {
  "use strict";

  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };
  const authHeaders = (json = false) => {
    const headers = {};
    const token = localStorage.getItem("authToken") || localStorage.getItem("cad_engine_token");
    if (token) headers.Authorization = `Bearer ${token}`;
    if (json) headers["Content-Type"] = "application/json";
    return headers;
  };

  const MODEL_FIELDS = [
    ["vision_model", "vision_options", "多模态模型",
     "图纸解析用；DeepSeek 无视觉能力，故不在此列"],
    ["text_model", "text_options", "语言模型",
     "文档分析、工艺推荐、成本测算与 Agent 对话都用它"],
  ];

  const NUMBER_FIELDS = [
    ["temperature", "温度", "0 ~ 1，留空用模型默认值", { min: 0, max: 1, step: 0.05 }],
    ["max_tokens", "最大 token", "留空用默认值", { min: 256, max: 64000, step: 256 }],
  ];

  async function load() {
    const response = await fetch("/api/llm/settings", { headers: authHeaders() });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    return data;
  }

  function mount(container, options = {}) {
    container.replaceChildren(el("div", "llm-set-status", "正在读取模型设置…"));
    load().then(settings => render(container, settings, options))
      .catch(error => {
        container.replaceChildren(
          el("div", "llm-set-status err", `读取模型设置失败：${error.message}`));
      });
  }

  function render(container, settings, options) {
    container.replaceChildren();
    const editable = Boolean(settings.editable);
    const status = el("div", "llm-set-status", editable
      ? "改动即时保存，对全平台所有任务生效。"
      : "只有系统管理员可以修改；此处仅供查看当前配置。");

    async function save(patch) {
      status.textContent = "保存中…";
      status.classList.remove("err");
      try {
        const response = await fetch("/api/llm/settings", {
          method: "PUT", headers: authHeaders(true), body: JSON.stringify(patch),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        Object.assign(settings, data);
        status.textContent = data.message || "已保存，全局生效";
        options.onSaved?.(data);
        return true;
      } catch (error) {
        status.textContent = `保存失败：${error.message}`;
        status.classList.add("err");
        return false;
      }
    }

    MODEL_FIELDS.forEach(([key, optionsKey, label, hint]) => {
      const row = el("div", "llm-set-row");
      row.append(el("label", "llm-set-label", label));
      const select = el("select", "llm-set-input");
      const values = settings[optionsKey] || [];
      if (!values.length) {
        select.append(el("option", null, "无可选模型"));
        select.disabled = true;
      } else {
        values.forEach(model => {
          const option = el("option", null, model.label || model.id);
          option.value = model.id;
          if (model.id === settings[key]) option.selected = true;
          select.append(option);
        });
        select.disabled = !editable;
        select.onchange = () => save({ [key]: select.value });
      }
      row.append(select);
      row.append(el("div", "llm-set-hint", hint));
      container.append(row);
    });

    NUMBER_FIELDS.forEach(([key, label, hint, range]) => {
      const row = el("div", "llm-set-row");
      row.append(el("label", "llm-set-label", label));
      const field = el("input", "llm-set-input");
      field.type = "number";
      Object.assign(field, range);
      field.value = settings[key] == null ? "" : settings[key];
      field.disabled = !editable;
      // 用 change 而不是 input：每敲一个数字就发一次请求毫无必要。
      field.onchange = () => save({ [key]: field.value === "" ? null : Number(field.value) });
      row.append(field);
      row.append(el("div", "llm-set-hint", hint));
      container.append(row);
    });

    const thinkingRow = el("label", "llm-set-check");
    const thinking = el("input");
    thinking.type = "checkbox";
    thinking.checked = Boolean(settings.thinking);
    thinking.disabled = !editable;
    thinking.onchange = () => save({ thinking: thinking.checked });
    thinkingRow.append(thinking, document.createTextNode("开启深度思考"));
    container.append(thinkingRow);

    // 只列当前两个模型实际用到的提供商 —— 没用到的 Key 摆出来只是噪声。
    // 两个模型同属一家时就只有一行。
    (settings.providers || []).forEach(item => {
      container.append(secretRow(item, editable, save));
    });
    container.append(status);
  }

  function secretRow(provider, editable, save) {
    const row = el("div", "llm-set-row");
    row.append(el("label", "llm-set-label", `${provider.label} API Key`));
    row.append(el("div", "llm-set-hint",
      (provider.key_set ? `当前：${provider.key_hint || "已配置"}` : "尚未配置")
      + ` · 网关 ${provider.base_url}`));
    const input = el("input", "llm-set-input");
    input.type = "password";
    input.autocomplete = "new-password";
    input.placeholder = "留空则不修改";
    input.disabled = !editable;
    row.append(input);
    const apply = el("button", "llm-set-btn", "更新");
    apply.type = "button";
    apply.disabled = !editable;
    apply.onclick = async () => {
      const value = input.value.trim();
      if (!value) return;
      // 保存成功立刻清空：密钥不该留在 DOM 里等着被截图或被自动填充读走。
      if (await save({ api_key: value, api_key_provider: provider.provider })) input.value = "";
    };
    row.append(apply);
    row.append(el("div", "llm-set-hint", "保存后不会再显示明文。"));
    return row;
  }

  window.LlmSettingsPanel = { mount, load, render };
})();

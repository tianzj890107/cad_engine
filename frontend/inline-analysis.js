/* 2.1 零件详情内嵌的工艺拆解/成本分析面板。复用原独立页面的接口和编辑能力。 */
(() => {
  "use strict";

  const TYPE_LABEL = {
    blank: "下料/备料", turning: "车", milling: "铣", drilling: "钻", boring: "镗",
    grinding: "磨", bench: "钳工", sheet_metal: "钣金", welding: "焊接",
    heat_treat: "热处理", surface: "表面处理", assembly: "装配", inspection: "检验", other: "其他",
  };
  const TYPES = Object.keys(TYPE_LABEL);
  const CAT_LABEL = {
    material: "材料费", machining: "机加工", standard_part: "标准件/外购", heat_treat: "热处理",
    surface: "表面处理", welding: "焊接", assembly: "装配", inspection: "检验",
    tooling: "工装摊销", logistics: "物流包装", overhead: "管理费", profit: "利润", other: "其他",
  };
  const CATS = Object.keys(CAT_LABEL);
  let active = null;

  const esc = value => String(value ?? "").replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  const attr = value => esc(value).replace(/"/g, "&quot;");
  const money = value => value == null ? "—" : Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const confClass = value => value >= 0.75 ? "" : value >= 0.5 ? "mid" : "lo";

  function restoreExpertPanel(state) {
    const panel = state?.context?.expertPanel;
    if (panel) panel.hidden = false;
  }

  async function jsonFetch(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.message || response.status);
    return data;
  }

  function open(mode, context) {
    if (!context?.host || !context.projectId || !context.part) return;
    restoreExpertPanel(active);
    if (context.expertPanel) context.expertPanel.hidden = true;
    const state = {
      mode,
      context,
      plan: null,
      validation: null,
      analysis: null,
      summary: null,
      editing: false,
      busy: false,
      root: null,
    };
    active = state;
    renderShell(state);
    load(state);
  }

  function renderShell(state) {
    const { context, mode } = state;
    const partName = context.part.name || context.part.part_id;
    const title = mode === "process" ? "工艺拆解" : "成本分析";
    const icon = window.cadWorkbenchIcons?.[mode] || "";
    const notePlaceholder = mode === "process"
      ? "补充工艺说明，如材料状态、关键表面粗糙度、设备或检验要求…"
      : "补充成本说明，如批量、材料价格、外购件单价或表面处理要求…";
    const quantity = mode === "cost"
      ? `<label class="inline-analysis-qty"><span>批量</span><input data-inline-quantity type="number" min="1" value="1" /></label>`
      : "";

    context.host.innerHTML = `<section class="inline-analysis" data-inline-mode-root="${mode}">
      <div class="inline-analysis-head">
      <div class="inline-analysis-title"><span class="inline-analysis-icon">${icon}</span><div><strong>${title}</strong><small>${esc(context.part.part_id)} · ${esc(partName)}</small></div></div>
        <button type="button" class="inline-analysis-close" data-inline-close>返回零件详情</button>
      </div>
      <div class="inline-analysis-tabs"><button type="button" data-inline-mode="process" class="${mode === "process" ? "active" : ""}">工艺拆解</button><button type="button" data-inline-mode="cost" class="${mode === "cost" ? "active" : ""}">成本分析</button></div>
      <div class="inline-analysis-inputs"><textarea data-inline-note rows="2" placeholder="${notePlaceholder}"></textarea>${quantity}<label class="inline-file-picker"><input data-inline-files type="file" multiple accept="image/*,.txt,.md,.csv,.json,.pdf,.yaml,.yml" /><span>选择补充文件</span><em data-inline-files-name>未选择文件</em></label></div>
      <div class="inline-analysis-actions"><button type="button" class="inline-action primary start-parse-btn" data-inline-generate>${mode === "process" ? "生成工艺拆解" : "生成成本分析"}</button><button type="button" class="inline-action" data-inline-edit disabled>编辑</button><button type="button" class="inline-action save" data-inline-save hidden>保存</button></div>
      <div class="inline-analysis-status" data-inline-status></div>
      <div class="inline-analysis-body" data-inline-body>正在读取${title}…</div>
    </section>`;
    state.root = context.host.querySelector(".inline-analysis");
    bindShell(state);
  }

  function bindShell(state) {
    const root = state.root;
    root.querySelector("[data-inline-close]").onclick = () => {
      restoreExpertPanel(state);
      active = null;
      state.context.onClose?.();
    };
    root.querySelectorAll("[data-inline-mode]").forEach(button => {
      button.onclick = () => open(button.dataset.inlineMode, state.context);
    });
    root.querySelector("[data-inline-files]").onchange = event => {
      const names = [...event.target.files].map(file => file.name);
      root.querySelector("[data-inline-files-name]").textContent = names.length ? names.join("、") : "未选择文件";
    };
    root.querySelector("[data-inline-generate]").onclick = () => generate(state);
    root.querySelector("[data-inline-edit]").onclick = () => {
      state.editing = true;
      render(state);
    };
    root.querySelector("[data-inline-save]").onclick = () => save(state);
  }

  async function load(state) {
    try {
      const url = `/api/projects/${encodeURIComponent(state.context.projectId)}/parts/${encodeURIComponent(state.context.part.part_id)}/${state.mode}`;
      const data = await jsonFetch(url);
      if (active !== state) return;
      if (state.mode === "process") {
        state.plan = data.plan;
        state.validation = data.validation;
      } else {
        state.analysis = data.analysis;
        state.summary = data.summary;
        const quantity = state.root.querySelector("[data-inline-quantity]");
        if (quantity && state.analysis?.quantity) quantity.value = state.analysis.quantity;
      }
      render(state);
      setStatus(state, state.mode === "process"
        ? (state.plan ? "已加载工艺路线。可编辑或重新生成。" : "尚未生成工艺路线。")
        : (state.analysis ? "已加载成本分析。可编辑或重新生成。" : "尚未生成成本分析。"));
    } catch (error) {
      if (active !== state) return;
      state.root.querySelector("[data-inline-body]").innerHTML = `<div class="inline-empty error">读取失败：${esc(error.message)}</div>`;
      setStatus(state, `${state.mode === "process" ? "工艺拆解" : "成本分析"}读取失败`, false, true);
    }
  }

  function setStatus(state, message, busy = false, error = false) {
    if (!state.root) return;
    const el = state.root.querySelector("[data-inline-status]");
    el.textContent = message || "";
    el.classList.toggle("error", error);
    el.classList.toggle("busy", busy);
    state.context.status?.(message, busy);
  }

  function renderControls(state) {
    const root = state.root;
    const data = state.mode === "process" ? state.plan : state.analysis;
    const generate = root.querySelector("[data-inline-generate]");
    const edit = root.querySelector("[data-inline-edit]");
    const save = root.querySelector("[data-inline-save]");
    const label = state.mode === "process"
      ? (data ? "重新生成工艺拆解" : "生成工艺拆解")
      : (data ? "重新生成成本分析" : "生成成本分析");
    generate.disabled = state.busy;
    generate.setAttribute("aria-label", state.busy ? `${label}中` : label);
    if (state.busy) {
      generate.setAttribute("aria-busy", "true");
      generate.innerHTML = `<span class="parse-spinner" aria-hidden="true"></span><span>${label}中…</span>`;
    } else {
      generate.removeAttribute("aria-busy");
      generate.textContent = label;
    }
    edit.hidden = !data || state.editing;
    edit.disabled = state.busy || !data;
    save.hidden = !state.editing;
    save.disabled = state.busy;
  }

  function render(state) {
    if (!state.root) return;
    renderControls(state);
    if (state.mode === "process") renderProcess(state);
    else renderCost(state);
  }

  function extraForm(state) {
    const fd = new FormData();
    fd.append("note", state.root.querySelector("[data-inline-note]").value || "");
    for (const file of state.root.querySelector("[data-inline-files]").files) fd.append("attachments", file);
    return fd;
  }

  async function generate(state) {
    if (state.busy) return;
    state.busy = true;
    renderControls(state);
    const title = state.mode === "process" ? "工艺拆解" : "成本分析";
    setStatus(state, `${title}生成中…`, true);
    try {
      let url = `/api/projects/${encodeURIComponent(state.context.projectId)}/parts/${encodeURIComponent(state.context.part.part_id)}/${state.mode}`;
      if (state.mode === "cost") {
        const quantity = Math.max(1, parseInt(state.root.querySelector("[data-inline-quantity]").value, 10) || 1);
        url += `?quantity=${quantity}`;
      }
      const submitted = await jsonFetch(url, { method: "POST", body: extraForm(state) });
      const result = await poll(state, submitted.task_id);
      if (active !== state) return;
      if (state.mode === "process") {
        state.plan = result.plan;
        state.validation = result.validation;
      } else {
        state.analysis = result.analysis;
        state.summary = result.summary;
        if (state.summary?.quantity) state.root.querySelector("[data-inline-quantity]").value = state.summary.quantity;
      }
      state.editing = false;
      render(state);
      setStatus(state, `${title}已完成`, false);
    } catch (error) {
      if (active === state) setStatus(state, `${title}失败：${error.message}`, false, true);
    } finally {
      state.busy = false;
      if (active === state) renderControls(state);
    }
  }

  async function poll(state, taskId) {
    while (true) {
      await sleep(1200);
      const task = await jsonFetch(`/api/projects/${encodeURIComponent(state.context.projectId)}/tasks/${encodeURIComponent(taskId)}`);
      if (task.status === "succeeded") return task.result;
      if (task.status === "failed") throw new Error(task.error || "任务失败");
      if (active === state) setStatus(state, `${state.mode === "process" ? "工艺拆解" : "成本分析"}：${task.progress || "正在处理"}…`, true);
    }
  }

  async function save(state) {
    if (state.busy) return;
    if (state.mode === "process") collectProcessEdits(state);
    else collectCostEdits(state);
    state.busy = true;
    renderControls(state);
    setStatus(state, `保存${state.mode === "process" ? "工艺路线" : "成本分析"}…`, true);
    try {
      const payload = state.mode === "process" ? state.plan : state.analysis;
      const data = await jsonFetch(`/api/projects/${encodeURIComponent(state.context.projectId)}/parts/${encodeURIComponent(state.context.part.part_id)}/${state.mode}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      if (active !== state) return;
      if (state.mode === "process") {
        state.plan = data.plan;
        state.validation = data.validation;
      } else {
        state.analysis = data.analysis;
        state.summary = data.summary;
      }
      state.editing = false;
      render(state);
      setStatus(state, "已保存。", false);
    } catch (error) {
      if (active === state) setStatus(state, `保存失败：${error.message}`, false, true);
    } finally {
      state.busy = false;
      if (active === state) renderControls(state);
    }
  }

  function renderProcess(state) {
    const body = state.root.querySelector("[data-inline-body]");
    const plan = state.plan;
    if (!plan) {
      body.innerHTML = `<div class="inline-empty">尚未生成工艺拆解。点击上方“生成工艺拆解”，AI 将依据当前零件的特征、材料、尺寸编制结构化加工工艺路线。</div>`;
      return;
    }
    const validation = state.validation || {};
    let html = `<div class="inline-process-grid"><div class="inline-col inline-col-left">`;
    html += `<section class="inline-card"><div class="inline-card-title">工艺概览</div>`;
    if (plan.blank) html += `<div class="inline-row"><b>毛坯</b>${esc(plan.blank)}</div>`;
    if (plan.material) html += `<div class="inline-row"><b>材料</b>${esc(plan.material)}</div>`;
    if (plan.summary) html += `<div class="inline-row"><b>工艺方案</b>${esc(plan.summary)}</div>`;
    if (plan.overall_note) html += `<div class="inline-row"><b>备注</b>${esc(plan.overall_note)}</div>`;
    html += `<div class="inline-totals"><span><strong>${validation.step_count ?? (plan.steps || []).length}</strong>工序数</span>`;
    if (validation.total_duration_min != null) html += `<span><strong>${validation.total_duration_min}</strong>合计工时(分钟)</span>`;
    html += `</div>`;
    (validation.warnings || []).forEach(w => { html += `<div class="inline-warn">⚠ ${esc(w)}</div>`; });
    html += `</section><section class="inline-card"><div class="inline-card-title">工艺流程图</div><div class="inline-hint">点击节点定位到工序明细</div>${processFlow(plan.steps || [])}</section></div>`;
    html += `<div class="inline-col inline-col-mid"><section class="inline-card"><div class="inline-card-title">工序明细</div><div class="inline-steps">`;
    (plan.steps || []).forEach((step, index) => { html += processStep(step, index, state.editing); });
    html += `</div></section></div><div class="inline-col inline-col-right"><section class="inline-card"><div class="inline-card-title">待澄清</div>`;
    const questions = plan.open_questions || [];
    if (questions.length) questions.forEach(q => { html += `<div class="inline-question">❓ ${esc(q.field)}：${esc(q.reason)}${q.guess ? `（猜测：${esc(q.guess)}）` : ""}</div>`; });
    else html += `<div class="inline-hint">暂无待澄清问题</div>`;
    html += `</section></div></div>`;
    body.innerHTML = html;
    body.querySelectorAll(".inline-vnode[data-step]").forEach(node => {
      node.onclick = () => {
        const target = body.querySelector(`#inline-step-${CSS.escape(node.dataset.step)}`);
        if (!target) return;
        target.scrollIntoView({ behavior: "smooth", block: "center" });
        target.classList.add("hl");
        setTimeout(() => target.classList.remove("hl"), 1200);
      };
    });
  }

  function processFlow(steps) {
    if (!steps.length) return `<div class="inline-hint">暂无工序</div>`;
    let html = `<div class="inline-vflow">`;
    steps.forEach(step => {
      const duration = step.duration_min != null ? ` · ${step.duration_min}分` : "";
      html += `<div class="inline-vnode ${nodeCategory(step.type)}" data-step="${attr(step.step_no)}"><div class="inline-vno">${esc(step.step_no)}</div><div><div class="inline-vname">${esc(step.name || "")}</div><div class="inline-vtype">${TYPE_LABEL[step.type] || esc(step.type)}${duration}</div></div></div>`;
    });
    return `${html}</div>`;
  }

  function nodeCategory(type) {
    if (type === "blank") return "cat-blank";
    if (type === "heat_treat" || type === "surface") return "cat-thermal";
    if (type === "inspection") return "cat-inspect";
    return "cat-machining";
  }

  function processStep(step, index, editing) {
    const confidence = Number(step.confidence || 0);
    if (editing) {
      const options = TYPES.map(type => `<option value="${type}" ${type === step.type ? "selected" : ""}>${TYPE_LABEL[type]}</option>`).join("");
      return `<div class="inline-step ${confClass(confidence)}" id="inline-step-${attr(step.step_no)}" data-inline-step data-i="${index}"><div class="inline-sno">${esc(step.step_no)}</div><div class="inline-sbody"><div class="inline-edit-grid">` +
        `<label>工序号</label><input data-f="step_no" type="number" value="${attr(step.step_no)}"/>` +
        `<label>名称</label><input data-f="name" value="${attr(step.name || "")}"/>` +
        `<label>类型</label><select data-f="type">${options}</select>` +
        `<label>内容</label><textarea data-f="description" rows="2">${esc(step.description || "")}</textarea>` +
        `<label>设备</label><input data-f="equipment" value="${attr(step.equipment || "")}"/>` +
        `<label>工装</label><input data-f="fixture" value="${attr(step.fixture || "")}"/>` +
        `<label>刀具/量具</label><input data-f="tooling" value="${attr(step.tooling || "")}"/>` +
        `<label>参数</label><input data-f="params" value="${attr(step.params || "")}"/>` +
        `<label>质量要求</label><input data-f="quality" value="${attr(step.quality || "")}"/>` +
        `<label>工时(分)</label><input data-f="duration_min" type="number" step="0.1" value="${step.duration_min != null ? attr(step.duration_min) : ""}"/>` +
        `<label>依赖工序号</label><input data-f="depends_on" value="${attr((step.depends_on || []).join(","))}"/>` +
        `</div></div></div>`;
    }
    const kv = (key, value) => value ? `<div><span class="inline-key">${key}:</span> ${esc(value)}</div>` : "";
    return `<div class="inline-step ${confClass(confidence)}" id="inline-step-${attr(step.step_no)}"><div class="inline-sno">${esc(step.step_no)}</div><div class="inline-sbody"><div class="inline-step-title">${esc(step.name || "")}<span class="inline-type">${TYPE_LABEL[step.type] || esc(step.type)}</span><span class="inline-confidence">置信 ${Math.floor(confidence * 100)}%</span></div>` +
      (step.description ? `<div class="inline-description">${esc(step.description)}</div>` : "") +
      `<div class="inline-step-grid">${kv("设备", step.equipment)}${kv("工装", step.fixture)}${kv("刀具/量具", step.tooling)}${kv("参数", step.params)}${kv("质量", step.quality)}${step.duration_min != null ? `<div><span class="inline-key">工时:</span> ${esc(step.duration_min)} 分</div>` : ""}</div>` +
      ((step.depends_on || []).length ? `<div class="inline-dep">前序依赖：工序 ${step.depends_on.join("、")}</div>` : "") +
      (step.note ? `<div class="inline-dep">备注：${esc(step.note)}</div>` : "") + `</div></div>`;
  }

  function collectProcessEdits(state) {
    state.root.querySelectorAll("[data-inline-step][data-i]").forEach(element => {
      const step = state.plan.steps[Number(element.dataset.i)];
      element.querySelectorAll("[data-f]").forEach(input => {
        const field = input.dataset.f;
        const value = input.value;
        if (field === "step_no") step.step_no = parseInt(value, 10) || step.step_no;
        else if (field === "duration_min") step.duration_min = value.trim() === "" ? null : parseFloat(value);
        else if (field === "depends_on") step.depends_on = value.split(",").map(item => parseInt(item.trim(), 10)).filter(number => !Number.isNaN(number));
        else step[field] = value.trim() === "" ? null : value;
      });
    });
    state.plan.steps.sort((a, b) => a.step_no - b.step_no);
  }

  function renderCost(state) {
    const body = state.root.querySelector("[data-inline-body]");
    const analysis = state.analysis;
    if (!analysis) {
      body.innerHTML = `<div class="inline-empty">尚未生成成本分析。设定批量后点击上方“生成成本分析”，AI 将联网检索材料、外购、加工等行情并拆解结构化成本。</div>`;
      return;
    }
    const summary = state.summary || {};
    const currency = summary.currency || analysis.currency || "CNY";
    let html = `<div class="inline-cost-content"><section class="inline-card"><div class="inline-card-title">成本概览</div><div class="inline-cost-total"><strong>${money(summary.computed_total)}</strong><span>元 / 件（${esc(currency)}）</span><em>核算批量 ${analysis.quantity || 1} 件</em></div>`;
    if (analysis.summary) html += `<div class="inline-row">${esc(analysis.summary)}</div>`;
    const byCategory = summary.by_category || {};
    const values = Object.values(byCategory).map(Number);
    const max = Math.max(1, ...values);
    const categories = Object.keys(byCategory).sort((a, b) => byCategory[b] - byCategory[a]);
    if (categories.length) {
      html += `<div class="inline-cat-bars">`;
      categories.forEach(category => { html += `<div class="inline-cat-bar"><div><span>${esc(CAT_LABEL[category] || category)}</span><span>${money(byCategory[category])} 元</span></div><i><b style="width:${Math.floor(Number(byCategory[category]) / max * 100)}%"></b></i></div>`; });
      html += `</div>`;
    }
    (summary.warnings || []).forEach(w => { html += `<div class="inline-warn">⚠ ${esc(w)}</div>`; });
    html += `</section><section class="inline-card"><div class="inline-card-title">成本明细</div><div class="inline-cost-table-wrap"><table class="inline-cost-table"><thead><tr><th>类别</th><th>分项</th><th>计算依据</th><th>数量</th><th>单位</th><th>单价</th><th>金额(元)</th><th>来源</th><th>置信</th></tr></thead><tbody>`;
    (analysis.items || []).forEach((item, index) => { html += costItemRow(item, index, state.editing); });
    html += `</tbody></table></div>${state.editing ? `<div class="inline-hint">提示：保存后平台会按数量×单价重算金额与合计。</div>` : ""}</section>`;
    if ((analysis.price_references || []).length) {
      html += `<section class="inline-card"><div class="inline-card-title">价格依据（联网检索）</div>`;
      analysis.price_references.forEach(reference => { html += `<div class="inline-reference"><b>${esc(reference.item)}</b> — <strong>${esc(reference.price)}</strong><small>${esc(reference.source || "")}${reference.date ? ` · ${esc(reference.date)}` : ""}${reference.url ? ` · <a href="${attr(reference.url)}" target="_blank" rel="noopener">查看来源 ↗</a>` : ""}</small></div>`; });
      html += `</section>`;
    }
    if ((analysis.search_sources || []).length) {
      html += `<section class="inline-card"><div class="inline-card-title">检索来源（可点击核查）</div>`;
      analysis.search_sources.forEach(source => { html += `<div class="inline-source">🔗 <a href="${attr(source.url)}" target="_blank" rel="noopener">${esc(source.title || source.url)}</a></div>`; });
      html += `</section>`;
    }
    if ((analysis.assumptions || []).length || (analysis.open_questions || []).length) {
      html += `<section class="inline-card"><div class="inline-card-title">估算假设 / 待澄清</div>`;
      (analysis.assumptions || []).forEach(item => { html += `<div class="inline-assumption">• ${esc(item)}</div>`; });
      (analysis.open_questions || []).forEach(q => { html += `<div class="inline-question">❓ ${esc(q.field)}：${esc(q.reason)}${q.guess ? `（猜测：${esc(q.guess)}）` : ""}</div>`; });
      html += `</section>`;
    }
    html += `</div>`;
    body.innerHTML = html;
  }

  function costItemRow(item, index, editing) {
    const confidence = Number(item.confidence || 0);
    if (editing) {
      const options = CATS.map(category => `<option value="${category}" ${category === item.category ? "selected" : ""}>${CAT_LABEL[category]}</option>`).join("");
      return `<tr data-inline-cost-item data-i="${index}"><td><select data-f="category">${options}</select></td><td><input data-f="name" value="${attr(item.name || "")}"/></td><td><input data-f="basis" value="${attr(item.basis || "")}"/></td><td><input data-f="quantity" type="number" step="any" value="${item.quantity != null ? attr(item.quantity) : ""}"/></td><td><input data-f="unit" value="${attr(item.unit || "")}"/></td><td><input data-f="unit_price" type="number" step="any" value="${item.unit_price != null ? attr(item.unit_price) : ""}"/></td><td><input data-f="amount" type="number" step="any" value="${item.amount != null ? attr(item.amount) : ""}"/></td><td><input data-f="source" value="${attr(item.source || "")}"/></td><td>${Math.floor(confidence * 100)}%</td></tr>`;
    }
    return `<tr><td><span class="inline-cat-tag">${esc(CAT_LABEL[item.category] || item.category)}</span></td><td>${esc(item.name)}</td><td class="source">${esc(item.basis || "")}</td><td class="number">${item.quantity != null ? esc(item.quantity) : ""}</td><td>${esc(item.unit || "")}</td><td class="number">${item.unit_price != null ? money(item.unit_price) : ""}</td><td class="number amount">${item.amount != null ? money(item.amount) : ""}</td><td class="source">${esc(item.source || "")}</td><td class="number">${Math.floor(confidence * 100)}%</td></tr>`;
  }

  function collectCostEdits(state) {
    state.root.querySelectorAll("[data-inline-cost-item][data-i]").forEach(element => {
      const item = state.analysis.items[Number(element.dataset.i)];
      element.querySelectorAll("[data-f]").forEach(input => {
        const field = input.dataset.f;
        const value = input.value;
        if (["quantity", "unit_price", "amount"].includes(field)) item[field] = value.trim() === "" ? null : parseFloat(value);
        else item[field] = value.trim() === "" ? (field === "category" ? "other" : null) : value;
      });
    });
  }

  window.CadInlineAnalysis = {
    open,
    reset: () => {
      restoreExpertPanel(active);
      active = null;
    },
  };
})();

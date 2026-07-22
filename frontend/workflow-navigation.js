/*
 * 流程栏统一导航。页面的流程栏保留原有 DOM 和样式；本文件只补充可点击、
 * 展开/收起和基于项目真实状态的跳转校验。
 */
(() => {
  'use strict';

  const stages = {
    1: ['1.1 创建', '1.2 确认', '1.3 审核'],
    2: ['2.1 图纸解析', '2.2 材料定性', '2.3 工艺路径', '2.4 洁净管控', '2.5 组装检测', '2.6 产能评估'],
    3: ['3.1 汇总结果', '3.2 审核报告', '3.3 发布报告'],
  };
  const stepKey = {
    '1.1':'create', '1.2':'confirm', '1.3':'review', '2.1':'drawing',
    '2.2':'material', '2.3':'manufacturing', '2.4':'cleaning', '2.5':'assembly', '2.6':'production',
    '3.1':'summary', '3.2':'reportReview', '3.3':'publish',
  };
  let cachedProject = null;
  let loadingProject = null;

  function projectId() {
    const q = new URLSearchParams(location.search).get('project');
    return q || localStorage.getItem('cad_engine_project_id') || localStorage.getItem('currentProject') || '';
  }
  function authHeaders() {
    const token = localStorage.getItem('authToken') || localStorage.getItem('cad_engine_token');
    return token ? { Authorization: `Bearer ${token}` } : {};
  }
  function notify(message, error = false) {
    document.querySelectorAll('.workflow-nav-toast').forEach((el) => el.remove());
    const el = document.createElement('div');
    el.className = `workflow-nav-toast${error ? ' error' : ''}`;
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3600);
  }
  function labelCode(label) { return String(label || '').match(/([123]\.[123456])/u)?.[1] || ''; }
  function stageOfCode(code) { return Number(String(code).split('.')[0]) || 0; }
  function activeStage(root) {
    const active = root.querySelector('.workflow-step.active,.main-step.active,.tp-flow-step.active,.detail-step.active');
    const code = active ? stageOfCode(active.textContent) : 0;
    if (code) return code;
    const text = active?.textContent || '';
    if (text.includes('接受')) return 1;
    if (text.includes('解析')) return 2;
    if (text.includes('输出')) return 3;
    return 0;
  }
  function isActiveStage(node) { return node.classList.contains('active'); }
  function subContainer(wrapper, variant) {
    let el = wrapper.querySelector('.workflow-sub,.sub-labels-row,.tp-flow-substeps');
    if (el) return el;
    el = document.createElement('div');
    el.className = variant === 'tp' ? 'tp-flow-substeps' : variant === 'workflow' ? 'workflow-sub' : 'sub-labels-row';
    wrapper.appendChild(el);
    return el;
  }
  function stageLabel(step) {
    const text = step.textContent || '';
    if (text.includes('接受')) return 1;
    if (text.includes('解析')) return 2;
    if (text.includes('输出')) return 3;
    return 0;
  }
  function hasCode(container, code) {
    return [...container.querySelectorAll('[data-workflow-code],span,button')].some((el) => labelCode(el.textContent) === code);
  }
  function linkMarkup(container, label, variant) {
    const code = labelCode(label);
    const stateClass = container.querySelector(`[data-workflow-code="${code}"]`)?.className || '';
    const b = document.createElement('button');
    b.type = 'button'; b.className = `${variant === 'tp' ? 'tp-flow-substep' : 'sub-label'} workflow-nav-link ${stateClass}`;
    b.dataset.workflowCode = code; b.textContent = label;
    return b;
  }
  function populateSubsteps(container, stage, variant) {
    const current = [...container.children];
    const codeNodes = current.filter((node) => labelCode(node.textContent));
    codeNodes.forEach((node) => {
      if (node.tagName === 'BUTTON') { node.dataset.workflowCode = labelCode(node.textContent); return; }
      const replacement = linkMarkup(container, node.textContent.trim(), variant);
      replacement.className = `${replacement.className} ${node.className || ''}`;
      node.replaceWith(replacement);
    });
    stages[stage].forEach((label, index) => {
      const code = labelCode(label);
      if (hasCode(container, code)) return;
      if (container.children.length && !container.lastElementChild?.classList.contains('workflow-nav-arrow')) {
        const arrow = document.createElement('span');
        arrow.className = variant === 'tp' ? 'tp-flow-subarrow workflow-nav-arrow' : 'sub-label-arrow workflow-nav-arrow';
        arrow.textContent = '→'; container.appendChild(arrow);
      }
      container.appendChild(linkMarkup(container, label, variant));
    });
  }
  function makeTrigger(step, stage) {
    if (step.dataset.workflowBound) return;
    step.dataset.workflowBound = '1'; step.dataset.workflowStage = String(stage);
    step.classList.add('workflow-nav-trigger'); step.setAttribute('role', 'button'); step.tabIndex = 0;
    step.setAttribute('aria-label', `展开${stage}的步骤`);
    const toggle = () => toggleStage(step);
    step.addEventListener('click', toggle);
    step.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggle(); } });
  }
  function getWrapper(step) { return step.closest('.workflow-group,.main-step-wrapper,.tp-flow-wrapper,.workflow-nav-detail-wrapper') || step.parentElement; }
  function toggleStage(step) {
    const wrapper = getWrapper(step); if (!wrapper) return;
    const stage = Number(step.dataset.workflowStage || stageLabel(step));
    const sub = wrapper.querySelector('.workflow-sub,.sub-labels-row,.tp-flow-substeps');
    if (!sub) return;
    const collapsed = sub.classList.toggle('workflow-nav-sub-collapsed');
    // 流程栏采用手风琴：展开当前阶段前，收起另外两个阶段，防止小步骤换行。
    if (!collapsed) {
      const root = step.closest('.workflow-main,.main-workflow,.tp-top-workflow,.detail-main-workflow');
      root?.querySelectorAll('.workflow-sub,.sub-labels-row,.tp-flow-substeps').forEach((other) => {
        if (other === sub) return;
        other.classList.add('workflow-nav-sub-collapsed');
        const otherStep = other.parentElement?.querySelector('.workflow-step,.main-step,.tp-flow-step,.detail-step');
        if (otherStep) otherStep.setAttribute('aria-expanded', 'false');
      });
    }
    step.setAttribute('aria-expanded', String(!collapsed));
  }
  function firstCurrentCode(root) {
    const active = root.querySelector('.workflow-nav-link.active,.sub-label.active,.tp-flow-substep.active');
    return active ? labelCode(active.textContent) : '';
  }
  function bindWorkflow(root) {
    if (!root || root.dataset.workflowNavigationReady === '1') return;
    const isTp = root.classList.contains('tp-top-workflow');
    const isDetail = root.classList.contains('detail-main-workflow');
    const steps = root.querySelectorAll('.workflow-step,.main-step,.tp-flow-step,.detail-step');
    if (!steps.length) return;
    root.dataset.workflowNavigationReady = '1';
    const currentStage = activeStage(root);
    steps.forEach((step) => {
      const stage = stageLabel(step); if (!stage) return;
      let wrapper = getWrapper(step);
      // 早期的拆解/成本页没有每个主步骤的容器；就地包一层，不动原有布局。
      if (isDetail && !wrapper.classList.contains('workflow-nav-detail-wrapper')) {
        const holder = document.createElement('div');
        holder.className = 'workflow-nav-detail-wrapper';
        step.parentNode.insertBefore(holder, step); holder.appendChild(step); wrapper = holder;
      }
      const variant = isTp ? 'tp' : root.querySelector('.workflow-step') ? 'workflow' : 'main';
      const sub = subContainer(wrapper, variant);
      populateSubsteps(sub, stage, variant);
      // 主阶段已经完成时，其下小步骤同样作为完成态展示，避免展开后变成默认灰色。
      if (stage < currentStage) {
        sub.querySelectorAll('.workflow-nav-link').forEach((link) => {
          link.classList.remove('active');
          link.classList.add('done');
        });
      }
      // 当前阶段默认显示；所有前序完成和后续未开始阶段先收起。
      if (stage !== currentStage) sub.classList.add('workflow-nav-sub-collapsed');
      makeTrigger(step, stage);
    });
    root.addEventListener('click', async (event) => {
      const link = event.target.closest('.workflow-nav-link'); if (!link) return;
      event.preventDefault(); event.stopPropagation();
      await navigate(link.dataset.workflowCode || labelCode(link.textContent));
    });
    if (projectId()) loadProject().then((progress) => applyProgress(root, progress));
  }
  function findRoots() {
    document.querySelectorAll('.workflow-main,.main-workflow,.tp-top-workflow,.detail-main-workflow').forEach(bindWorkflow);
  }
  async function loadProject() {
    const id = projectId();
    if (!id) return null;
    if (cachedProject?.id === id) return cachedProject;
    if (loadingProject) return loadingProject;
    loadingProject = Promise.all([
      fetch(`/api/projects/${encodeURIComponent(id)}/workflow`, { headers: authHeaders() }).then((r) => r.ok ? r.json() : null),
      fetch(`/api/projects/${encodeURIComponent(id)}/summary`, { headers: authHeaders() }).then((r) => r.ok ? r.json() : null),
    ]).then(([workflow, aggregate]) => (cachedProject = { id, workflow: workflow || {}, aggregate: aggregate || {} }))
      .catch(() => (cachedProject = { id, workflow: {}, aggregate: {} }))
      .finally(() => { loadingProject = null; });
    return loadingProject;
  }
  function hasStepData(progress, key) {
    const value = progress?.aggregate?.steps?.[key];
    if (!value || typeof value !== 'object') return false;
    const timing = value.timing || {};
    return timing.completed === true || timing.status === 'done' || Object.keys(value).some((k) => !['timing', 'project_id', 'updated_at', 'history'].includes(k) && value[k]);
  }
  function stepFinished(progress, key) {
    const timing = progress?.aggregate?.steps?.[key]?.timing || {};
    return timing.completed === true || timing.status === 'done';
  }
  function setState(node, state, isMain = false) {
    if (!node) return;
    node.classList.remove('active', 'done', 'completed', 'pending', 'workflow-nav-stage-done', 'workflow-nav-stage-active');
    if (state === 'done') node.classList.add('done', 'completed', ...(isMain ? ['workflow-nav-stage-done'] : []));
    else if (state === 'active') node.classList.add('active', ...(isMain ? ['workflow-nav-stage-active'] : []));
    else node.classList.add('pending');
  }
  function applyProgress(root, progress) {
    // 网络失败时保留页面原有状态，避免静态流程栏被错误重置成灰色。
    if (!progress?.workflow?.project) return;
    const req = progress.workflow.requirement || {};
    const report = progress.workflow.report || {};
    const summary = progress.workflow.summary || {};
    const reqStatus = req.status || '';
    const pageStage = activeStage(root);
    const drawingDone = Boolean(progress.aggregate?.ir?.parts?.length);
    const techDone = Boolean(summary.confirmed) || stepFinished(progress, 'production');
    const techStarted = drawingDone || ['material', 'manufacturing', 'cleaning', 'assembly', 'production'].some((key) => hasStepData(progress, key));
    const reportExists = Boolean(report.status);
    // 兼容早期已发布项目：旧数据可能没有 requirement/summary 文档，
    // 但既然已经生成报告，前序阶段在业务上必然已完成。
    const requirementDone = reqStatus === 'approved' || techStarted || techDone || reportExists;
    const technicalDone = techDone || reportExists;
    const stageStates = {
      1: requirementDone ? 'done' : reqStatus ? 'active' : pageStage === 1 ? 'active' : 'pending',
      2: technicalDone ? 'done' : techStarted ? 'active' : pageStage === 2 ? 'active' : 'pending',
      3: report.status === 'published' ? 'done' : reportExists ? 'active' : pageStage === 3 ? 'active' : 'pending',
    };
    const doneCodes = new Set();
    if (requirementDone) ['1.1', '1.2', '1.3'].forEach((code) => doneCodes.add(code));
    else {
      if (reqStatus && reqStatus !== 'draft') doneCodes.add('1.1');
      if (['pending_review', 'approved'].includes(reqStatus)) doneCodes.add('1.2');
    }
    if (technicalDone) ['2.1', '2.2', '2.3', '2.4', '2.5', '2.6'].forEach((code) => doneCodes.add(code));
    else {
      if (drawingDone) doneCodes.add('2.1');
      if (stepFinished(progress, 'material')) doneCodes.add('2.2');
      if (stepFinished(progress, 'manufacturing')) doneCodes.add('2.3');
      if (stepFinished(progress, 'cleaning')) doneCodes.add('2.4');
      if (stepFinished(progress, 'assembly')) doneCodes.add('2.5');
    }
    if (['in_review', 'approved', 'published'].includes(report.status)) doneCodes.add('3.1');
    if (['approved', 'published'].includes(report.status)) doneCodes.add('3.2');
    if (report.status === 'published') doneCodes.add('3.3');

    const mainSteps = [...root.querySelectorAll('.workflow-step,.main-step,.tp-flow-step,.detail-step')];
    mainSteps.forEach((node) => setState(node, stageStates[stageLabel(node)] || 'pending', true));
    root.querySelectorAll('.workflow-nav-link').forEach((node) => {
      const code = node.dataset.workflowCode || labelCode(node.textContent);
      setState(node, doneCodes.has(code) ? 'done' : node.classList.contains('active') ? 'active' : 'pending');
    });
    const connectors = [...root.querySelectorAll('.workflow-connector,.main-connector,.tp-flow-line,.detail-connector')];
    connectors.forEach((connector, index) => {
      const state = stageStates[index + 2] || 'pending';
      connector.classList.remove('active', 'done', 'workflow-nav-connector-active', 'workflow-nav-connector-done');
      if (state === 'done') connector.classList.add('workflow-nav-connector-done');
      else if (state === 'active') connector.classList.add('workflow-nav-connector-active');
    });
  }
  function gate(code, progress) {
    const req = progress?.workflow?.requirement || {};
    const report = progress?.workflow?.report || {};
    const summary = progress?.workflow?.summary || {};
    const status = req.status || '';
    const approved = status === 'approved';
    const readyReq = ['pending_confirmation', 'pending_review', 'approved'].includes(status);
    const readyConfirm = ['pending_review', 'approved'].includes(status);
    const readyDrawing = approved || Boolean(progress?.aggregate?.ir?.parts?.length);
    const readyMaterial = readyDrawing;
    const readyManufacturing = hasStepData(progress, 'material');
    const readyCleaning = hasStepData(progress, 'manufacturing');
    const readyAssembly = hasStepData(progress, 'cleaning');
    const readyProduction = hasStepData(progress, 'assembly');
    const readySummary = hasStepData(progress, 'production') || Boolean(summary?.confirmed_at) || Boolean(report?.id || report?.status);
    const readyReview = ['in_review', 'approved', 'published'].includes(report.status);
    const readyPublish = ['approved', 'published'].includes(report.status);
    const checks = {
      '1.1': [true, ''], '1.2': [readyReq, '请先在 1.1 创建中保存并提交工艺评估需求。'],
      '1.3': [readyConfirm, '请先在 1.2 确认工艺评估需求后再进入审核。'],
      '2.1': [readyDrawing, '请先完成 1.3 审核并通过工艺评估需求。'],
      '2.2': [readyMaterial, '请先完成 2.1 图纸解析并生成解析结果。'],
      '2.3': [readyManufacturing, '请先完成并确认 2.2 材料定性。'],
      '2.4': [readyCleaning, '请先完成并确认 2.3 工艺路径。'],
      '2.5': [readyAssembly, '请先完成并确认 2.4 洁净管控。'],
      '2.6': [readyProduction, '请先完成并确认 2.5 组装检测。'],
      '3.1': [readySummary, '请先完成并确认 2.6 产能评估后再汇总结果。'],
      '3.2': [readyReview, '请先在 3.1 汇总结果中保存并提交评估报告。'],
      '3.3': [readyPublish, '请先完成 3.2 审核报告并获得通过。'],
    };
    return checks[code] || [false, '该流程步骤暂不可进入。'];
  }
  function urlFor(code, id) {
    const q = id ? `?project=${encodeURIComponent(id)}` : '';
    const routes = {
      '1.1': `/requirement-create.html${q}`, '1.2': `/requirement-confirm.html${q}`, '1.3': `/requirement-review.html${q}`,
      '2.1': `/index.html${q}`, '3.1': `/summary.html${q}`, '3.2': `/report-review.html${q}`, '3.3': `/report-publish.html${q}`,
    };
    if (code.startsWith('2.') && code !== '2.1') {
      const techStep = Number(code.split('.')[1]) - 1;
      return `/apps/tech-process/?biz=tech${id ? `&project=${encodeURIComponent(id)}` : ''}&step=${techStep}`;
    }
    return routes[code] || '/home.html';
  }
  async function navigate(code) {
    if (!code) return;
    const id = projectId();
    if (!id && code !== '1.1') { notify('请先在 1.1 创建并保存一个工艺评估需求。', true); return; }
    const progress = await loadProject();
    const [allowed, message] = gate(code, progress);
    if (!allowed) { notify(message, true); return; }
    if (id) { localStorage.setItem('cad_engine_project_id', id); localStorage.setItem('currentProject', id); }
    location.href = urlFor(code, id);
  }
  window.CadWorkflowNavigation = { navigate, refresh: () => { cachedProject = null; findRoots(); } };
  const observer = new MutationObserver(() => findRoots());
  const start = () => { findRoots(); observer.observe(document.body, { childList: true, subtree: true }); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();

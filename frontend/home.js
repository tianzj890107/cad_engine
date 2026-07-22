/* 首页视觉严格对齐「AI工艺_首页_最终版」；清单统一展示真实需求、图纸项目和技术工艺记录。 */
let homeItems = [], homeUser = {}, activeTab = 'mine', keyword = '', page = 1;
const PAGE_SIZE = 6;
// 首页附件在选择后立即以聊天附件卡片展示。FileList 不可直接修改，因此用
// 独立状态保存，并在移除单个文件时回写给对应 input。
let homeModelFile = null;
let homeDocumentFiles = [];
const HOME_ROLE_LABEL = {viewer:'只读用户',engineer:'工艺工程师',process_manager:'工艺技术经理',reviewer:'校核人员',process_director:'工艺技术总监',admin:'系统管理员'};
// 当前“我的清单”保留全部已创建需求单，并额外纳入用户指定的图纸项目。
const HOME_MINE_CREATED_AT = '2026-07-14 11:08:35';

function homeEsc(v) { return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function homeToast(message, error = false) { const el=document.createElement('div'); el.className=`home-toast${error?' error':''}`; el.textContent=message; document.body.append(el); setTimeout(()=>el.remove(),3200); }
function homeStatus(status) {
  return ({draft:['待提交','status-submitted'],pending_confirmation:['待确认','status-confirm'],pending_review:['待审核','status-review'],approved:['已通过','status-completed'],published:['已完成','status-completed'],rejected:['已退回','status-review'],uploaded:['待图纸解析','status-submitted'],parsed:['图纸已解析','status-confirm'],tech_record:['技术工艺已录入','status-completed']})[status] || ['处理中','status-submitted'];
}
function homeProjectStatus(project = {}, record = null) {
  if (record) return 'tech_record';
  if (project.has_ir || project.stages?.parsed) return 'parsed';
  return 'uploaded';
}
function homeVirtualRequirement(project = {}, record = null) {
  const projectId = project.project_id || record?.project_id || '';
  return {
    project_id: projectId,
    requirement_no: record?.code || `PRJ-${projectId}`,
    title: record?.name || project.project_name || project.device_name || project.source_filename || '未命名图纸项目',
    status: homeProjectStatus(project, record),
    created_by: record?.owner || project.owner || 'system',
    created_at: project.created_at || record?.created_at || '',
    data: { customer_project: project.device_name || '' },
  };
}
function mergeHomeItems(requirementItems = [], projects = [], records = []) {
  const projectById = new Map(projects.filter(p => p?.project_id).map(p => [p.project_id, p]));
  const requirementById = new Map(requirementItems
    .filter(item => item?.project?.project_id || item?.requirement?.project_id)
    .map(item => [item.project?.project_id || item.requirement?.project_id, item]));
  const recordByProject = new Map(records.filter(record => record?.project_id).map(record => [record.project_id, record]));
  const projectIds = new Set([...projectById.keys(), ...requirementById.keys(), ...recordByProject.keys()]);
  return [...projectIds].map(projectId => {
    const saved = requirementById.get(projectId);
    const record = recordByProject.get(projectId) || null;
    const project = projectById.get(projectId) || saved?.project || {
      project_id: projectId, owner: record?.owner || 'system', created_at: record?.created_at || '', source_filename: '', stages: {},
    };
    return {
      project,
      requirement: saved?.requirement || homeVirtualRequirement(project, record),
      hasRequirement: Boolean(saved?.requirement),
      techRecord: record,
    };
  }).sort((a, b) => String(b.project.created_at || b.requirement.created_at || '').localeCompare(String(a.project.created_at || a.requirement.created_at || '')));
}
function homeOwner(item) { return item?.project?.owner || item?.requirement?.created_by || 'system'; }
function homeOwnerName(item) { return item?.project?.owner_display_name || homeOwner(item); }
function homeIsMine(item) { return Boolean(homeUser?.username) && homeOwner(item) === homeUser.username; }
function homeCanManage(item) {
  const role = homeUser?.role;
  return role === 'admin' || role === 'process_manager' || (role === 'engineer' && homeOwner(item) === homeUser.username);
}
function homeCacheKey(projectId) { return `cad_engine:last_page:${projectId}`; }
function homeCachedTarget(projectId) {
  const saved = localStorage.getItem(homeCacheKey(projectId));
  if (!saved) return '';
  try {
    const url = new URL(saved, location.origin);
    if (url.origin !== location.origin || url.searchParams.get('project') !== projectId || url.pathname.endsWith('/home.html')) return '';
    return `${url.pathname}${url.search}${url.hash}`;
  } catch { return ''; }
}
function homeTarget(item) {
  const projectId = item.project?.project_id;
  if (!projectId) return 'home.html';
  return homeCachedTarget(projectId) || `requirement-detail.html?project=${encodeURIComponent(projectId)}`;
}
function homeStepHas(value) {
  if (Array.isArray(value)) return value.some(homeStepHas);
  if (value && typeof value === 'object') return Object.entries(value).some(([key, item]) => !['project_id','updated_at','timing','confirmed','confirmed_by','confirmed_at'].includes(key) && homeStepHas(item));
  return typeof value === 'string' ? Boolean(value.trim()) : value !== null && value !== undefined && value !== false;
}
function homeProjectPage(page, projectId) { return `${page}?project=${encodeURIComponent(projectId)}`; }
function homeTechPage(projectId, step) { return `/apps/tech-process/?biz=tech&project=${encodeURIComponent(projectId)}&step=${step}`; }
async function homeCurrentTarget(item) {
  const projectId = item.project?.project_id;
  const cached = homeCachedTarget(projectId);
  if (cached) return cached;
  const [flow, projectData, aggregate] = await Promise.all([
    api(`/api/projects/${encodeURIComponent(projectId)}/workflow`),
    api(`/api/projects/${encodeURIComponent(projectId)}`),
    api(`/api/projects/${encodeURIComponent(projectId)}/summary`).catch(() => ({steps:{}})),
  ]);
  const requirement = flow.requirement || item.requirement || null;
  const status = requirement?.status || '';
  if (['draft','rejected'].includes(status)) return homeProjectPage('requirement-create.html', projectId);
  if (status === 'pending_confirmation') return homeProjectPage('requirement-confirm.html', projectId);
  if (status === 'pending_review') return homeProjectPage('requirement-review.html', projectId);
  const report = flow.report || null;
  if (report) {
    if (report.status === 'in_review') return homeProjectPage('report-review.html', projectId);
    if (['approved','published'].includes(report.status)) return homeProjectPage('report-publish.html', projectId);
    return homeProjectPage('summary.html', projectId);
  }
  const hasIr = Boolean(projectData.ir?.parts?.length || projectData.meta?.has_ir || item.project?.has_ir || item.project?.stages?.parsed);
  if (!hasIr) return homeProjectPage('index.html', projectId);
  const steps = aggregate?.steps || {};
  const next = [['material',1],['manufacturing',2],['cleaning',3],['assembly',4],['production',5]].find(([key]) => !homeStepHas(steps[key] || {}));
  return next ? homeTechPage(projectId, next[1]) : homeProjectPage('summary.html', projectId);
}
async function homeOpenProject(item, card) {
  const projectId = item.project?.project_id;
  if (!projectId) return;
  card?.classList.add('is-opening');
  try { location.href = await homeCurrentTarget(item); }
  catch (error) { homeToast(`无法定位当前步骤：${error.message}，已打开流程详情。`, true); location.href = homeTarget(item); }
}
function homeFiltered() {
  const term=keyword.trim().toLowerCase();
  return homeItems.filter((item)=>{
    const {requirement:r={},project:p={},techRecord}=item;
    const record=techRecord||{};
    const mine=activeTab !== 'mine' || homeIsMine(item);
    const hay=[r.requirement_no,r.title,r.created_by,p.owner,p.owner_display_name,p.project_name,p.source_filename,p.device_name,r.data?.customer_project,r.created_at,record.code,record.name].join(' ').toLowerCase();
    return mine && (!term || hay.includes(term));
  });
}
function renderCards() {
  const all=homeFiltered(), max=Math.max(1,Math.ceil(all.length/PAGE_SIZE)); page=Math.min(page,max);
  const rows=all.slice((page-1)*PAGE_SIZE,page*PAGE_SIZE);
  const grid=document.querySelector('#cardGrid'), info=document.querySelector('#paginationInfo'), controls=document.querySelector('#paginationControls');
  grid.innerHTML=rows.length ? rows.map((item)=>{
    const {requirement:r={},project:p={}}=item;
    const [label,clazz]=homeStatus(r.status), who=homeOwnerName(item), initial=who.slice(0,1).toUpperCase();
    const title=p.project_name||r.title||r.data?.customer_project||p.device_name||p.source_filename||'未命名工艺需求';
    const action=homeCanManage(item)
      ? `<button class="request-edit" type="button" data-edit-project="${homeEsc(p.project_id||'')}">编辑</button>`
      : '<span class="request-readonly">只读</span>';
    return `<article class="request-card" data-target="${homeEsc(homeTarget(item))}" data-project-id="${homeEsc(p.project_id||'')}"><div class="request-card-header"><span class="request-id">${homeEsc(r.requirement_no||`PRJ-${p.project_id||''}`)}</span><span class="request-status ${clazz}">${label}</span></div><div class="request-title" title="${homeEsc(title)}">${homeEsc(title)}</div><div class="request-footer"><div class="request-creator"><span class="creator-avatar">${homeEsc(initial)}</span><span>${homeEsc(who)} · ${homeEsc((r.created_at||p.created_at||'').slice(0,10)||'—')}</span></div>${action}</div></article>`;
  }).join('') : '<div class="empty-grid">暂无匹配的真实工艺需求。上传图纸后可在此查看处理进度。</div>';
  grid.querySelectorAll('[data-target]').forEach(el=>el.onclick=()=>homeOpenProject(homeItems.find(item=>item.project?.project_id===el.dataset.projectId),el));
  grid.querySelectorAll('[data-edit-project]').forEach(button => button.onclick = event => {
    event.stopPropagation();
    homeOpenProjectEditor(homeItems.find(item => item.project?.project_id === button.dataset.editProject));
  });
  const start=all.length ? (page-1)*PAGE_SIZE+1 : 0, end=Math.min(page*PAGE_SIZE,all.length);
  info.textContent=`共 ${all.length} 个结果，当前显示 ${start}–${end}`;
  controls.innerHTML=homePages(max).map(n=>n==='…'?'<button class="page-btn" disabled>…</button>':`<button class="page-btn ${n===page?'active':''}" data-page="${n}">${n}</button>`).join('');
  controls.querySelectorAll('[data-page]').forEach(el=>el.onclick=()=>{page=Number(el.dataset.page);renderCards();});
}
function homePages(max) { if(max<=5)return Array.from({length:max},(_,i)=>i+1); return page<=3?[1,2,3,'…',max]:page>=max-2?[1,'…',max-2,max-1,max]:[1,'…',page,'…',max]; }
function homeOpenProjectEditor(item) {
  if (!item?.project?.project_id || !homeCanManage(item)) {
    homeToast('你没有编辑此项目的权限。', true);
    return;
  }
  document.querySelector('#projectEditorModal')?.remove();
  const projectId = item.project.project_id;
  const initialName = item.project.project_name || item.requirement?.title || item.project.device_name || item.project.source_filename || '';
  document.body.insertAdjacentHTML('beforeend', `<div class="project-editor-mask" id="projectEditorModal" role="dialog" aria-modal="true" aria-labelledby="projectEditorTitle"><form class="project-editor"><div class="project-editor-head"><h2 id="projectEditorTitle">编辑项目</h2><button class="project-editor-close" type="button" aria-label="关闭">×</button></div><label>项目名称<input id="projectEditorName" maxlength="120" required value="${homeEsc(initialName)}" placeholder="请输入项目名称"></label><p class="project-editor-note">原始图纸、工艺结果和处理记录不会被修改。</p><div class="project-editor-actions"><button class="project-delete" type="button">删除项目</button><span></span><button class="project-cancel" type="button">取消</button><button class="project-save" type="submit">保存修改</button></div></form></div>`);
  const modal = document.querySelector('#projectEditorModal');
  const close = () => modal.remove();
  modal.querySelector('.project-editor-close').onclick = close;
  modal.querySelector('.project-cancel').onclick = close;
  modal.onclick = event => { if (event.target === modal) close(); };
  modal.querySelector('form').onsubmit = async event => {
    event.preventDefault();
    const name = modal.querySelector('#projectEditorName').value.trim();
    const save = modal.querySelector('.project-save');
    if (!name) return;
    save.disabled = true;
    try {
      await api(`/api/projects/${encodeURIComponent(projectId)}/management`, {method:'PATCH', body:JSON.stringify({name})});
      item.project.project_name = name;
      if (item.requirement) item.requirement.title = name;
      renderCards();
      close();
      homeToast('项目名称已更新。');
    } catch (error) { homeToast(error.message || '保存项目失败', true); save.disabled = false; }
  };
  modal.querySelector('.project-delete').onclick = async () => {
    if (!confirm(`确认删除项目“${initialName}”？删除后将不再出现在清单中。`)) return;
    const del = modal.querySelector('.project-delete');
    del.disabled = true;
    try {
      await api(`/api/projects/${encodeURIComponent(projectId)}/management`, {method:'DELETE'});
      homeItems = homeItems.filter(row => row.project?.project_id !== projectId);
      renderCards();
      close();
      homeToast('项目已删除。');
    } catch (error) { homeToast(error.message || '删除项目失败', true); del.disabled = false; }
  };
  modal.querySelector('#projectEditorName').focus();
}
function renderHome() {
  document.querySelector('#app').innerHTML=`<header class="header"><a class="logo" href="home.html"><span class="logo-icon">AI</span><span class="logo-text">AI 工艺平台</span></a><div class="user-menu-wrap"><button class="user-menu-trigger" id="userMenu" type="button" aria-expanded="false"><span class="user-id"><b id="userId">加载中…</b><small id="userRole">—</small></span><span class="user-avatar" id="userAvatar">AI</span></button><div class="user-dropdown" id="userDropdown" hidden><a href="account.html">账户设置</a><a href="account.html#users" id="userAdminLink" hidden>用户与权限</a><button type="button" id="homeLogout">退出登录</button></div></div></header><section class="chat-container"><div class="greeting"><h1 class="greeting-title"><span class="highlight">创作无限可能</span>，AI 工艺</h1><p class="greeting-subtitle">今天我能帮你做些什么？</p></div><div class="category-tags"><button class="category-tag active" data-category="ai">⚡ AI工艺</button><button class="category-tag" data-category="hot">🔥 热门问答</button><button class="category-tag" data-category="wiki">📚 企业百科</button></div><section class="unified-input-card"><div class="unified-content"><p class="unified-intro">请使用 @ 引用需求编号，或直接上传文件内容（图纸、文档等），开始解析工艺、生成方案。</p><div class="format-row"><span class="format-label">支持格式：</span><span class="format-label">3D 格式</span><div class="format-tags-row"><span class="format-tag-inline">STEP</span><span class="format-tag-inline">STP</span><span class="format-tag-inline">SLDPRT</span><span class="format-tag-inline">STL</span><span class="format-tag-inline">SAT</span></div><span class="format-label">2D 格式</span><div class="format-tags-row"><span class="format-tag-inline">DWG</span><span class="format-tag-inline">DXF</span><span class="format-tag-inline">PDF</span></div></div></div><div class="upload-names" id="uploadNames" aria-live="polite"></div><textarea id="homePrompt" class="unified-textarea" maxlength="200" placeholder="请输入你的问题或需求描述..." rows="3"></textarea><div class="unified-input-area"><label class="upload-section" for="modelFile"><input id="modelFile" class="upload-input" type="file" accept="image/*,.pdf,.dwg,.dxf,.step,.stp,.sldprt,.stl,.sat"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg><span>上传模型图纸</span></label><label class="upload-section" for="documentFiles"><input id="documentFiles" class="upload-input" type="file" multiple accept="image/*,.pdf,.txt,.md,.csv,.doc,.docx"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg><span>上传需求文档</span></label><span class="char-counter" id="charCounter">0/200</span><button class="send-btn" id="sendBtn" aria-label="创建需求"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg></button></div></section><section class="list-section"><div class="list-header"><div class="list-tabs"><button class="list-tab active" data-list="mine">我的清单</button><button class="list-tab" data-list="all">全部清单</button></div><div class="search-box"><input class="search-input" id="searchInput" placeholder="支持通过客户名&创建人搜索"><button class="search-btn" id="searchBtn">查询</button></div></div><div class="card-grid" id="cardGrid"></div><div class="pagination"><div class="pagination-info" id="paginationInfo"></div><div class="pagination-controls" id="paginationControls"></div></div></section></section>`;
}

// 首页改为左侧工作导航。保留上方旧 renderHome 定义仅为兼容历史版本；此处作为主体内容基底。
function renderHomeBase() {
  document.querySelector('#app').innerHTML=`<div class="home-shell">
    <aside class="home-nav" id="homeNav">
      <div class="home-nav-rail">
        <button class="home-nav-toggle" id="homeNavToggle" aria-label="展开导航" aria-expanded="false">☰</button>
        <button class="home-nav-icon active" data-home-view="mine" title="我的清单">▣</button>
        <button class="home-nav-icon" data-home-view="all" title="全部清单">▤</button>
        <button class="home-nav-icon" id="homeNavCreate" title="新建需求">＋</button><span class="home-nav-spacer"></span>
        <a class="home-nav-icon" href="account.html" title="账户设置">⚙</a>
      </div>
      <div class="home-nav-panel">
        <div class="home-nav-panel-head"><strong>工作台</strong><button class="home-nav-close" id="homeNavClose" aria-label="收起导航">×</button></div>
        <button class="home-nav-create" id="homeNavCreatePanel">＋ 创建工艺评估需求</button>
        <label class="home-nav-search"><span>⌕</span><input id="sideSearchInput" placeholder="搜索需求单"></label>
        <div class="home-nav-group"><span>清单</span><button class="home-nav-item active" data-home-view="mine">我的清单</button><button class="home-nav-item" data-home-view="all">全部清单</button></div>
        <div class="home-nav-group home-nav-recent"><span>最近项目</span><div id="sideProjectList"><p class="home-nav-empty">正在加载…</p></div></div>
        <div class="home-nav-account"><div class="home-nav-account-avatar" id="sideUserAvatar">AI</div><div><b id="sideUserName">加载中…</b><small id="sideUserRole">—</small></div><a href="account.html" title="账户设置">›</a></div>
        <div class="home-nav-account-actions"><a href="account.html">账户设置</a><a href="account.html#users" id="sideUserAdminLink" hidden>用户与权限</a><button type="button" id="homeLogout">退出登录</button></div>
      </div>
    </aside>
    <main class="page-container home-main"><section class="chat-container">
      <div class="greeting"><h1 class="greeting-title"><span class="highlight">创作无限可能</span>，AI 工艺</h1><p class="greeting-subtitle">今天我能帮你做些什么？</p></div>
      <div class="category-tags"><button class="category-tag active" data-category="ai">⚡ AI工艺</button><button class="category-tag" data-category="hot">🔥 热门问答</button><button class="category-tag" data-category="wiki">📚 企业百科</button></div>
      <section class="unified-input-card"><div class="unified-content"><p class="unified-intro">请使用 @ 引用需求编号，或直接上传文件内容（图纸、文档等），开始解析工艺、生成方案。</p><div class="format-row"><span class="format-label">支持格式：</span><span class="format-label">3D 格式</span><div class="format-tags-row"><span class="format-tag-inline">STEP</span><span class="format-tag-inline">STP</span><span class="format-tag-inline">SLDPRT</span><span class="format-tag-inline">STL</span><span class="format-tag-inline">SAT</span></div><span class="format-label">2D 格式</span><div class="format-tags-row"><span class="format-tag-inline">DWG</span><span class="format-tag-inline">DXF</span><span class="format-tag-inline">PDF</span></div></div></div><div class="upload-names" id="uploadNames" aria-live="polite"></div><textarea id="homePrompt" class="unified-textarea" maxlength="200" placeholder="请输入你的问题或需求描述..." rows="3"></textarea><div class="unified-input-area"><label class="upload-section" for="modelFile"><input id="modelFile" class="upload-input" type="file" accept="image/*,.pdf,.dwg,.dxf,.step,.stp,.sldprt,.stl,.sat"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg><span>上传模型图纸</span></label><label class="upload-section" for="documentFiles"><input id="documentFiles" class="upload-input" type="file" multiple accept="image/*,.pdf,.txt,.md,.csv,.doc,.docx"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg><span>上传需求文档</span></label><span class="char-counter" id="charCounter">0/200</span><button class="send-btn" id="sendBtn" aria-label="创建需求"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg></button></div></section>
      <section class="list-section"><div class="list-header"><div class="list-tabs"><button class="list-tab active" data-list="mine">我的清单</button><button class="list-tab" data-list="all">全部清单</button></div><div class="search-box"><input class="search-input" id="searchInput" placeholder="支持通过客户名&创建人搜索"><button class="search-btn" id="searchBtn">查询</button></div></div><div class="card-grid" id="cardGrid"></div><div class="pagination"><div class="pagination-info" id="paginationInfo"></div><div class="pagination-controls" id="paginationControls"></div></div></section>
    </section></main></div>`;
}

function renderHome() {
  renderHomeBase();
  const nav = document.querySelector('#homeNav');
  nav.innerHTML=`<div class="cpq-nav-collapsed" id="homeNavCollapsed">
    <button class="cpq-nav-logo" id="homeNavToggle" aria-label="展开侧栏" aria-expanded="false">✦</button>
    <button class="cpq-nav-icon active" data-home-view="mine"><span>▣</span><b>我的清单</b></button>
    <button class="cpq-nav-icon" id="homeNavCreate"><span>＋</span><b>新建需求</b></button>
    <button class="cpq-nav-icon" id="homeNavHistory"><span>◷</span><b>历史对话</b></button>
    <i class="cpq-nav-divider"></i>
    <button class="cpq-nav-icon" data-home-view="all"><span>⊞</span><b>全部清单</b></button>
    <button class="cpq-nav-icon" id="homeNavSearch"><span>⌕</span><b>搜索</b></button>
    <i class="cpq-nav-grow"></i>
    <button class="cpq-nav-icon" id="homeNavSettings"><span>⚙</span><b>模型设置</b></button>
    <i class="cpq-nav-divider"></i>
    <a class="cpq-nav-icon" href="account.html"><span>◉</span><b>账户设置</b></a>
  </div>
  <div class="cpq-nav-expanded" id="homeNavExpanded">
    <div class="cpq-nav-search"><label><span>⌕</span><input id="sideSearchInput" placeholder="搜索…"><kbd>⌘K</kbd></label></div>
    <div class="cpq-nav-user"><div class="cpq-avatar" id="sideUserAvatar">AI</div><div><strong id="sideUserName">加载中…</strong><small id="sideUserRole">—</small></div></div>
    <div class="cpq-nav-actions"><button id="homeNavCreatePanel" class="cpq-action active"><span>＋</span>新建需求<kbd>⌘K</kbd></button><button class="cpq-action" data-home-view="mine"><span>▣</span>我的清单</button><button class="cpq-action" data-home-view="all"><span>⊞</span>全部清单</button><button class="cpq-action" id="homeNavSettingsPanel"><span>⚙</span>模型与 API<i>›</i></button></div>
    <i class="cpq-panel-divider"></i><div class="cpq-section-title">历史对话</div><div class="cpq-nav-history" id="sideProjectList"><p class="home-nav-empty">正在加载…</p></div>
    <div class="cpq-nav-footer"><div class="cpq-avatar" id="sideFooterAvatar">AI</div><span id="sideFooterUser">加载中…</span><a href="account.html" aria-label="账户设置">›</a><button id="homeLogout" title="退出登录">↪</button></div>
  </div>`;
  document.querySelector('#app').insertAdjacentHTML('beforeend', `<div class="home-llm-mask" id="homeLlmMask" hidden><section class="home-llm-dialog" role="dialog" aria-modal="true" aria-labelledby="homeLlmTitle"><header><h2 id="homeLlmTitle">模型设置</h2><button type="button" id="homeLlmClose" aria-label="关闭">×</button></header><div class="home-llm-body"><p class="home-llm-status" id="homeLlmStatus">正在读取服务配置…</p><label>LLM 提供商<select id="homeLlmProvider" disabled><option value="qwen">Qwen / 阿里云百炼</option></select></label><label>视觉模型（图纸解析）<input id="homeLlmVision" placeholder="qwen3-vl-flash"></label><label>文本模型（文档/分析）<input id="homeLlmText" placeholder="qwen-plus"></label><label>联网检索模型<input id="homeLlmWeb" placeholder="qwen-plus"></label><label>API Base URL<input id="homeLlmBase" placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1"></label><label>API Key <small>留空即不修改当前密钥</small><input id="homeLlmKey" type="password" autocomplete="new-password" placeholder="••••••••"></label><p class="home-llm-note" id="homeLlmNote"></p></div><footer><button type="button" id="homeLlmCancel">取消</button><button type="button" class="primary" id="homeLlmSave">保存并立即生效</button></footer></section></div>`);
}

function homeFileIcon(kind) {
  return kind === 'model'
    ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 4 7.5v9L12 21l8-4.5v-9L12 3Z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/></svg>'
    : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8L14 2Z"/><path d="M14 2v6h6M8 13h8M8 17h6"/></svg>';
}
function homeFileSize(size) {
  if (!Number.isFinite(size)) return '';
  return size < 1024 * 1024 ? `${Math.max(1, Math.round(size / 1024))} KB` : `${(size / 1024 / 1024).toFixed(1)} MB`;
}
function syncHomeDocumentInput() {
  const input = document.querySelector('#documentFiles');
  const transfer = new DataTransfer();
  homeDocumentFiles.forEach(file => transfer.items.add(file));
  input.files = transfer.files;
}
function renderHomeAttachments() {
  const container = document.querySelector('#uploadNames');
  if (!container) return;
  const entries = [
    ...(homeModelFile ? [{kind:'model', index:0, file:homeModelFile, label:'模型图纸'}] : []),
    ...homeDocumentFiles.map((file, index) => ({kind:'document', index, file, label:'技术文档'})),
  ];
  container.classList.toggle('has-files', entries.length > 0);
  container.innerHTML = entries.map(({kind, index, file, label}) => `<div class="home-file-chip" title="${homeEsc(file.name)}"><span class="home-file-icon ${kind}">${homeFileIcon(kind)}</span><span class="home-file-meta"><span class="home-file-name">${homeEsc(file.name)}</span><span class="home-file-type">${label}${file.size ? ` · ${homeFileSize(file.size)}` : ''}</span></span><button class="home-file-remove" type="button" data-kind="${kind}" data-index="${index}" aria-label="移除 ${homeEsc(file.name)}">×</button></div>`).join('');
  container.querySelectorAll('.home-file-remove').forEach(button => {
    button.onclick = () => {
      if (button.dataset.kind === 'model') {
        homeModelFile = null;
        document.querySelector('#modelFile').value = '';
      } else {
        homeDocumentFiles.splice(Number(button.dataset.index), 1);
        syncHomeDocumentInput();
      }
      renderHomeAttachments();
    };
  });
}
function bindHome() {
  const nav = document.querySelector('#homeNav'), navToggle = document.querySelector('#homeNavToggle');
  const collapsed = document.querySelector('#homeNavCollapsed'), expanded = document.querySelector('#homeNavExpanded');
  const toggleNav = open => {
    nav.classList.toggle('is-open', open);
    collapsed.classList.toggle('hidden', open); expanded.classList.toggle('open', open);
    navToggle.setAttribute('aria-expanded', String(open));
  };
  navToggle.onclick = () => toggleNav(!nav.classList.contains('is-open'));
  document.addEventListener('click', event => { if (!nav.contains(event.target) && nav.classList.contains('is-open')) toggleNav(false); });
  const switchHomeView = view => {
    activeTab=view; page=1;
    document.querySelectorAll('.list-tab').forEach(x=>x.classList.toggle('active', x.dataset.list===view));
    document.querySelectorAll('[data-home-view]').forEach(x=>x.classList.toggle('active', x.dataset.homeView===view));
    renderCards();
  };
  document.querySelectorAll('[data-home-view]').forEach(el=>el.onclick=()=>switchHomeView(el.dataset.homeView));
  const goCreate = () => { location.href='requirement-create.html'; };
  document.querySelector('#homeNavCreate').onclick=goCreate;
  document.querySelector('#homeNavCreatePanel').onclick=goCreate;
  document.querySelector('#homeNavHistory').onclick=()=>toggleNav(true);
  document.querySelector('#homeNavSearch').onclick=()=>{toggleNav(true);setTimeout(()=>document.querySelector('#sideSearchInput').focus(),0);};
  document.querySelector('#homeNavSettings').onclick=openHomeLlmSettings;
  document.querySelector('#homeNavSettingsPanel').onclick=openHomeLlmSettings;
  document.querySelector('#homeLogout').onclick = () => { localStorage.removeItem('authToken'); localStorage.removeItem('cad_engine_token'); location.href = 'auth.html'; };
  document.querySelectorAll('.category-tag').forEach(el=>el.onclick=()=>{document.querySelectorAll('.category-tag').forEach(x=>x.classList.remove('active'));el.classList.add('active');const cat=el.dataset.category;if(cat!=='ai')homeToast('该能力入口正在接入企业知识库，当前可使用 AI 工艺创建需求。');});
  document.querySelectorAll('.list-tab').forEach(el=>el.onclick=()=>switchHomeView(el.dataset.list));
  document.querySelector('#searchBtn').onclick=()=>{keyword=document.querySelector('#searchInput').value;page=1;renderCards();};
  document.querySelector('#searchInput').addEventListener('keydown',e=>{if(e.key==='Enter')document.querySelector('#searchBtn').click();});
  document.querySelector('#sideSearchInput').addEventListener('input', event=>{keyword=event.target.value;document.querySelector('#searchInput').value=keyword;page=1;renderCards();});
  const prompt=document.querySelector('#homePrompt'), counter=document.querySelector('#charCounter'); prompt.addEventListener('input',()=>{counter.textContent=`${prompt.value.length}/200`;counter.style.color=prompt.value.length>=200?'#ef4444':'';});
  document.querySelector('#modelFile').onchange = event => {
    homeModelFile = event.target.files[0] || null;
    renderHomeAttachments();
  };
  document.querySelector('#documentFiles').onchange = event => {
    const added = [...event.target.files].filter(file => !homeDocumentFiles.some(existing => existing.name === file.name && existing.size === file.size && existing.lastModified === file.lastModified));
    homeDocumentFiles.push(...added);
    syncHomeDocumentInput();
    renderHomeAttachments();
  };
  document.querySelector('#sendBtn').onclick=createFromHome;
  document.querySelector('#homeLlmClose').onclick=closeHomeLlmSettings;
  document.querySelector('#homeLlmCancel').onclick=closeHomeLlmSettings;
  document.querySelector('#homeLlmMask').onclick=event=>{if(event.target.id==='homeLlmMask')closeHomeLlmSettings();};
  document.querySelector('#homeLlmSave').onclick=saveHomeLlmSettings;
}
function renderHomeSidebarProjects() {
  const list=document.querySelector('#sideProjectList');
  if (!list) return;
  const rows=homeItems.slice(0,8);
  list.innerHTML=rows.length ? rows.map(item=>`<button type="button" class="home-nav-project" data-project-id="${homeEsc(item.project?.project_id)}"><span>${homeEsc(item.requirement?.title || item.project?.project_name || item.project?.source_filename || '未命名项目')}</span><small>${homeEsc(homeStatus(item.requirement?.status)[0])}</small></button>`).join('') : '<p class="home-nav-empty">暂无可查看项目</p>';
  list.querySelectorAll('.home-nav-project').forEach(button=>button.onclick=()=>{
    const item=homeItems.find(row=>row.project?.project_id===button.dataset.projectId);
    if (item) homeOpenProject(item);
  });
}

function homeModelList(value) {
  return String(value || '').split(',').map(item=>item.trim()).filter(Boolean);
}
function closeHomeLlmSettings() { document.querySelector('#homeLlmMask').hidden=true; }
async function openHomeLlmSettings() {
  const mask=document.querySelector('#homeLlmMask'), status=document.querySelector('#homeLlmStatus');
  mask.hidden=false; status.textContent='正在读取服务配置…';
  try {
    const settings=await api('/api/llm/settings');
    const editable=Boolean(settings.editable);
    document.querySelector('#homeLlmProvider').value=settings.provider==='qwen'?'qwen':'';
    document.querySelector('#homeLlmVision').value=(settings.vision_models||[settings.model||'']).join(', ');
    document.querySelector('#homeLlmText').value=(settings.text_models||[settings.text_model||'']).join(', ');
    document.querySelector('#homeLlmWeb').value=(settings.web_search_models||[]).join(', ');
    document.querySelector('#homeLlmBase').value=settings.base_url||'';
    document.querySelectorAll('#homeLlmVision,#homeLlmText,#homeLlmWeb,#homeLlmBase,#homeLlmKey').forEach(el=>el.disabled=!editable);
    document.querySelector('#homeLlmSave').hidden=!editable;
    status.textContent=editable?'可修改全局 Qwen 模型池和 API；保存后对新任务立即生效。':'当前模型：'+(settings.model||'—')+'。只有系统管理员可修改全局模型与 API。';
    document.querySelector('#homeLlmNote').textContent=settings.provider==='qwen'?'视觉/文本/联网模型按顺序作为故障或额度耗尽时的候选池。API Key 永不回显。':(settings.reason||'当前提供商由服务器部署配置固定。');
  } catch(error) { status.textContent='读取模型设置失败：'+(error.message||'未知错误'); }
}
async function saveHomeLlmSettings() {
  const save=document.querySelector('#homeLlmSave'), status=document.querySelector('#homeLlmStatus');
  save.disabled=true; status.textContent='正在保存…';
  try {
    const result=await api('/api/llm/settings',{method:'PUT',body:JSON.stringify({api_key:document.querySelector('#homeLlmKey').value.trim(),base_url:document.querySelector('#homeLlmBase').value.trim(),vision_models:homeModelList(document.querySelector('#homeLlmVision').value),text_models:homeModelList(document.querySelector('#homeLlmText').value),web_search_models:homeModelList(document.querySelector('#homeLlmWeb').value)})});
    document.querySelector('#homeLlmKey').value=''; status.textContent=result.message||'已保存并立即生效。'; homeToast('模型与 API 设置已保存。');
  } catch(error) { status.textContent='保存失败：'+(error.message||'未知错误'); } finally { save.disabled=false; }
}
async function createFromHome() {
  const file=homeModelFile, description=document.querySelector('#homePrompt').value.trim();
  if(!file){homeToast('请先上传模型图纸，再创建真实工艺需求。',true);return;}
  const button=document.querySelector('#sendBtn');button.disabled=true;
  button.setAttribute('aria-busy','true');
  try {
    const fd=new FormData();fd.append('file',file);fd.append('note',description);homeDocumentFiles.forEach(f=>fd.append('attachments',f));
    const created=await api('/api/projects',{method:'POST',body:fd});
    const title=file.name.replace(/\.[^.]+$/,'')||'未命名工艺需求';
    const doc={project_id:created.project_id,requirement_no:'',title,status:'draft',data:{title,description,requirement_type:'工艺评估'}};
    try {
      await api(`/api/projects/${created.project_id}/requirement`,{method:'PUT',body:JSON.stringify(doc)});
    } catch (draftError) {
      // 图纸已安全创建时，不应把用户困在首页；1.1 可继续读取图纸并保存完整草稿。
      setProject(created.project_id);
      homeToast(`图纸已创建，但 1.1 草稿预填失败：${draftError.message}；已进入 1.1，可继续填写。`, true);
      location.href=`requirement-create.html?project=${encodeURIComponent(created.project_id)}`;
      return;
    }
    if (homeDocumentFiles.length) {
      try {
        homeToast('正在读取技术文档并自动填充 1.1 草稿…');
        const extraction=await api(`/api/projects/${created.project_id}/requirement/extract-documents`,{method:'POST'});
        if (extraction.task_id) {
          const finished=await waitHomeTask(created.project_id, extraction.task_id);
          const count=finished.result?.filled_fields?.length || 0;
          homeToast(count ? `已从技术文档补充 ${count} 个 1.1 草稿字段。` : '技术文档已解析，没有发现可直接填入的字段。');
        } else if (extraction.skipped) {
          homeToast('技术文档已上传，但未提取到可直接填写的文本。');
        }
      } catch (extractError) {
        // 文档提取失败不撤销已创建的项目与需求草稿，用户仍可在 1.1 手工填写。
        homeToast(`技术文档自动提取未完成：${extractError.message}；已保留文件，可在 1.1 手工补充。`, true);
      }
    }
    setProject(created.project_id);
    location.href=`requirement-create.html?project=${encodeURIComponent(created.project_id)}`;
  } catch(err){homeToast(err.message||'创建需求失败',true);button.disabled=false;button.removeAttribute('aria-busy');}
}

async function waitHomeTask(projectId, taskId) {
  const deadline=Date.now()+210000;
  while (Date.now()<deadline) {
    const task=await api(`/api/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}`);
    if (task.status==='succeeded') return task;
    if (task.status==='failed') throw new Error(task.error||'技术文档解析失败');
    await new Promise(resolve=>setTimeout(resolve,900));
  }
  throw new Error('技术文档解析等待超时；项目和文件已保留，请稍后在 1.1 刷新查看');
}
async function startHome() {
  // 界面先行渲染：即使清单接口暂时失败，也不能让首页成为空白页。
  renderHome();
  bindHome();
  renderCards();
  try {
    const me = await api('/api/me');
    homeUser = me?.user || {};
    document.querySelector('#sideUserName').textContent = homeUser.display_name || homeUser.username || '系统用户';
    document.querySelector('#sideUserRole').textContent = HOME_ROLE_LABEL[homeUser.role] || homeUser.role || '—';
    document.querySelector('#sideUserAvatar').textContent = (homeUser.display_name || homeUser.username || 'AI').slice(0, 1);
    document.querySelector('#sideFooterAvatar').textContent = (homeUser.display_name || homeUser.username || 'AI').slice(0, 1);
    document.querySelector('#sideFooterUser').textContent = homeUser.display_name || homeUser.username || '系统用户';
    // 侧栏精简状态下不必展示管理入口；节点存在时才按管理员权限开放。
    const adminLink = document.querySelector('#sideUserAdminLink');
    if (adminLink) adminLink.hidden = homeUser.role !== 'admin';

    const [requests, projects, techRecords] = await Promise.all([
      api('/api/requirements'),
      api('/api/projects'),
      api('/api/techprocess/records?biz=tech'),
    ]);
    homeItems = mergeHomeItems(requests?.items || [], Array.isArray(projects) ? projects : [], techRecords?.records || []);
    renderCards();
    renderHomeSidebarProjects();
  } catch (err) {
    if (/未登录|令牌|401/.test(err?.message || '')) {
      // 服务重启或认证密钥更新后，旧会话必然失效；明确回到登录页，而不是留下空白首页。
      location.replace('auth.html?next=home.html');
      return;
    }
    homeToast(`首页清单暂时加载失败：${err?.message || '未知错误'}`, true);
    renderCards();
  }
}
startHome();

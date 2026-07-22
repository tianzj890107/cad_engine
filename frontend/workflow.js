/* Shared client for the ten enterprise workflow pages. No action here calls an LLM. */
const qp = new URLSearchParams(location.search);
// 所有流程页共用的可展开、可校验导航；按需加载，不影响旧页面的业务逻辑。
if (!document.querySelector('script[data-workflow-navigation]')) {
  const navCss = document.createElement('link');
  navCss.rel = 'stylesheet'; navCss.href = '/workflow-navigation.css?v=workflow-nav4';
  document.head.appendChild(navCss);
  const navScript = document.createElement('script');
  navScript.src = '/workflow-navigation.js?v=workflow-nav4'; navScript.dataset.workflowNavigation = '1';
  document.head.appendChild(navScript);
}
if (!document.querySelector('script[data-session-guard]')) {
  const sessionGuard = document.createElement('script');
  sessionGuard.src = '/session-guard.js?v=session1'; sessionGuard.dataset.sessionGuard = '1';
  document.head.appendChild(sessionGuard);
}
const projectId = qp.get('project') || localStorage.getItem('cad_engine_project_id') || '';
const authHeaders = () => {
  const token = localStorage.getItem('authToken') || localStorage.getItem('cad_engine_token');
  return token ? {Authorization: `Bearer ${token}`} : {};
};
async function api(url, options = {}) {
  const headers = {...authHeaders(), ...(options.headers || {})};
  if (options.body && !(options.body instanceof FormData) && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  const res = await fetch(url, {...options, headers});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.message || `请求失败 (${res.status})`);
  return data;
}
function esc(value) { return String(value ?? '').replace(/[&<>'"]/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[s])); }
function text(value) { return esc(value).replace(/\n/g, '<br>'); }
function toast(message, ms = 2600) { const el = document.createElement('div'); el.className='toast'; el.textContent=message; document.body.append(el); setTimeout(()=>el.remove(),ms); }
function href(page, id = projectId) { return `${page}${id ? `?project=${encodeURIComponent(id)}` : ''}`; }
function statusLabel(status) { return ({draft:'草稿',pending_confirmation:'待确认',pending_review:'待审核',approved:'已通过',rejected:'已退回',in_review:'审核中',published:'已发布'})[status] || status || '未创建'; }
function statusClass(status) { return ['approved','published'].includes(status) ? 'green' : ['rejected'].includes(status) ? 'orange' : status === 'draft' ? 'gray' : ''; }
function workflow(active, sub = '') {
  const a1 = active === 1, a2 = active === 2, a3 = active === 3;
  const done1 = active > 1, done2 = active > 2;
  const c = (on, done) => done ? 'done' : on ? 'active' : 'pending';
  return `<section class="workflow card"><div class="workflow-main">
    <div class="workflow-group"><div class="workflow-step ${c(a1,done1)}"><i>1</i>接受工艺评估需求</div><div class="workflow-sub"><span class="${sub==='1.1'?'active':''}">1.1 创建</span> → <span class="${sub==='1.2'?'active':''}">1.2 确认</span> → <span class="${sub==='1.3'?'active':''}">1.3 审核</span></div></div>
    <i class="workflow-connector ${active>=2?'active':''}"></i>
    <div class="workflow-group"><div class="workflow-step ${c(a2,done2)}"><i>2</i>解析技术工艺过程</div><div class="workflow-sub"><span class="${sub==='2.1'?'active':''}">2.1 图纸解析</span></div></div>
    <i class="workflow-connector ${active>=3?'active':''}"></i>
    <div class="workflow-group"><div class="workflow-step ${c(a3,false)}"><i>3</i>输出工艺评估结果</div><div class="workflow-sub"><span class="${sub==='3.1'?'active':''}">3.1 汇总</span> → <span class="${sub==='3.2'?'active':''}">3.2 审核</span> → <span class="${sub==='3.3'?'active':''}">3.3 发布</span></div></div>
  </div></section>`;
}
function header() { return `<header class="topbar"><a class="brand" href="${href('home.html','')}"><span class="brand-mark">AI</span><span>AI 工艺平台</span></a><div class="top-links"><a href="${href('home.html','')}">工作台</a><a href="${href('requirement-detail.html')}">需求详情</a><a href="${href('index.html')}">图纸解析</a><span id="userName">加载中…</span><span class="avatar" id="userAvatar">AI</span></div></header>`; }
async function loadMe(){ try { const d=await api('/api/me'); const u=d.user||{}; const n=u.display_name||u.username||'系统'; const el=document.querySelector('#userName'), av=document.querySelector('#userAvatar'); if(el)el.textContent=`${n} · ${u.role||''}`; if(av)av.textContent=n.slice(0,1); return u;}catch{return {};}}
function renderHistory(history) { if (!history?.length) return '<div class="empty">尚无流程留痕</div>'; return `<ul class="history">${history.slice().reverse().map(x=>`<li><time>${esc(x.at)}</time><div><b>${esc(x.action)}</b><div>${esc(x.actor)} ${x.role?`· ${esc(x.role)}`:''}${x.comment?`：${esc(x.comment)}`:''}</div></div></li>`).join('')}</ul>`; }
function setProject(id) { if (id) { localStorage.setItem('cad_engine_project_id', id); localStorage.setItem('currentProject', id); } }

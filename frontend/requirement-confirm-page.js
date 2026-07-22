/* 1.2 确认需求：默认展示本地规则；仅点击“AI 检查”才会调用 Qwen。 */
const cfPid = new URLSearchParams(location.search).get('project') || localStorage.getItem('cad_engine_project_id') || '';
let cfRequirement = null, cfProject = null, cfPrecheck = null;
const cfEsc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));

function cfToast(message, error = false) { const el = document.createElement('div'); el.className = `page-toast${error ? ' error' : ''}`; el.textContent = message; document.body.append(el); setTimeout(() => el.remove(), 3600); }
function cfLabel(key, value) { const maps = {requirement_type:{new:'全新评估',iteration:'迭代评估',change:'技术变更评估'},priority:{urgent:'紧急',high:'高',medium:'中',low:'低'},bu:{bu1:'半导体精密零部件事业部',bu2:'精密装备事业部'},customer_type:{new:'新客户',old:'老客户'},customer_industry:{foundry:'晶圆代工',idm:'IDM',equipment:'半导体设备制造',other:'其他'}}; return maps[key]?.[value] || value || '—'; }
function cfIcon(kind) { return kind === 'pdf' ? '<svg width="48" height="48" viewBox="0 0 24 24" fill="currentColor"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm-1 2l5 5h-5V4zM6 20V4h5v7h7v9H6z"/></svg>' : '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>'; }
function cfPrecheckRows() { return (cfPrecheck?.items || []).map(row => `<tr class="${row.item.match(/^[一二三四五六七]/) ? 'section-row' : ''}${row.item.match(/^3\./) ? ' sub-item' : ''}"><td class="item-name">${cfEsc(row.item)}</td><td><span class="confirm-result"><span class="${row.status === 'ok' ? 'ok' : 'need-info'}">${row.status === 'ok' ? '确认OK' : '需补充'}</span></span></td><td class="confirm-supplement">${cfEsc(row.detail)}</td></tr>`).join(''); }
function cfAiNote() { return cfPrecheck?.engine === 'qwen' ? `Qwen AI 检查 · ${cfPrecheck.model || 'Qwen'} · ${cfPrecheck.checked_at || '刚刚完成'}` : '结构化规则检查，未调用模型'; }
function cfSourceUrl() { const token = localStorage.getItem('authToken') || localStorage.getItem('cad_engine_token'); const base = `/api/projects/${encodeURIComponent(cfPid)}/source`; return token ? `${base}?token=${encodeURIComponent(token)}` : base; }

function cfRender() {
  const data = cfRequirement.data || {};
  const status = cfRequirement.status === 'pending_confirmation' ? '待确认' : cfRequirement.status === 'draft' ? '草稿（请先提交）' : statusLabel(cfRequirement.status);
  const fields = [['需求编号',cfRequirement.requirement_no],['需求名称',cfRequirement.title],['需求类型',cfLabel('requirement_type',data.requirement_type)],['优先级',cfLabel('priority',data.priority)],['BU',cfLabel('bu',data.bu)],['创建人',cfRequirement.created_by],['新旧客户',cfLabel('customer_type',data.customer_type)],['最终客户',data.final_customer_name],['客户行业',cfLabel('customer_industry',data.customer_industry)],['项目编码',data.project_code],['年预测量',data.annual_forecast],['期望交付',data.first_sample_due]];
  document.querySelector('#app').innerHTML = `<section class="workflow-section"><div class="main-workflow"><div class="main-step-wrapper"><div class="main-step active"><div class="main-step-number">1</div><span>接受工艺评估需求</span></div><div class="sub-labels-row"><span class="sub-label completed">1.1 创建</span><span class="sub-label-arrow">→</span><span class="sub-label active">1.2 确认</span><span class="sub-label-arrow">→</span><span class="sub-label">1.3 审核</span></div></div><div class="main-connector"></div><div class="main-step-wrapper"><div class="main-step pending"><div class="main-step-number">2</div><span>解析技术工艺过程</span></div></div><div class="main-connector"></div><div class="main-step-wrapper"><div class="main-step pending"><div class="main-step-number">3</div><span>输出工艺评估结果</span></div></div></div></section><section class="title-section"><div class="title-row"><h1 class="form-title">${cfEsc(cfRequirement.title || '工艺评估需求单')}</h1><span class="status-badge">${cfEsc(status)}</span></div></section><section class="pdf-section"><div class="pdf-header"><div class="pdf-title">${cfIcon('doc')}工艺评估需求表单（点击查看详情）</div></div><div class="pdf-preview"><button class="pdf-thumbnail" id="previewForm" type="button">${cfIcon('pdf')}<span>预览 PDF</span></button><div class="pdf-info"><div class="pdf-info-title">需求基本信息</div><div class="info-list">${fields.map(([label,value]) => `<div class="info-item"><span class="info-label">${label}</span><span class="info-value">${cfEsc(value || '—')}</span></div>`).join('')}</div><div class="pdf-actions"><button class="pdf-action-btn" id="viewForm" type="button">◉ 查看完整表单</button><button class="pdf-action-btn" id="downloadForm" type="button">⇩ 下载 PDF</button><button class="pdf-action-btn ai-check-btn" id="runAiCheck" type="button">⚡ AI 检查</button></div></div></div></section><section class="ai-check-section"><div class="section-title">⚡ AI 检查结果</div><div class="ai-result-box"><div class="ai-result-header"><span class="ai-badge">⚡ 工艺评估需求单确认表</span><span class="ai-note">${cfEsc(cfAiNote())}</span></div><table class="confirm-table"><thead><tr><th>确认项目</th><th>确认结果</th><th>补充说明</th></tr></thead><tbody>${cfPrecheckRows()}</tbody></table></div></section><section class="opinion-section"><div class="section-title">▢ 提交意见</div><textarea id="confirmationNote" class="opinion-textarea" maxlength="3000" placeholder="请输入审批意见...">${cfEsc(cfRequirement.confirmation_note || '')}</textarea><div class="opinion-hint"><span style="color:var(--color-red)">*</span> 必填，最多可输入 3000 字</div></section><footer class="footer-bar"><div class="footer-left"><a class="btn btn-secondary" href="requirement-create.html?project=${encodeURIComponent(cfPid)}">← 上一步</a></div><div class="footer-right"><button class="btn btn-secondary" id="bringAi" type="button">⚡ AI 结果带入</button><button class="btn btn-danger" id="returnDraft" type="button">× 驳回</button><button class="btn btn-primary" id="confirmPass" type="button">✓ 通过</button></div></footer><div class="modal-overlay" id="formModal"><div class="modal"><div class="modal-header"><span class="modal-title">工艺评估需求单</span><button class="modal-close" id="closeModal" type="button">×</button></div><div class="modal-content"><img class="source-preview" src="${cfSourceUrl()}" alt="项目原始图纸"><a class="modal-link" href="requirement-create.html?project=${encodeURIComponent(cfPid)}">打开可编辑的完整需求表单</a></div></div></div>`;
  cfBind();
}

function cfBind() {
  const modal = document.querySelector('#formModal'), open = () => modal.classList.add('active'), close = () => modal.classList.remove('active');
  document.querySelector('#previewForm').onclick = open; document.querySelector('#viewForm').onclick = open; document.querySelector('#closeModal').onclick = close; modal.onclick = event => { if (event.target === modal) close(); };
  document.querySelector('#downloadForm').onclick = () => window.print(); document.querySelector('#runAiCheck').onclick = cfRunAiCheck;
  document.querySelector('#bringAi').onclick = () => { const area = document.querySelector('#confirmationNote'); area.value = area.value ? `${area.value}\n${cfPrecheck.generated_note}` : cfPrecheck.generated_note; area.focus(); };
  document.querySelector('#confirmPass').onclick = () => cfAct('confirm'); document.querySelector('#returnDraft').onclick = () => cfAct('return');
}

async function cfRunAiCheck() {
  const button = document.querySelector('#runAiCheck'); button.disabled = true; button.textContent = '⏳ Qwen 检查中…';
  try { const response = await api(`/api/projects/${cfPid}/requirement/ai-check`, {method:'POST'}); cfPrecheck = response.check; cfRequirement.ai_check = response.check; cfRender(); cfToast('Qwen AI 检查完成，结果已保存。'); }
  catch (err) { button.disabled = false; button.textContent = '⚡ AI 检查'; cfToast(`AI 检查失败：${err.message}`, true); }
}

async function cfAct(kind) {
  const comment = document.querySelector('#confirmationNote').value.trim();
  if (!comment) return cfToast('请填写提交意见。', true);
  if (cfRequirement.status !== 'pending_confirmation') return cfToast('当前需求尚未提交至确认环节，请先返回上一步点击“提交”。', true);
  try { const url = kind === 'confirm' ? `/api/projects/${cfPid}/requirement/confirm` : `/api/projects/${cfPid}/requirement/return-to-draft`; await api(url,{method:'POST',body:JSON.stringify({comment})}); location.href = kind === 'confirm' ? `requirement-review.html?project=${encodeURIComponent(cfPid)}` : `requirement-create.html?project=${encodeURIComponent(cfPid)}`; } catch (err) { cfToast(err.message, true); }
}

async function cfStart() {
  if (!cfPid) { location.href = 'home.html'; return; }
  try { const [req,project,ruleCheck] = await Promise.all([api(`/api/projects/${cfPid}/requirement`),api(`/api/projects/${cfPid}`),api(`/api/projects/${cfPid}/requirement/precheck`)]); cfRequirement = req.requirement; if (!cfRequirement) { location.href = `requirement-create.html?project=${encodeURIComponent(cfPid)}`; return; } cfProject = project; cfPrecheck = cfRequirement.ai_check?.engine === 'qwen' ? cfRequirement.ai_check : ruleCheck; cfRender(); }
  catch (err) { document.querySelector('#app').innerHTML = `<div class="page-toast error" style="position:static">页面加载失败：${cfEsc(err.message)}</div>`; }
}
cfStart();

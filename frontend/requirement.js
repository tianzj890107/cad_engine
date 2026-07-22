function requirementDataFromForm(form) { const fd=new FormData(form); return Object.fromEntries([...fd.entries()].filter(([k])=>k!=='source')); }
function requirementFields(data={}) { const get=k=>esc(data[k]||''); return `<div class="inline-fields"><div class="field"><label>需求名称 <em>*</em></label><input name="title" required placeholder="例如：晶圆搬运吸嘴组件工艺评估" value="${get('title')}"></div><div class="field"><label>需求类型 <em>*</em></label><select name="requirement_type"><option value="工艺评估" ${data.requirement_type==='工艺评估'?'selected':''}>工艺评估</option><option value="可制造性评估">可制造性评估</option><option value="成本与交期评估">成本与交期评估</option></select></div><div class="field"><label>申请部门</label><input name="department" placeholder="请输入申请部门" value="${get('department')}"></div><div class="field"><label>期望完成日期</label><input type="date" name="due_date" value="${get('due_date')}"></div></div><div class="field"><label>需求背景与目标 <em>*</em></label><textarea required name="description" placeholder="说明应用场景、工艺目标和验收关注点">${get('description')}</textarea></div><div class="inline-fields"><div class="field"><label>客户 / 项目名称</label><input name="customer_project" placeholder="请输入客户或项目名称" value="${get('customer_project')}"></div><div class="field"><label>优先级</label><select name="priority"><option value="普通">普通</option><option value="高" ${data.priority==='高'?'selected':''}>高</option><option value="紧急" ${data.priority==='紧急'?'selected':''}>紧急</option></select></div></div><div class="field"><label>技术要求与约束</label><textarea name="technical_requirements" placeholder="材料、尺寸、洁净度、性能、适用标准及其他约束">${get('technical_requirements')}</textarea></div><div class="field"><label>补充说明</label><textarea name="notes" placeholder="可填写交付格式、保密要求、协作人等">${get('notes')}</textarea></div>`; }
async function renderCreate() {
  let existing=null, id=projectId;
  if(id) { try { existing=(await api(`/api/projects/${id}/requirement`)).requirement; } catch(err) { toast(err.message); } }
  const values={...(existing?.data||{}),title:existing?.title||existing?.data?.title||''};
  document.querySelector('#app').innerHTML=`${header()}${workflow(1,'1.1')}<section class="title-card card"><div class="title-row"><h1>创建工艺评估需求</h1><span class="badge ${statusClass(existing?.status)}">${statusLabel(existing?.status||'draft')}</span></div><div class="ai-hint"><strong>✦ AI 工艺助手</strong><span>请提交完整需求与原始图纸。提交后进入人工确认和审核，不会自动调用模型。</span></div></section><form id="requirementForm"><section class="card section"><h2>基本信息</h2>${requirementFields(values)}</section><section class="card section"><h2>原始图纸与佐证资料</h2><div class="input-like"><label for="sourceFile" class="file-button">⌁ 选择图纸文件</label><input class="file-input" id="sourceFile" name="source" type="file" accept="image/*,.pdf,.dwg,.dxf" ${id?'':'required'}><span id="fileName" class="file-name">${id?'已关联现有项目图纸':'支持 PNG、JPG、PDF、DWG、DXF 等格式'}</span><p style="margin:10px 0 0;color:var(--muted);font-size:12px">提交后原图将作为全流程证据链的源文件保存。</p></div></section></form><div class="footer-actions"><div class="footer-actions-inner"><a class="btn secondary" href="home.html">取消</a><div class="button-row"><button class="btn secondary" id="saveDraft">保存草稿</button><button class="btn primary" id="submitRequirement">提交并进入确认</button></div></div></div>`;
  await loadMe();
  document.querySelector('#sourceFile').addEventListener('change',e=>{document.querySelector('#fileName').textContent=e.target.files[0]?.name||'未选择文件';});
  async function persist(submit) {
    const form=document.querySelector('#requirementForm');
    if(submit && !form.reportValidity()) return;
    const values=requirementDataFromForm(form);
    try {
      if(!id) { const file=document.querySelector('#sourceFile').files[0]; if(!file) throw new Error('请先选择原始图纸'); const fd=new FormData(); fd.append('file',file); fd.append('note',values.description||''); const created=await api('/api/projects',{method:'POST',body:fd}); id=created.project_id; setProject(id); }
      const doc={project_id:id,requirement_no:existing?.requirement_no||'',title:values.title||'',status:'draft',data:values};
      await api(`/api/projects/${id}/requirement`,{method:'PUT',body:JSON.stringify(doc)});
      if(submit) { await api(`/api/projects/${id}/requirement/submit-confirmation`,{method:'POST',body:JSON.stringify({comment:'需求创建人已提交，等待需求确认。'})}); location.href=href('requirement-confirm.html',id); }
      else { toast('草稿已保存'); history.replaceState(null,'',href('requirement-create.html',id)); }
    } catch(err) { toast(err.message,4200); }
  }
  document.querySelector('#saveDraft').onclick=()=>persist(false); document.querySelector('#submitRequirement').onclick=()=>persist(true);
}
async function loadRequirementPage() {
  if(location.pathname.endsWith('requirement-create.html')) return renderCreate();
  if(!projectId) { location.href='home.html'; return; }
  const payload=await api(`/api/projects/${projectId}/requirement`); const req=payload.requirement;
  if(!req) { location.href=href('requirement-create.html',projectId); return; }
  if(location.pathname.endsWith('requirement-confirm.html')) return renderConfirm(req);
  if(location.pathname.endsWith('requirement-review.html')) return renderReview(req);
}
loadRequirementPage().catch(e=>{document.querySelector('#app').innerHTML=`<div class="flow-page"><div class="notice">页面加载失败：${esc(e.message)}</div></div>`});

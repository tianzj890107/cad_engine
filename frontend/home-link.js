/* 全部业务页统一：记录每个项目最后停留的页面，并提供与首页入口对称的返回按钮。 */
(() => {
  const path = location.pathname;
  if (path.endsWith('/home.html') || path === '/home.html') return;
  const params = new URLSearchParams(location.search);
  const projectId = params.get('project');
  const homeUrl = path.includes('/apps/') ? '/home.html' : 'home.html';
  const detailUrl = projectId ? `${path.includes('/apps/') ? '/requirement-detail.html' : 'requirement-detail.html'}?project=${encodeURIComponent(projectId)}` : homeUrl;
  const cacheKey = id => `cad_engine:last_page:${id}`;
  const remember = () => {
    if (!projectId) return;
    // 仅存同站点的相对路径，刷新或返回首页后仍能恢复到当前步骤。
    localStorage.setItem(cacheKey(projectId), `${location.pathname}${location.search}${location.hash}`);
  };
  remember();
  const style = document.createElement('style');
  style.textContent = '.global-home-link,.global-back-link{position:fixed;top:18px;z-index:1200;display:inline-flex;align-items:center;gap:7px;padding:9px 13px;border:1px solid #bfdbfe;border-radius:8px;background:#fff;color:#2563eb;font:600 13px/1.2 Inter,"PingFang SC",-apple-system,sans-serif;text-decoration:none;box-shadow:0 3px 12px rgba(30,64,175,.1);transition:.15s}.global-home-link{right:20px}.global-back-link{left:20px}.global-home-link:hover,.global-back-link:hover{border-color:#2563eb;background:#eff6ff;transform:translateY(-1px)}@media(max-width:700px){.global-home-link{top:10px;right:12px;padding:8px 10px;font-size:12px}.global-back-link{top:10px;left:12px;padding:8px 10px;font-size:12px}}';
  document.head.append(style);
  if (!document.querySelector('.global-home-link,.tp-home-link')) {
    const link = document.createElement('a');
    link.className = 'global-home-link';
    link.href = homeUrl;
    link.setAttribute('aria-label', '返回 AI 工艺平台首页');
    link.textContent = '⌂ AI 工艺平台首页';
    link.addEventListener('click', remember);
    document.body.append(link);
  } else {
    document.querySelector('.tp-home-link')?.addEventListener('click', remember);
  }
  // 需求详情已有参考稿左上返回按钮，避免重复；其他业务页统一回到该项目的单列流程时间轴。
  if (projectId && !path.endsWith('/requirement-detail.html') && !document.querySelector('.global-back-link,.detail-back-button')) {
    const back = document.createElement('button');
    back.type = 'button';
    back.className = 'global-back-link';
    back.setAttribute('aria-label', '返回流程详情');
    back.textContent = '← 返回流程详情';
    back.addEventListener('click', () => {
      location.href = detailUrl;
    });
    document.body.append(back);
  }
})();

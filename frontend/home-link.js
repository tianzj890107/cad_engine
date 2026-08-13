/* 全部业务页统一：记录每个项目最后停留的页面，并提供与首页入口对称的返回按钮。 */
(() => {
  const path = location.pathname;
  if (path.endsWith('/home.html') || path === '/home.html') return;
  const params = new URLSearchParams(location.search);
  const projectId = params.get('project');
  const homeUrl = path.includes('/apps/') ? '/home.html' : 'home.html';
  const detailUrl = projectId ? `${path.includes('/apps/') ? '/requirement-detail.html' : 'requirement-detail.html'}?project=${encodeURIComponent(projectId)}` : homeUrl;
  const cacheKey = id => `cad_engine:last_page:${id}`;
  const flowReturnKey = id => `cad_engine:flow_return:${id}`;
  const remember = () => {
    if (!projectId) return;
    // 仅存同站点的相对路径，刷新或返回首页后仍能恢复到当前步骤。
    localStorage.setItem(cacheKey(projectId), `${location.pathname}${location.search}${location.hash}`);
  };
  remember();
  // 进入单列流程页前，保留当前业务步骤，供流程页左下角准确返回。
  // 需求详情页本身不能覆盖该记录，否则会出现“返回到流程页自身”。
  if (projectId && !path.endsWith('/requirement-detail.html')) {
    sessionStorage.setItem(flowReturnKey(projectId), `${location.pathname}${location.search}${location.hash}`);
  }
  const navBackIcon = '<svg fill="none" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" aria-hidden="true"><path d="M0 0h16v16H0z"/><path fill="#8F9299" d="M3.5 7.5h10c.146 0 .266.047.36.14.093.094.14.214.14.36a.487.487 0 0 1-.14.36.487.487 0 0 1-.36.14h-10a.487.487 0 0 1-.36-.14A.487.487 0 0 1 3 8c0-.146.047-.266.14-.36a.487.487 0 0 1 .36-.14Zm.203.5 4.156 4.14c.094.105.141.225.141.36a.48.48 0 0 1-.149.351A.48.48 0 0 1 7.5 13a.522.522 0 0 1-.36-.14l-4.5-4.5A.522.522 0 0 1 2.5 8c0-.135.047-.255.14-.36l4.5-4.5A.522.522 0 0 1 7.5 3a.48.48 0 0 1 .351.148A.48.48 0 0 1 8 3.5a.522.522 0 0 1-.14.36L3.702 8Z" data-follow-fill="#8F9299"/></svg>';
  const navHomeIcon = '<svg fill="none" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" aria-hidden="true"><path d="M0 0h16v16H0z"/><path fill-rule="evenodd" fill="#8F9299" d="M2.5 12.48V6.454c0-.294.057-.56.172-.799.115-.238.287-.45.516-.633l3.667-2.933c.167-.134.346-.235.537-.302.191-.067.394-.1.608-.1.214 0 .417.033.608.1.19.067.37.168.537.302l3.667 2.933c.23.184.401.395.516.633.115.239.172.505.172.799v6.026c0 .253-.045.487-.134.703-.09.216-.224.414-.403.593-.18.179-.377.313-.593.403-.216.09-.45.134-.703.134H4.333c-.253 0-.487-.045-.703-.134a1.827 1.827 0 0 1-.593-.403 1.82 1.82 0 0 1-.403-.593 1.82 1.82 0 0 1-.134-.704Zm1 0c0 .277.07.486.208.624.14.14.348.209.625.209h1.5v-2.5c0-.253.045-.488.135-.704.089-.216.223-.413.402-.593.18-.179.377-.313.593-.402.216-.09.45-.135.704-.135h.666c.253 0 .488.045.704.135.216.09.414.223.593.402.179.18.313.377.402.593.09.216.134.45.134.704v2.5h1.5c.278 0 .487-.07.626-.209.139-.138.208-.347.208-.624V6.454a.828.828 0 0 0-.078-.363.83.83 0 0 0-.235-.288L8.521 2.87C8.347 2.73 8.174 2.66 8 2.66c-.174 0-.347.07-.52.209L3.813 5.803a.828.828 0 0 0-.235.288.828.828 0 0 0-.078.363v6.026Zm5.666.833H6.833v-2.5c0-.278.07-.486.208-.625.14-.14.348-.209.625-.209h.667c.278 0 .486.07.625.209.14.139.208.347.208.625v2.5Z" data-follow-fill="#8F9299"/></svg>';
  const style = document.createElement('style');
  style.textContent = '.global-home-link,.global-back-link{position:fixed;top:18px;z-index:1200;display:inline-flex;align-items:center;gap:6px;padding:9px 13px;border:1px solid #e5e7eb;border-radius:8px;background:#fff;color:#374151;font:600 13px/1.2 Inter,"PingFang SC",-apple-system,sans-serif;text-decoration:none;box-shadow:0 3px 12px rgba(15,23,42,.08);transition:.15s}.global-home-link{right:20px}.global-back-link{left:20px}.global-home-link svg,.global-back-link svg{width:16px;height:16px;flex:0 0 16px}.global-home-link:hover,.global-back-link:hover{border-color:#93c5fd;background:#eff6ff;color:#2563eb;transform:translateY(-1px)}@media(max-width:700px){.global-home-link{top:10px;right:12px;padding:8px 10px;font-size:12px}.global-back-link{top:10px;left:12px;padding:8px 10px;font-size:12px}}';
  document.head.append(style);
  const navBlueStyle = document.createElement('style');
  navBlueStyle.textContent = '.global-home-link,.global-back-link{border-color:#bfdbfe;color:#2563eb}.global-home-link:hover,.global-back-link:hover{border-color:#2563eb;background:#eff6ff;color:#2563eb}';
  document.head.append(navBlueStyle);
  if (!document.querySelector('.global-home-link,.tp-home-link')) {
    const link = document.createElement('a');
    link.className = 'global-home-link';
    link.href = homeUrl;
    link.setAttribute('aria-label', '首页');
    link.innerHTML = `${navHomeIcon.replaceAll('#8F9299', '#2563EB')}<span>首页</span>`;
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
    back.setAttribute('aria-label', '返回');
    back.innerHTML = `${navBackIcon.replaceAll('#8F9299', '#2563EB')}<span>返回</span>`;
    back.addEventListener('click', () => {
      // 优先回到真实来源页面；从地址栏直接打开时再使用项目详情作为兜底。
      const referrer = document.referrer;
      try {
        if (referrer && new URL(referrer, location.href).origin === location.origin) {
          history.back();
          return;
        }
      } catch { /* 使用兜底地址 */ }
      location.href = detailUrl;
    });
    document.body.append(back);
  }

  // 所有带项目上下文的业务页共用一个悬浮 AI 对话；会话保存到项目数据，不随页面切换丢失。
  if (projectId && !document.querySelector('script[data-project-chat]')) {
    const root = path.includes('/apps/') ? '/' : '';
    const css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = `${root}project-chat.css?v=20260810-chat-transition3`;
    document.head.append(css);
    const script = document.createElement('script');
    script.src = `${root}project-chat.js?v=20260810-chat-transition3`;
    script.dataset.projectChat = 'true';
    document.body.append(script);
  }
})();

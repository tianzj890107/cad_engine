/* 已开启认证时，所有业务页统一回到登录页；鉴权关闭时不改变原有本地开发体验。 */
(() => {
  const path = location.pathname;
  if (path.endsWith('/auth.html') || path.endsWith('/account.html')) return;
  const token = localStorage.getItem('authToken') || localStorage.getItem('cad_engine_token') || '';
  fetch('/api/me', {headers: token ? {Authorization:`Bearer ${token}`} : {}})
    .then(async response => {
      if (response.status !== 401) return;
      localStorage.removeItem('authToken'); localStorage.removeItem('cad_engine_token');
      const next = `${location.pathname.replace(/^\//, '')}${location.search}${location.hash}`;
      location.replace(`auth.html?next=${encodeURIComponent(next)}`);
    })
    .catch(() => {});
})();

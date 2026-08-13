(() => {
  const next = (() => { const value = new URLSearchParams(location.search).get('next') || 'home.html'; return value.startsWith('/') || value.includes('://') ? 'home.html' : value; })();
  const message = document.querySelector('#authMessage');
  const setMessage = (text, ok = false) => { message.textContent = text; message.classList.toggle('ok', ok); };
  const request = async (url, options = {}) => { const response = await fetch(url, { ...options, headers: {'Content-Type':'application/json', ...(options.headers || {})} }); const data = await response.json().catch(() => ({})); if (!response.ok) throw new Error(data.detail || '请求失败'); return data; };
  async function init() {
    // 临时演示模式（AUTH_AUTO_ADMIN=true）或完全关闭鉴权时，后端已经注入 admin，
    // 不再让用户看到一个实际上不会被使用的登录表单。
    try {
      const response = await fetch('/api/me');
      if (response.ok) {
        const data = await response.json();
        if (data.auth_enabled === false) {
          location.replace(next);
          return;
        }
      }
    } catch (_) { /* 后端未启动时继续显示正常登录表单 */ }
    document.querySelectorAll('.auth-tab').forEach(button => button.onclick = () => { const register = button.dataset.tab === 'register'; document.querySelectorAll('.auth-tab').forEach(item => item.classList.toggle('active', item === button)); document.querySelector('#loginForm').hidden = register; document.querySelector('#registerForm').hidden = !register; setMessage(''); });
    document.querySelector('#loginForm').onsubmit = async (event) => { event.preventDefault(); const button = event.submitter; button.disabled = true; setMessage(''); try { const data = await request('/api/login', {method:'POST',body:JSON.stringify({username:document.querySelector('#loginUsername').value.trim(),password:document.querySelector('#loginPassword').value})}); localStorage.setItem('authToken', data.token); localStorage.setItem('cad_engine_token', data.token); location.href = next; } catch (error) { setMessage(error.message); button.disabled = false; } };
    document.querySelector('#registerForm').onsubmit = async (event) => { event.preventDefault(); const button = event.submitter; button.disabled = true; setMessage(''); try { const data = await request('/api/register', {method:'POST',body:JSON.stringify({display_name:document.querySelector('#registerName').value.trim(),username:document.querySelector('#registerUsername').value.trim(),password:document.querySelector('#registerPassword').value,requested_role:document.querySelector('#registerRole').value})}); setMessage(data.message || '注册成功，请登录。', true); document.querySelector('[data-tab="login"]').click(); document.querySelector('#loginUsername').value = document.querySelector('#registerUsername').value.trim(); } catch (error) { setMessage(error.message); } finally { button.disabled = false; } };
  }
  init();
})();

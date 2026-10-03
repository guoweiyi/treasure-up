import { validateOrigin } from './policy.js';
const form = document.querySelector('#connect');
const input = document.querySelector('#origin');
const status = document.querySelector('#status');
const submit = document.querySelector('#submit');
const forget = document.querySelector('#forget');
const invoke = window.__TAURI__?.core?.invoke;
const setStatus = message => { status.textContent = message; };
const busy = value => { submit.disabled = forget.disabled = input.disabled = value; };

if (!invoke) {
  busy(true);
  setStatus('请在 Treasure Up 原生客户端中打开此连接页。');
} else {
  invoke('load_connection').then(saved => { if (saved) input.value = saved.origin; }).catch(error => setStatus(String(error)));
}
form.addEventListener('submit', async event => {
  event.preventDefault();
  busy(true);
  setStatus('正在验证服务器身份并连接…');
  try { await invoke('connect_server', { origin: validateOrigin(input.value) }); }
  catch (error) { setStatus(String(error)); }
  finally { busy(false); }
});
forget.addEventListener('click', async () => {
  if (!confirm('清除保存的服务器地址和本客户端登录数据？服务器上的账户与媒体会保留。')) return;
  busy(true);
  try { await invoke('forget_connection'); input.value = ''; setStatus('已清除，可以连接其他服务器。'); }
  catch (error) { setStatus(String(error)); }
  finally { busy(false); }
});

import { validateOrigin } from './policy.js';
const form = document.querySelector('#connect');
const input = document.querySelector('#origin');
const status = document.querySelector('#status');
const submit = document.querySelector('#submit');
const forget = document.querySelector('#forget');
const invoke = window.__TAURI__?.core?.invoke;
const setStatus = message => { status.textContent = message; };
let pending = true;
const busy = value => {
  pending = value;
  submit.disabled = forget.disabled = input.disabled = value;
  form.setAttribute('aria-busy', String(value));
};

// Loading saved settings is an operation too: no late result may replace a new choice.
busy(true);
if (!invoke) {
  setStatus('请在 Treasure Up 原生客户端中打开此连接页。');
} else {
  setStatus('正在读取连接设置…');
  Promise.resolve().then(() => invoke('load_connection')).then(saved => {
    if (saved) input.value = saved.origin;
    setStatus('');
  }).catch(error => setStatus(String(error))).finally(() => busy(false));
}
form.addEventListener('submit', async event => {
  event.preventDefault();
  if (pending || !invoke) return;
  busy(true);
  setStatus('正在验证服务器身份并连接…');
  try { await invoke('connect_server', { origin: validateOrigin(input.value) }); }
  catch (error) { setStatus(String(error)); }
  finally { busy(false); }
});
forget.addEventListener('click', async () => {
  if (pending || !invoke) return;
  if (!confirm('清除保存的服务器地址和本客户端登录数据？服务器上的账户与媒体会保留。')) return;
  busy(true);
  try { await invoke('forget_connection'); input.value = ''; setStatus('地址已清除，已请求系统清理客户端登录数据。'); }
  catch (error) { setStatus(String(error)); }
  finally { busy(false); }
});

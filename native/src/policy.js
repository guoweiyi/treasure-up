// Usability check only. Rust independently enforces the actual trust boundary.
export function validateOrigin(value) {
  const input = value.trim();
  if (input.length > 2048 || /[\u0000-\u001f\u007f\\]/.test(input)) throw Error('服务器地址包含无效字符');
  let url;
  try { url = new URL(input); } catch { throw Error('请输入完整服务器地址'); }
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || input.includes('@')
      || input.includes('?') || input.includes('#') || !['/', '/api', '/api/', '/api/v1', '/api/v1/'].includes(url.pathname)) throw Error('请填写站点地址或 /api/v1 地址，不要包含凭据和参数');
  if (['tauri.localhost', 'ipc.localhost', 'treasure-up.invalid'].includes(url.hostname)) throw Error('该地址是客户端保留地址');
  const loopback = url.hostname === 'localhost' || url.hostname === '[::1]' || /^127\.\d+\.\d+\.\d+$/.test(url.hostname);
  if (url.protocol === 'http:' && !loopback) throw Error('远程服务器必须使用 HTTPS');
  return url.origin;
}

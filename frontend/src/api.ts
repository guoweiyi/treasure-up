import { reactive } from 'vue';
import type { User } from './types';
export const session = reactive<{ user: User | null; csrf: string; ready: boolean }>({
  user: null,
  csrf: '',
  ready: false,
});
export const display = reactive({ site_name: 'Treasure Up', default_danmaku: true });
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public retryAfterSeconds?: number,
  ) {
    super(message);
  }
}
export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set('Content-Type', 'application/json');
  if (options.method && !['GET', 'HEAD'].includes(options.method.toUpperCase()))
    headers.set('X-CSRF-Token', session.csrf);
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    headers,
    credentials: 'same-origin',
  });
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401) {
      session.user = null;
      session.csrf = '';
    }
    const detail = data?.detail;
    throw new ApiError(
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map((e: any) => e.msg).join('；')
          : `请求失败（${response.status}）`,
      response.status,
      (() => {
        const value = response.headers.get('Retry-After');
        if (!value) return undefined;
        const seconds = /^\d+$/.test(value)
          ? Number(value)
          : (Date.parse(value) - Date.now()) / 1000;
        return Number.isFinite(seconds)
          ? Math.max(1, Math.min(86400, Math.ceil(seconds)))
          : undefined;
      })(),
    );
  }
  return data as T;
}
export const write = <T = any>(path: string, body?: unknown, method = 'POST') =>
  api<T>(path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
export async function loadSession() {
  try {
    const data = await api<{ user: User; csrf_token: string }>('/auth/me');
    session.user = data.user;
    session.csrf = data.csrf_token;
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 401) throw error;
  } finally {
    session.ready = true;
    await loadDisplaySettings();
  }
}
export async function loadDisplaySettings() {
  try {
    const data = await api<{ site_name: string; default_danmaku: boolean }>('/library/settings');
    if (typeof data.site_name === 'string') display.site_name = data.site_name || 'Treasure Up';
    if (typeof data.default_danmaku === 'boolean') display.default_danmaku = data.default_danmaku;
  } catch {
    /* Server defaults keep the library usable if optional preferences fail. */
  }
}
export function query(values: Record<string, unknown>) {
  const p = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== '' && value != null) p.set(key, String(value));
  });
  return p.toString();
}
export function errorText(error: unknown) {
  return error instanceof Error ? error.message : '操作失败，请重试';
}
export function duration(value = 0) {
  const n = Math.floor(Number(value) || 0);
  return n >= 3600
    ? `${Math.floor(n / 3600)}:${String(Math.floor((n % 3600) / 60)).padStart(2, '0')}:${String(n % 60).padStart(2, '0')}`
    : `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`;
}
export function date(value?: string) {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.valueOf()) ? value : d.toLocaleString('zh-CN', { hour12: false });
}
export function bytes(value: unknown) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  if (n < 1024) return `${n} B`;
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), 4);
  return `${(n / 1024 ** i).toFixed(1)} ${['B', 'KB', 'MB', 'GB', 'TB'][i]}`;
}
const states: Record<string, string> = {
  queued: '等待执行',
  running: '进行中',
  paused: '已暂停',
  blocked: '受阻',
  partial: '部分完成',
  succeeded: '已完成',
  complete: '已完成',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  pending: '待归档',
  metadata_ready: '资料已保存，等待视频下载',
  available: '可访问',
  unavailable: '来源不可用',
  unknown: '未确认',
  active: '正常',
  normal: '正常',
  valid: '有效',
  unverified: '待验证',
  expired: '已过期',
  incomplete: '未完成',
  local_import: '本地导入',
  imported: '已导入',
  ready: '就绪',
  archived: '已归档',
  deleted: '源视频已移除',
  missing: '资源缺失',
  invalid: '已失效',
  disabled: '已停用',
  enabled: '已启用',
  capturing: '正在归档',
  discovered: '待归档',
  verifying: '正在验证',
};
export const statusText = (value?: string) => (value ? states[value] || '状态待确认' : '未确认');

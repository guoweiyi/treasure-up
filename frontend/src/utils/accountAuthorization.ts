export type SourceAccount = {
  id: string;
  name: string;
  uid?: string | null;
  status: string;
  has_refresh_token: boolean;
  auto_refresh_enabled: boolean;
  refresh_status: string;
  refresh_message?: string | null;
  last_refresh_check_at?: string | null;
  last_refreshed_at?: string | null;
  next_refresh_at?: string | null;
  last_verified_at?: string | null;
  cooldown_until?: string | null;
  next_request_at?: string | null;
  next_video_at?: string | null;
  risk_failures?: number;
};
export type AccountDraft = {
  name: string;
  cookie: string;
  refreshToken: string;
  removeRefreshToken: boolean;
  autoRefresh: boolean;
};
export function accountDraft(account?: SourceAccount | null): AccountDraft {
  return {
    name: account?.name || '',
    cookie: '',
    refreshToken: '',
    removeRefreshToken: false,
    autoRefresh: account?.auto_refresh_enabled ?? true,
  };
}
export function accountPayload(draft: AccountDraft, editing = false) {
  const name = draft.name.trim();
  const cookie = draft.cookie.trim();
  const token = draft.refreshToken.trim();
  if (!name || name.length > 200) throw new Error('请填写不超过 200 字的账号名称');
  if (!editing && !cookie) throw new Error('请填写 Cookie');
  if (token.length > 4096) throw new Error('刷新令牌不能超过 4096 字符');
  if (draft.removeRefreshToken && token) throw new Error('移除刷新令牌前请清空新令牌');
  return {
    name,
    ...(cookie ? { cookie } : {}),
    ...(draft.removeRefreshToken ? { refresh_token: '' } : token ? { refresh_token: token } : {}),
    auto_refresh_enabled: draft.autoRefresh,
  };
}
export const refreshLabels: Record<string, string> = {
  unsupported: '未配置刷新令牌',
  ready: '续期就绪',
  checking: '正在检查',
  refreshing: '正在续期',
  pending_confirm: '正在确认续期',
  error: '续期异常',
  relogin_required: '需要重新授权',
  paused: '自动续期已关闭',
};
export type AuthorizationStatus =
  | 'pending'
  | 'scanned'
  | 'validating'
  | 'success'
  | 'expired'
  | 'cancelled'
  | 'error';
export type AuthorizationInput = { name: string; account_id?: string };
export type AuthorizationResponse = {
  id: string;
  status: AuthorizationStatus;
  expires_at: string;
  poll_after_seconds: number;
  account?: SourceAccount;
  message?: string;
};
export type AuthorizationCreated = AuthorizationResponse & { url: string };
export type AuthorizationState = {
  status: AuthorizationStatus | 'idle' | 'starting';
  image: string;
  expiresAt: string;
  error: string;
};
export function authorizationState(): AuthorizationState {
  return { status: 'idle', image: '', expiresAt: '', error: '' };
}
export const authorizationActive = (status: AuthorizationState['status']) =>
  ['starting', 'pending', 'scanned', 'validating'].includes(status);

export function authorizationURL(value: string) {
  const url = new URL(value);
  const endpoint = value.split(/[?#]/, 1)[0];
  const allowedEndpoints = [
    'https://passport.bilibili.com/h5-app/passport/login/scan',
    'https://account.bilibili.com/h5/account-h5/auth/scan-web',
  ];
  if (
    url.protocol !== 'https:' ||
    !allowedEndpoints.includes(endpoint || '') ||
    url.port ||
    url.username ||
    url.password ||
    value.includes('#') ||
    value.length > 4096
  )
    throw new Error('扫码地址无效，请重新生成二维码');
  return url.href;
}
export async function authorizationImage(value: string) {
  const url = authorizationURL(value);
  const { default: qrcode } = await import('qrcode-generator');
  const code = qrcode(0, 'M');
  code.addData(url, 'Byte');
  code.make();
  return code.createDataURL(5, 20);
}

// A single request at a time, with one expiry deadline that also stops slow requests.
// All state is ephemeral: a closed editor or changed login can never revive its QR code.
export function createAccountAuthorization(
  state: AuthorizationState,
  actions: {
    create: (input: AuthorizationInput, signal: AbortSignal) => Promise<AuthorizationCreated>;
    poll: (id: string, signal: AbortSignal) => Promise<AuthorizationResponse>;
    cancel: (id: string) => Promise<unknown>;
    render: (url: string) => Promise<string>;
    complete: (account: SourceAccount) => void;
  },
) {
  let generation = 0;
  let request: AbortController | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let expiry: ReturnType<typeof setTimeout> | undefined;
  let currentID = '';
  let expiresAt = 0;
  let disposed = false;
  const cancelRemote = (id: string) => {
    if (id) void actions.cancel(id).catch(() => {});
  };
  function stop(cancel = true) {
    generation++;
    clearTimeout(timer);
    clearTimeout(expiry);
    request?.abort();
    request = undefined;
    if (cancel) cancelRemote(currentID);
    currentID = '';
    Object.assign(state, authorizationState());
  }
  function expire(ticket: number) {
    if (ticket !== generation) return;
    stop();
    state.status = 'expired';
  }
  function schedule(ticket: number, seconds: number) {
    const delay = Math.max(3, Math.min(30, Number(seconds) || 3)) * 1000;
    timer = setTimeout(() => void poll(ticket), delay);
  }
  async function poll(ticket: number) {
    if (ticket !== generation) return;
    if (Date.now() >= expiresAt) return expire(ticket);
    request = new AbortController();
    try {
      const result = await actions.poll(currentID, request.signal);
      if (ticket !== generation) return;
      if (Date.now() >= expiresAt) return expire(ticket);
      state.status = result.status;
      if (['pending', 'scanned', 'validating'].includes(result.status)) {
        schedule(ticket, result.poll_after_seconds);
      } else {
        if (result.status === 'success' && !result.account)
          throw new Error('授权结果不完整，请刷新账号列表确认');
        stop(result.status !== 'success');
        state.status = result.status;
        state.error = result.message || '';
        if (result.status === 'success') actions.complete(result.account!);
      }
    } catch (error) {
      if (ticket !== generation) return;
      stop();
      state.status = 'error';
      state.error = error instanceof Error ? error.message : '授权暂时不可用，请重新生成二维码';
    }
  }
  async function start(input: AuthorizationInput) {
    if (disposed || authorizationActive(state.status)) return;
    stop();
    const ticket = generation;
    state.status = 'starting';
    request = new AbortController();
    try {
      const created = await actions.create(input, request.signal);
      if (ticket !== generation) {
        cancelRemote(created.id);
        return;
      }
      currentID = created.id;
      expiresAt = Date.parse(created.expires_at);
      if (!Number.isFinite(expiresAt)) throw new Error('二维码有效期无效，请重新生成');
      if (Date.now() >= expiresAt) return expire(ticket);
      state.expiresAt = created.expires_at;
      expiry = setTimeout(() => expire(ticket), Math.min(expiresAt - Date.now(), 600000));
      const image = await actions.render(authorizationURL(created.url));
      if (ticket !== generation) return;
      state.image = image;
      state.status = 'pending';
      schedule(ticket, created.poll_after_seconds);
    } catch (error) {
      if (ticket !== generation) return;
      stop();
      state.status = 'error';
      state.error = error instanceof Error ? error.message : '生成二维码失败，请重试';
    }
  }
  return {
    start,
    stop,
    dispose() {
      disposed = true;
      stop();
    },
  };
}

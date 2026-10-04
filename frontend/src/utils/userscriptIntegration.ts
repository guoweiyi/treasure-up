export const userscriptQualities = [
  'best',
  '4320p',
  '2160p',
  '1440p',
  '1080p',
  '720p',
  '480p',
  '360p',
] as const;
export type UserscriptQuality = (typeof userscriptQualities)[number];
export interface UserscriptTokenForm {
  name: string;
  account_id: string;
  expires_days: number | string;
  quality: UserscriptQuality;
  download_media: boolean;
  fetch_danmaku: boolean;
  fetch_comments: boolean;
  fetch_subtitles: boolean;
}
export interface UserscriptToken {
  id: string;
  name: string;
  account_id: string;
  account_name?: string | null;
  scope: string;
  expires_at: string;
  revoked_at?: string | null;
  created_at: string;
  last_used_at?: string | null;
  policy: Record<string, unknown>;
}
export const userscriptFlags = [
  ['download_media', '视频原档'],
  ['fetch_danmaku', '弹幕'],
  ['fetch_comments', '评论'],
  ['fetch_subtitles', '字幕'],
] as const;

export function usableUserscriptAccount(account: { status: string }) {
  return !['disabled', 'invalid', 'expired'].includes(account.status);
}

export function newUserscriptTokenForm(accountId = ''): UserscriptTokenForm {
  return {
    name: '',
    account_id: accountId,
    expires_days: 90,
    quality: 'best',
    download_media: true,
    fetch_danmaku: true,
    fetch_comments: true,
    fetch_subtitles: true,
  };
}

export function userscriptTokenPayload(form: UserscriptTokenForm, accountIds: readonly string[]) {
  const name = form.name.trim();
  if (!name || name.length > 100) throw new Error('请填写 1 至 100 字的令牌名称');
  if (!form.account_id || !accountIds.includes(form.account_id))
    throw new Error('请选择已有的来源账号');
  const expires = Number(form.expires_days);
  if (!Number.isInteger(expires) || expires < 1 || expires > 365)
    throw new Error('有效期须为 1 至 365 天');
  if (!userscriptQualities.includes(form.quality)) throw new Error('请选择有效的画质上限');
  const policy: Record<string, string | boolean> = { quality: form.quality };
  for (const [key] of userscriptFlags) {
    if (typeof form[key] !== 'boolean') throw new Error('请选择要保存的内容');
    policy[key] = form[key];
  }
  // Explicit allowlist: never forward credentials, DOM fields or extra options.
  return { name, account_id: form.account_id, expires_days: expires, policy };
}

export function userscriptTokenState(
  token: Pick<UserscriptToken, 'revoked_at' | 'expires_at'>,
  now = Date.now(),
) {
  if (token.revoked_at) return { key: 'revoked', label: '已撤销', usable: false };
  const expires = Date.parse(token.expires_at);
  if (!Number.isFinite(expires)) return { key: 'unknown', label: '期限待确认', usable: false };
  if (expires <= now) return { key: 'expired', label: '已过期', usable: false };
  return { key: 'active', label: '可用', usable: true };
}

export function userscriptPolicySummary(policy: Record<string, unknown> = {}) {
  const quality =
    policy.quality === 'best'
      ? '最高可用画质'
      : typeof policy.quality === 'string' &&
          userscriptQualities.includes(policy.quality as UserscriptQuality)
        ? policy.quality.toUpperCase()
        : '按系统画质';
  const enabled = userscriptFlags.filter(([key]) => policy[key] === true).map(([, label]) => label);
  return [quality, ...enabled].join(' · ');
}

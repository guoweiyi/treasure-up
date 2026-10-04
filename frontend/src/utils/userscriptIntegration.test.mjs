import test from 'node:test';
import assert from 'node:assert/strict';
import {
  newUserscriptTokenForm,
  userscriptTokenPayload,
  userscriptTokenState,
  userscriptPolicySummary,
  usableUserscriptAccount,
} from './userscriptIntegration.ts';

test('unavailable source accounts are excluded from default selection and token creation', () => {
  for (const status of ['disabled', 'invalid', 'expired'])
    assert.equal(usableUserscriptAccount({ status }), false);
  for (const status of ['valid', 'unverified'])
    assert.equal(usableUserscriptAccount({ status }), true);
});

test('token payload is bounded and contains only chosen policy, never credentials or unrelated form fields', () => {
  const form = {
    ...newUserscriptTokenForm('account-a'),
    name: '  家用浏览器  ',
    fetch_comments: false,
    cookie: 'do-not-forward',
    password: 'do-not-forward',
    token: 'do-not-forward',
    scope: 'admin',
  };
  assert.deepEqual(userscriptTokenPayload(form, ['account-a']), {
    name: '家用浏览器',
    account_id: 'account-a',
    expires_days: 90,
    policy: {
      quality: 'best',
      download_media: true,
      fetch_danmaku: true,
      fetch_comments: false,
      fetch_subtitles: true,
    },
  });
  assert.equal(form.name, '  家用浏览器  ');
});

test('unknown accounts and invalid expiration cannot create a token while both day boundaries work', () => {
  const form = { ...newUserscriptTokenForm('a'), name: 'test' };
  for (const expires_days of [0, 366, 1.5, '', 'abc', Infinity])
    assert.throws(() => userscriptTokenPayload({ ...form, expires_days }, ['a']), /有效期/);
  for (const expires_days of [1, 365, '90'])
    assert.equal(
      userscriptTokenPayload({ ...form, expires_days }, ['a']).expires_days,
      Number(expires_days),
    );
  assert.throws(() => userscriptTokenPayload(form, ['other']), /来源账号/);
  assert.throws(() => userscriptTokenPayload({ ...form, name: ' ' }, ['a']), /名称/);
  assert.throws(() => userscriptTokenPayload({ ...form, name: 'a'.repeat(101) }, ['a']), /名称/);
  assert.throws(() => userscriptTokenPayload({ ...form, quality: 'remote' }, ['a']), /画质/);
});

test('revocation wins over expiry and an invalid timestamp is never presented as a usable credential', () => {
  const now = Date.parse('2026-10-01T00:00:00Z');
  assert.equal(userscriptTokenState({ expires_at: '2026-10-02T00:00:00Z' }, now).usable, true);
  assert.equal(userscriptTokenState({ expires_at: '2026-10-01T00:00:00Z' }, now).key, 'expired');
  assert.equal(
    userscriptTokenState(
      { expires_at: '2026-10-02T00:00:00Z', revoked_at: '2026-09-30T00:00:00Z' },
      now,
    ).key,
    'revoked',
  );
  assert.equal(userscriptTokenState({ expires_at: 'invalid' }, now).usable, false);
  assert.equal(
    userscriptPolicySummary({
      quality: '1080p',
      download_media: true,
      fetch_comments: false,
      fetch_subtitles: true,
    }),
    '1080P · 视频原档 · 字幕',
  );
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {
  accountDraft,
  accountPayload,
  authorizationImage,
  authorizationState,
  authorizationURL,
  createAccountAuthorization,
} from './accountAuthorization.ts';
import { credentialRefreshNotice, jobNames } from './jobDisplay.ts';

const now = new Date('2026-10-07T00:00:00Z');
const account = {
  id: 'a',
  name: '已连接账号',
  has_refresh_token: true,
  auto_refresh_enabled: true,
};
const created = (id = 'qr-a') => ({
  id,
  status: 'pending',
  poll_after_seconds: 3,
  expires_at: new Date(now.valueOf() + 180000).toISOString(),
  url: 'https://passport.bilibili.com/h5-app/passport/login/scan?qrcode_key=synthetic-test-key',
});
const response = (status, extra = {}) => ({ ...created(), status, ...extra });
const settle = async () => {
  for (let i = 0; i < 8; i++) await Promise.resolve();
};
function fixture(t, overrides = {}) {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now });
  const state = authorizationState(),
    completed = [],
    cancelled = [];
  const actions = {
    create: async () => created(),
    poll: async () => response('pending'),
    cancel: async (id) => {
      cancelled.push(id);
    },
    render: async () => 'data:image/gif;base64,synthetic',
    complete: (value) => completed.push(value),
    ...overrides,
  };
  return { state, completed, cancelled, controller: createAccountAuthorization(state, actions) };
}

test('account drafts never echo stored credentials; partial edits preserve omitted secrets', () => {
  const draft = accountDraft({
    ...account,
    cookie: 'must-not-echo',
    refresh_token: 'must-not-echo',
  });
  assert.equal(draft.cookie, '');
  assert.equal(draft.refreshToken, '');
  assert.deepEqual(accountPayload(draft, true), { name: account.name, auto_refresh_enabled: true });
  draft.cookie = ' new-cookie ';
  assert.equal(accountPayload(draft, true).cookie, 'new-cookie');
  assert.equal(Object.hasOwn(accountPayload(draft, true), 'refresh_token'), false);
  draft.refreshToken = ' paired-token ';
  assert.equal(accountPayload(draft, true).refresh_token, 'paired-token');
  draft.refreshToken = '';
  draft.removeRefreshToken = true;
  assert.equal(accountPayload(draft, true).refresh_token, '');
});
test('manual credentials validate required fields and token bounds without echoing input', () => {
  const draft = accountDraft();
  assert.throws(() => accountPayload(draft), /账号名称/);
  draft.name = '账号';
  assert.throws(() => accountPayload(draft), /Cookie/);
  draft.cookie = 'cookie';
  assert.equal(Object.hasOwn(accountPayload(draft), 'refresh_token'), false);
  draft.refreshToken = 'x'.repeat(4097);
  assert.throws(() => accountPayload(draft), /4096/);
  draft.refreshToken = 'token';
  draft.removeRefreshToken = true;
  assert.throws(() => accountPayload(draft), /清空/);
});
test('QR images are generated locally and accept only official HTTPS authorization URLs', async (t) => {
  t.mock.method(globalThis, 'fetch', () => {
    throw new Error('QR rendering must not use the network');
  });
  const image = await authorizationImage(created().url);
  assert.match(image, /^data:image\/gif;base64,/);
  assert.equal(image.includes('synthetic-test-key'), false);
  for (const url of [
    'http://passport.bilibili.com/login',
    'https://evil.test/login',
    'https://passport.bilibili.com.evil.test/',
    'https://me@passport.bilibili.com/',
    'https://passport.bilibili.com:444/',
    'javascript:alert(1)',
  ])
    assert.throws(() => authorizationURL(url));
});
test('both official QR entry points are accepted without broadening hosts or paths', async (t) => {
  t.mock.method(globalThis, 'fetch', () => {
    throw new Error('QR rendering must not use the network');
  });
  const legacy = created().url;
  const current =
    'https://account.bilibili.com/h5/account-h5/auth/scan-web?navhide=1&callback=close&qrcode_key=synthetic-new-key';
  for (const url of [legacy, current]) {
    assert.equal(authorizationURL(url), url);
    assert.match(await authorizationImage(url), /^data:image\/gif;base64,/);
  }
  for (const url of [
    current.replace('account.bilibili.com', 'passport.bilibili.com'),
    legacy.replace('passport.bilibili.com', 'account.bilibili.com'),
    current.replace('/scan-web?', '/scan-web/other?'),
    current.replace('/scan-web?', '/scan-web/?'),
    current.replace('/auth/', '/other/../auth/'),
    current.replace('account.bilibili.com', 'account.bilibili.com.evil.test'),
    current.replace('account.bilibili.com', 'evil@account.bilibili.com'),
    current.replace('account.bilibili.com', 'account.bilibili.com:443'),
    current.replace('account.bilibili.com', 'account.bilibili.com:444'),
    current.replace('https:', 'http:'),
    `${current}#unexpected-fragment`,
    'https://account.bilibili.com/',
    'https://passport.bilibili.com/other',
  ])
    assert.throws(() => authorizationURL(url));
});
test('duplicate generation is ignored and closing aborts a pending create before rendering', async (t) => {
  let finish,
    signal,
    creates = 0,
    renders = 0;
  const f = fixture(t, {
    create: (_input, requestSignal) => {
      creates++;
      signal = requestSignal;
      return new Promise((resolve) => {
        finish = resolve;
      });
    },
    render: async () => {
      renders++;
      return 'never-shown';
    },
  });
  const pending = f.controller.start({ name: '账号' });
  await f.controller.start({ name: '重复提交' });
  assert.equal(creates, 1);
  f.controller.stop();
  assert.equal(signal.aborted, true);
  finish(created());
  await pending;
  assert.equal(renders, 0);
  assert.deepEqual(f.cancelled, ['qr-a']);
  assert.deepEqual(f.state, authorizationState());
});
test('closing while a lazy QR module loads cannot revive a cancelled authorization', async (t) => {
  let finishRender;
  const f = fixture(t, {
    render: () =>
      new Promise((resolve) => {
        finishRender = resolve;
      }),
  });
  const pending = f.controller.start({ name: '账号' });
  await settle();
  f.controller.stop();
  finishRender('late-qr');
  await pending;
  t.mock.timers.tick(10000);
  assert.deepEqual(f.state, authorizationState());
  assert.deepEqual(f.cancelled, ['qr-a']);
});
test('QR polling is sequential and stale results after regeneration are ignored', async (t) => {
  let finish,
    oldSignal,
    calls = 0;
  const f = fixture(t, {
    poll: (_id, signal) => {
      calls++;
      oldSignal = signal;
      return new Promise((resolve) => {
        finish = resolve;
      });
    },
  });
  await f.controller.start({ name: '账号' });
  t.mock.timers.tick(3000);
  t.mock.timers.tick(15000);
  assert.equal(calls, 1);
  f.controller.stop();
  await f.controller.start({ name: '另一个授权' });
  assert.equal(oldSignal.aborted, true);
  finish(response('success', { account }));
  await settle();
  assert.deepEqual(f.completed, []);
  assert.equal(f.state.status, 'pending');
  f.controller.dispose();
});
test('scan and phone confirmation advance through validation and save exactly once', async (t) => {
  const statuses = ['scanned', 'validating', 'success'];
  let calls = 0;
  const f = fixture(t, { poll: async () => response(statuses[calls++], { account }) });
  await f.controller.start({ name: '账号' });
  for (const status of statuses) {
    t.mock.timers.tick(3000);
    await settle();
    assert.equal(f.state.status, status);
  }
  t.mock.timers.tick(200000);
  assert.equal(calls, 3);
  assert.deepEqual(f.completed, [account]);
  assert.deepEqual(f.cancelled, []);
  assert.equal(f.state.image, '');
});
test('local expiry aborts even a hanging poll and ignores a late success', async (t) => {
  let finish, signal;
  const f = fixture(t, {
    poll: (_id, s) => {
      signal = s;
      return new Promise((resolve) => {
        finish = resolve;
      });
    },
  });
  await f.controller.start({ name: '账号' });
  t.mock.timers.tick(3000);
  t.mock.timers.tick(180000);
  assert.equal(f.state.status, 'expired');
  assert.equal(f.state.image, '');
  assert.equal(signal.aborted, true);
  finish(response('success', { account }));
  await settle();
  assert.deepEqual(f.completed, []);
  assert.deepEqual(f.cancelled, ['qr-a']);
});
test('server terminal states stop polling; missing account cannot report success', async (t) => {
  let polls = 0;
  const f = fixture(t, {
    poll: async () => {
      polls++;
      return response('success');
    },
  });
  await f.controller.start({ name: '账号' });
  t.mock.timers.tick(3000);
  await settle();
  assert.equal(f.state.status, 'error');
  assert.match(f.state.error, /不完整/);
  t.mock.timers.tick(15000);
  assert.equal(polls, 1);
  assert.deepEqual(f.completed, []);
});
test('failed polling can be retried with a new QR and dispose prevents future requests', async (t) => {
  let creates = 0;
  const f = fixture(t, {
    create: async () => {
      creates++;
      return created();
    },
    poll: async () => {
      throw new Error('连接暂时中断');
    },
  });
  await f.controller.start({ name: '账号' });
  t.mock.timers.tick(3000);
  await settle();
  assert.equal(f.state.status, 'error');
  assert.equal(f.state.image, '');
  await f.controller.start({ name: '账号' });
  assert.equal(f.state.status, 'pending');
  f.controller.dispose();
  await f.controller.start({ name: '不能复活' });
  t.mock.timers.tick(10000);
  assert.equal(creates, 2);
  assert.deepEqual(f.state, authorizationState());
});
test('unsupported QR URL is rejected without generating an image or polling', async (t) => {
  let renders = 0;
  const f = fixture(t, {
    create: async () => ({ ...created(), url: 'https://third-party.test/?key=secret' }),
    render: async () => {
      renders++;
      return '';
    },
  });
  await f.controller.start({ name: '账号' });
  assert.equal(f.state.status, 'error');
  assert.equal(f.state.image, '');
  assert.equal(renders, 0);
  assert.deepEqual(f.cancelled, ['qr-a']);
});
test('refresh task messages distinguish no rotation from renewed credentials', () => {
  assert.equal(jobNames.refresh_credentials, '检查账号续期');
  const job = {
    kind: 'refresh_credentials',
    status: 'succeeded',
    result: { refresh_status: 'ready', rotated: false },
  };
  assert.match(credentialRefreshNotice(job), /无需续期/);
  job.result.rotated = true;
  assert.match(credentialRefreshNotice(job), /已续期/);
  assert.equal(credentialRefreshNotice({ ...job, status: 'running' }), '');
  assert.match(
    credentialRefreshNotice({ ...job, result: { refresh_status: 'relogin_required' } }),
    /重新授权/,
  );
});

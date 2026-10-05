import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(
  new URL('../../public/userscripts/treasure-up.user.js', import.meta.url),
  'utf8',
);
const core = source.match(/\/\/ CORE-BEGIN[^\n]*\n([\s\S]*?)\/\/ CORE-END/)?.[1];
assert.ok(core, 'test the shipped single-file script, without a page-visible test export');
const ctx = vm.createContext({ URL, setTimeout, clearTimeout });
vm.runInContext(
  `${core}\nglobalThis.testCore = {extractBvids,videoFromUrl,normalizeBackend,nextConfig,supportedManager,safeError,responseError,environmentIssue,invalidateCards,mergeSelection,pendingItems,createDraftStore,createClient,verifyAndSaveConnection};`,
  ctx,
);
const api = ctx.testCore;
const token = 'tu_ingest_' + 's'.repeat(43);
const config = { backend: 'https://archive.example.com', token };
const ids = ['BV1TZac6NEmK', 'BV151tczUEcs', 'BV1234567890'];
const native = (value) => JSON.parse(JSON.stringify(value));
function fakeGM(handler) {
  const calls = [];
  return {
    calls,
    info: { scriptHandler: 'Tampermonkey', version: '5.5.1', sandboxMode: 'dom' },
    xmlHttpRequest(options) {
      calls.push(options);
      return handler(options);
    },
  };
}

test('userscript extracts case-sensitive BV identifiers, deduplicates and rejects foreign card links', () => {
  assert.deepEqual(
    native(
      api.extractBvids(
        `链接 https://www.bilibili.com/video/${ids[0]}?p=2\n${ids[1]} ${ids[0]} X${ids[2]} ${ids[2]}X bv1234567890`,
      ),
    ),
    ids.slice(0, 2),
  );
  assert.equal(api.videoFromUrl(`/video/${ids[0]}?p=3`), ids[0]);
  assert.equal(api.videoFromUrl(`https://www.bilibili.com/list/ml1?bvid=${ids[1]}`), ids[1]);
  for (const url of [
    `https://bilibili.com.evil.example/video/${ids[0]}`,
    `https://evil.example/?bvid=${ids[0]}`,
    `javascript:alert('${ids[0]}')`,
    `/video/${ids[0]}tooLong`,
  ])
    assert.equal(api.videoFromUrl(url), null);
});
test('backend targets normalize API suffix and permit explicit private HTTP while rejecting public plaintext and credential-bearing URLs', () => {
  for (const target of [
    'https://archive.example.com',
    'http://localhost:8788',
    'http://127.0.0.2:8788',
    'http://192.168.2.4:8788',
    'http://172.31.10.2',
    'http://10.5.4.3',
    'http://[::1]:8788',
    'http://[fd12:3456::7]',
  ])
    assert.equal(api.normalizeBackend(target + '/api/v1/'), new URL(target).origin);
  for (const target of [
    'http://archive.example.com',
    'http://8.8.8.8',
    'http://172.32.0.1',
    'http://[fe80::1]',
    'https://name:secret@example.com',
    'https://example.com/private',
    'https://example.com/?token=secret',
    'https://example.com/#x',
    'file:///tmp',
    '//localhost:8788',
  ])
    assert.throws(() => api.normalizeBackend(target));
  assert.deepEqual(native(api.nextConfig(config, config.backend + '/api/v1', '')), config);
  assert.throws(() => api.nextConfig(config, 'https://different.example.com', ''), /重新填写/);
  assert.throws(() => api.nextConfig(config, config.backend, 'SESSDATA=not-a-token'), /专用令牌/);
  for (const length of [32, 42, 44, 128])
    assert.throws(
      () => api.nextConfig(config, config.backend, 'tu_ingest_' + 's'.repeat(length)),
      /专用令牌/,
    );
  assert.equal(
    api.nextConfig(config, 'https://different.example.com', token).backend,
    'https://different.example.com',
  );
});
test('selection enforces 50 unique valid videos and does not persist arbitrary page data', () => {
  const input = Array.from({ length: 55 }, (_, i) => ({
    bvid: 'BV' + String(i).padStart(10, '0'),
    title: 'item ' + i,
    cookie: 'must not persist',
    cover: 'https://untrusted.example/token',
  }));
  const result = api.mergeSelection(new Map(), [...input, input[0], { bvid: 'invalid' }]);
  assert.equal(result.items.size, 50);
  assert.equal(result.overflow, 5);
  assert.deepEqual(Object.keys(result.items.values().next().value).sort(), ['bvid', 'title']);
  assert.equal(
    api.pendingItems(
      new Map([
        ['a', { status: 'queued' }],
        ['b', { status: 'daily_limit' }],
        ['c', {}],
      ]),
    ).length,
    2,
  );
});
test('GM client sends only fixed endpoint + BV body, without cookies or redirects, and refuses unsupported managers before any request', async () => {
  const gm = fakeGM((options) =>
    Promise.resolve({ status: 202, finalUrl: options.url, response: { items: [] } }),
  );
  await api.createClient(gm, config).submit([ids[0], ids[0], ids[1]]);
  const call = gm.calls[0];
  assert.equal(call.url, config.backend + '/api/v1/integrations/userscript/videos');
  assert.deepEqual(JSON.parse(call.data), { bvids: ids.slice(0, 2) });
  assert.equal(call.anonymous, true);
  assert.equal(call.redirect, 'error');
  assert.equal(call.fetch, true);
  assert.equal(call.headers.Authorization, `Bearer ${token}`);
  for (const key of ['cookie', 'user', 'password', 'Origin', 'Referer'])
    assert.equal(call[key] ?? call.headers[key], undefined);
  for (const info of [
    { scriptHandler: 'Violentmonkey', version: '9.0' },
    { scriptHandler: 'Tampermonkey', version: '4.19.6180' },
    { scriptHandler: 'Tampermonkey', version: 'unknown' },
    { scriptHandler: 'Tampermonkey', version: '5.5.1', sandboxMode: 'raw' },
    { scriptHandler: 'Tampermonkey', version: '5.5.1', sandboxMode: 'js' },
  ]) {
    gm.info = info;
    await assert.rejects(api.createClient(gm, config).status(), /Tampermonkey 5/);
  }
  assert.equal(gm.calls.length, 1);
  assert.throws(() => api.createClient(gm, config).submit(['invalid']), /BV/);
});
test('GM client rejects redirected responses, masks FastAPI detail secrets and preserves validation explanations', async () => {
  const redirected = fakeGM(() =>
    Promise.resolve({
      status: 200,
      finalUrl: 'https://attacker.example/status',
      response: { ok: true },
    }),
  );
  await assert.rejects(api.createClient(redirected, config).status(), /重定向/);
  const bad = fakeGM((options) =>
    Promise.resolve({
      status: 401,
      finalUrl: options.url,
      response: { detail: `已撤销 ${token}` },
    }),
  );
  await assert.rejects(
    api.createClient(bad, config).status(),
    (error) =>
      error.message.includes('HTTP 401') &&
      error.message.includes('已撤销') &&
      !error.message.includes(token),
  );
  assert.equal(
    api.safeError([{ msg: 'BV 号无效', input: token }, { msg: '最多 50 个' }], token),
    'BV 号无效；最多 50 个',
  );
  const invalid = fakeGM((options) =>
    Promise.resolve({ status: 200, finalUrl: options.url, response: { ok: true, scope: 'admin' } }),
  );
  await assert.rejects(api.createClient(invalid, config).status(), /协议/);
});
test('request timeout aborts the actual GM promise and late completion cannot turn it into success', async () => {
  let aborts = 0,
    finish;
  const gm = fakeGM(() => {
    const promise = new Promise((resolve) => {
      finish = resolve;
    });
    promise.abort = () => {
      aborts++;
    };
    return promise;
  });
  await assert.rejects(api.createClient(gm, config, 10).submit([ids[0]]), /超时.*可能已提交/);
  assert.equal(aborts, 1);
  finish({ status: 202, finalUrl: gm.calls[0].url, response: {} });
});

test('HTTP failures provide actionable guidance even when a reverse proxy returns HTML, without exposing tokens', async () => {
  for (const [status, hint] of [
    [401, /新建令牌/],
    [409, /更新或验证/],
    [404, /站点根地址/],
    [429, /稍后手动重试/],
    [503, /服务暂时不可用/],
  ]) {
    const gm = fakeGM((options) =>
      Promise.resolve({
        status,
        finalUrl: options.url,
        responseText: `<html>proxy error ${token}</html>`,
      }),
    );
    await assert.rejects(
      api.createClient(gm, config).status(),
      (error) => hint.test(error.message) && !error.message.includes(token),
    );
  }
  const gm = fakeGM(() => Promise.resolve({ status: 0, finalUrl: '' }));
  await assert.rejects(api.createClient(gm, config).status(), /没有收到服务响应/);
  assert.ok(!api.responseError(401, `invalid ${token}`, token).includes(token));
});

test('a failed connection replacement preserves the previously saved origin and token', async () => {
  let saved = { ...config },
    writes = 0,
    status = 401;
  const next = { backend: 'https://new-archive.example.com', token: 'tu_ingest_' + 'n'.repeat(43) };
  const gm = fakeGM((options) =>
    Promise.resolve({
      status,
      finalUrl: options.url,
      response: status === 200 ? { ok: true, scope: 'ingest.submit' } : { detail: 'expired' },
    }),
  );
  gm.setValue = async (key, value) => {
    assert.equal(key, 'test-connection');
    writes++;
    saved = value;
  };
  await assert.rejects(api.verifyAndSaveConnection(gm, next, 'test-connection'), /401/);
  assert.deepEqual(saved, config);
  assert.equal(writes, 0);
  status = 200;
  assert.equal((await api.verifyAndSaveConnection(gm, next, 'test-connection')).ok, true);
  assert.deepEqual(saved, next);
  assert.equal(writes, 1);
});

test('startup diagnoses missing tab-storage grants before offering a selection that cannot persist', () => {
  const gm = { info: { scriptHandler: 'Tampermonkey', version: '5.5.1', sandboxMode: 'dom' } };
  assert.match(api.environmentIssue(gm), /权限不完整/);
  for (const name of ['getValue', 'setValue', 'getTab', 'saveTab', 'xmlHttpRequest'])
    gm[name] = () => {};
  assert.equal(api.environmentIssue(gm), '');
  gm.info.sandboxMode = 'raw';
  assert.match(api.environmentIssue(gm), /DOM 隔离/);
});

test('rescanning a recycled or emptied card drops its former BV rather than selecting stale content', () => {
  const changed = { isConnected: true },
    nested = { isConnected: true },
    other = { isConnected: true },
    detached = { isConnected: false };
  changed.contains = (node) => node === nested;
  const cards = new Map([
    [changed, { bvid: ids[0] }],
    [nested, { bvid: ids[0] }],
    [other, { bvid: ids[1] }],
    [detached, { bvid: ids[2] }],
  ]);
  api.invalidateCards(cards, changed);
  assert.deepEqual([...cards.keys()], [other]);
  // There may be no replacement link after invalidation: do not re-add the old BV.
  assert.equal(api.videoFromUrl('https://live.bilibili.com/123'), null);
  cards.set(changed, { bvid: ids[2] });
  api.invalidateCards(cards, changed);
  assert.deepEqual([...cards.values()], [{ bvid: ids[1] }]);
});
test('draft persists within its tab across pages, serializes writes and never shares selections with another tab', async () => {
  let stored = {},
    displayed;
  const gm = {
    getTab: async () => structuredClone(stored),
    saveTab: async (value) => {
      stored = native(value);
    },
  };
  const draft = api.createDraftStore(gm, (items) => {
    displayed = items;
  });
  await Promise.all([
    draft.change({ type: 'add', items: [{ bvid: ids[0], title: 'A' }] }),
    draft.change({ type: 'add', items: [{ bvid: ids[1], title: 'B' }] }),
  ]);
  assert.deepEqual(
    stored.treasureUpDraft.map((item) => item.bvid),
    ids.slice(0, 2),
  );
  const newPage = api.createDraftStore(gm, (items) => {
    displayed = items;
  });
  await newPage.refresh();
  assert.equal(displayed.size, 2);
  const otherTab = api.createDraftStore(
    { getTab: async () => ({}), saveTab: async () => {} },
    (items) => {
      assert.equal(items.size, 0);
    },
  );
  await otherTab.refresh();
  stored.treasureUpDraft.push({ bvid: ids[2], title: 'another pending operation' });
  await draft.change({ type: 'remove', bvids: ids.slice(0, 2) });
  assert.deepEqual(stored.treasureUpDraft, [{ bvid: ids[2], title: 'another pending operation' }]);
  assert.equal(displayed.size, 1);
});
test('production metadata and credential UI keep page DOM free of a secret input or public test hook', () => {
  assert.match(source, /@sandbox\s+DOM/);
  assert.match(source, /@noframes/);
  assert.match(source, /attachShadow\(\{ mode: 'closed' \}\)/);
  assert.doesNotMatch(
    source,
    /type="password"|localStorage|document\.cookie|unsafeWindow|console\./,
  );
  assert.match(source, /window\.prompt\(/);
  assert.doesNotMatch(source, /globalThis\.testCore|module\.exports/);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { createSourceLoader } from './sourceDiscovery.ts';

const row = (id) => ({ kind: 'favorite', source_id: id, title: `收藏 ${id}` });
const page = (ids, next = null) => ({ items: ids.map(row), next_page: next, cached: false });
const state = () => ({ rows: [], nextPage: null, cached: false, loading: false, error: '' });

test('switching accounts rejects late private-list results and errors', async () => {
  const current = state();
  const pending = [];
  const loader = createSourceLoader(
    current,
    () => new Promise((resolve, reject) => pending.push({ resolve, reject })),
  );
  const first = loader.load('a', 'created');
  const second = loader.load('b', 'created');
  pending[1].resolve(page(['b']));
  await second;
  pending[0].resolve(page(['a']));
  await first;
  assert.deepEqual(
    current.rows.map((item) => item.source_id),
    ['b'],
  );
  const old = loader.load('a', 'following');
  await loader.load('', 'following');
  pending[2].reject(new Error('stale error'));
  await old;
  assert.deepEqual(current.rows, []);
  assert.equal(current.error, '');
  assert.equal(current.loading, false);
});

test('pagination deduplicates and failed load-more retries the same page', async () => {
  const current = state(),
    calls = [];
  const loader = createSourceLoader(current, async (params) => {
    calls.push(params.page);
    if (calls.length === 1) return page(['1', '2'], 2);
    if (calls.length === 2) throw new Error('稍后重试');
    return page(['2', '3']);
  });
  await loader.load('a', 'collected');
  await loader.load('a', 'collected', true);
  assert.equal(current.nextPage, 2);
  assert.equal(current.error, '稍后重试');
  await loader.load('a', 'collected', true);
  assert.deepEqual(calls, [1, 2, 2]);
  assert.deepEqual(
    current.rows.map((item) => item.source_id),
    ['1', '2', '3'],
  );
  assert.equal(current.nextPage, null);
  assert.equal(current.error, '');
});

test('unmounted pickers discard responses and do not start more requests', async () => {
  const current = state();
  let finish;
  let calls = 0;
  const loader = createSourceLoader(current, () => {
    calls++;
    return new Promise((resolve) => {
      finish = resolve;
    });
  });
  const pending = loader.load('a', 'created');
  loader.dispose();
  finish(page(['a']));
  await pending;
  await loader.load('a', 'created');
  assert.deepEqual(current.rows, []);
  assert.equal(calls, 1);
});

test('account lock retries respect Retry-After and stop after two automatic retries', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const current = state();
  let calls = 0;
  const loader = createSourceLoader(current, async () => {
    calls++;
    throw Object.assign(new Error('账号忙'), { status: 429, retryAfterSeconds: 30 });
  });
  await loader.load('a', 'created');
  assert.equal(current.loading, false);
  assert.equal(current.retryPending, true);
  t.mock.timers.tick(29999);
  assert.equal(calls, 1);
  t.mock.timers.tick(1);
  await Promise.resolve();
  assert.equal(calls, 2);
  t.mock.timers.tick(30000);
  await Promise.resolve();
  assert.equal(calls, 3);
  assert.equal(current.retryPending, false);
  t.mock.timers.tick(300000);
  assert.equal(calls, 3);
});

test('switching accounts or cancelling removes scheduled discovery retries', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const current = state(),
    calls = [];
  const loader = createSourceLoader(current, async (request) => {
    calls.push(request.account_id);
    if (request.account_id === 'a')
      throw Object.assign(new Error('账号忙'), { status: 429, retryAfterSeconds: 20 });
    return page(['b']);
  });
  await loader.load('a', 'created');
  await loader.load('b', 'created');
  t.mock.timers.tick(30000);
  await Promise.resolve();
  assert.deepEqual(calls, ['a', 'b']);
  await loader.load('a', 'created');
  loader.cancelRetry();
  t.mock.timers.tick(30000);
  assert.equal(calls.length, 3);
});

test('long source cooldown reports a retry time without a spinner or automatic request', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const current = state();
  let calls = 0;
  const loader = createSourceLoader(current, async () => {
    calls++;
    throw Object.assign(new Error('冷却'), { status: 429, retryAfterSeconds: 900 });
  });
  await loader.load('a', 'created');
  assert.ok(current.retryAt > Date.now());
  assert.equal(current.loading, false);
  assert.equal(current.retryPending, false);
  t.mock.timers.tick(900000);
  assert.equal(calls, 1);
});

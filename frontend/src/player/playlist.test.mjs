import { test } from 'node:test';
import assert from 'node:assert/strict';
import { computed, effectScope, ref, nextTick } from 'vue';
import { usePlaylist } from './usePlaylist.ts';
function fixture(id = 'v1') {
  const requests = [],
    scope = ref({ type: 'collection', id: 'a' }),
    current = ref(id),
    effects = effectScope();
  const queue = effects.run(() =>
    usePlaylist(
      computed(() => scope.value),
      computed(() => current.value),
      (context, page, signal) =>
        new Promise((resolve, reject) => requests.push({ context, page, signal, resolve, reject })),
    ),
  );
  return { queue, requests, scope, current, stop: () => effects.stop() };
}
const page = (ids, total = ids.length) => ({
  items: ids.map((id) => ({ id })),
  total,
  page: 1,
  page_size: 100,
  scope_title: 'Fixture',
});
const flush = async () => {
  await Promise.resolve();
  await nextTick();
  await Promise.resolve();
};
test('very large lists stop background location after five pages and continue only when requested', async () => {
  const f = fixture('v6');
  for (let index = 0; index < 5; index++) {
    assert.equal(f.requests[index].page, index + 1);
    f.requests[index].resolve(page([`v${index + 1}`], 10000));
    await flush();
  }
  assert.equal(f.requests.length, 5);
  const locating = f.queue.locate();
  f.requests[5].resolve(page(['v6'], 10000));
  await locating;
  assert.equal(f.requests.length, 6);
  f.stop();
});
test('playlist scope change and unmount discard late pages, abort requests and do not continue fetching', async () => {
  const f = fixture('v1');
  f.scope.value = { type: 'creator', id: 'b' };
  await nextTick();
  assert.equal(f.requests[0].signal.aborted, true);
  f.requests[1].resolve(page(['v1', 'v2']));
  await flush();
  f.requests[0].resolve(page(['old'], 1000));
  await flush();
  assert.deepEqual(
    f.queue.items.value.map((v) => v.id),
    ['v1', 'v2'],
  );
  assert.equal(f.requests.length, 2);
  const pending = f.queue.more();
  await pending;
  assert.equal(f.requests.length, 2);
  f.scope.value = { type: 'creator', id: 'c' };
  await nextTick();
  f.stop();
  f.requests[2].resolve(page(['late'], 1000));
  await flush();
  assert.equal(f.requests[2].signal.aborted, true);
  assert.equal(f.queue.items.value.length, 0);
  assert.equal(f.requests.length, 3);
});
test('current video location, page boundary and overlapping loads issue one request per page', async () => {
  const f = fixture('v2');
  f.requests[0].resolve(page(['v1'], 3));
  await flush();
  assert.equal(f.requests[1].page, 2);
  f.requests[1].resolve(page(['v2'], 3));
  await flush();
  const next = f.queue.ensureNext();
  await flush();
  const more = f.queue.more();
  assert.equal(f.requests.length, 3);
  assert.equal(f.requests[2].page, 3);
  f.requests[2].resolve(page(['v2', 'v3', 'v3'], 3));
  await Promise.all([next, more]);
  assert.deepEqual(
    f.queue.items.value.map((v) => v.id),
    ['v1', 'v2', 'v3'],
  );
  f.stop();
});
test('failed page retries unchanged page and empty stale-total response stops location search', async () => {
  const f = fixture('missing');
  f.requests[0].reject(new Error('offline'));
  await flush();
  assert.equal(f.queue.error.value, 'offline');
  const retry = f.queue.more();
  assert.equal(f.requests[1].page, 1);
  f.requests[1].resolve(page([], 500));
  await retry;
  await flush();
  assert.equal(f.queue.total.value, 0);
  await f.queue.ensureNext();
  assert.equal(f.requests.length, 2);
  f.stop();
});

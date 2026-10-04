import test from 'node:test';
import assert from 'node:assert/strict';
import { createCommentFeed, emptyCommentFeed } from './commentFeed.ts';
const page = (ids, number = 1, total = ids.length) => ({
  items: ids.map((id) => ({ id })),
  page: number,
  page_size: 20,
  total,
});

test('switching searches aborts and discards old responses and errors', async () => {
  const state = emptyCommentFeed(),
    pending = [];
  const feed = createCommentFeed(
    state,
    (query, signal) =>
      new Promise((resolve, reject) => pending.push({ query, signal, resolve, reject })),
  );
  const old = feed.reset({ videoId: 'v', q: 'old' });
  const current = feed.reset({ videoId: 'v', q: 'current' });
  assert.equal(pending[0].signal.aborted, true);
  pending[1].resolve(page(['new']));
  await current;
  pending[0].resolve(page(['stale']));
  await old;
  assert.deepEqual(state.items, [{ id: 'new' }]);
  const staleError = feed.reset({ videoId: 'v', q: 'slow' });
  const otherVideo = feed.reset({ videoId: 'other' });
  pending[2].reject(new Error('old request failed'));
  await staleError;
  assert.equal(state.error, '');
  assert.equal(state.loading, true);
  pending[3].resolve(page([]));
  await otherVideo;
});

test('append preserves displayed comments and retry keeps the failed page', async () => {
  const state = emptyCommentFeed(),
    calls = [];
  const feed = createCommentFeed(state, async (query) => {
    calls.push(query);
    if (calls.length === 1) return page(['first'], 1, 45);
    if (calls.length === 2) throw new Error('temporary failure');
    return page(['first', 'second', 'second'], 2, 22);
  });
  await feed.reset({ videoId: 'v' });
  await feed.more();
  assert.deepEqual(state.items, [{ id: 'first' }]);
  assert.equal(state.page, 1);
  assert.equal(state.error, 'temporary failure');
  await feed.more();
  assert.deepEqual(
    calls.map((c) => c.page),
    [1, 2, 2],
  );
  assert.ok(calls.every((c) => c.sort === 'likes'));
  assert.deepEqual(state.items, [{ id: 'first' }, { id: 'second' }]);
  assert.equal(state.hasMore, false);
  assert.equal(state.error, '');
});

test('observer and button cannot request the same page concurrently', async () => {
  let complete,
    calls = 0;
  const state = emptyCommentFeed();
  const feed = createCommentFeed(state, () => {
    calls++;
    return new Promise((resolve) => {
      complete = resolve;
    });
  });
  const first = feed.reset({ videoId: 'v' });
  await feed.more();
  await feed.more();
  assert.equal(calls, 1);
  complete(page([]));
  await first;
  await feed.more();
  assert.equal(calls, 1);
  assert.equal(state.hasMore, false);
});

test('reply feed stays idle until expanded and cannot change state after unmount', async () => {
  const state = emptyCommentFeed();
  let complete,
    signal,
    calls = 0;
  const feed = createCommentFeed(state, (_, abort) => {
    calls++;
    signal = abort;
    return new Promise((resolve) => {
      complete = resolve;
    });
  });
  await feed.reset({ videoId: 'v', root: 'root' }, false);
  assert.equal(calls, 0);
  const first = feed.more();
  feed.dispose();
  assert.equal(signal.aborted, true);
  complete(page(['reply']));
  await first;
  await feed.more();
  assert.deepEqual(state.items, []);
  assert.equal(calls, 1);
});

test('an empty page ends automatic loading even if a stale total is larger', async () => {
  const state = emptyCommentFeed();
  let calls = 0;
  const feed = createCommentFeed(state, async () => {
    calls++;
    return page([], 1, 999);
  });
  await feed.reset({ videoId: 'v' });
  await feed.more();
  assert.equal(calls, 1);
  assert.equal(state.hasMore, false);
});

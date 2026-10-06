import test from 'node:test';
import assert from 'node:assert/strict';
import { createCommentSeekQueue } from './commentSeek.ts';

const scope = { request: 2, partId: 'p2', identity: 4 };
test('a cross-part seek waits through loading and consumes zero seconds exactly once', () => {
  const queue = createCommentSeekQueue();
  queue.enqueue({ ...scope, request: 1, partId: 'p1' }, 120, true, 300);
  queue.clear(); // new part setup, before the view queues its new intent after nextTick
  assert.equal(queue.enqueue(scope, 0, false, 300), true);
  assert.equal(queue.pending, true); // ready must skip saved history even at 0 seconds
  assert.deepEqual(queue.consume(scope, 298), { ...scope, seconds: 0, play: false });
  assert.equal(queue.consume(scope, 298), null);
});
test('rapid timestamps preserve only the latest intent while media or signed URL is loading', () => {
  const queue = createCommentSeekQueue();
  queue.enqueue(scope, 3, false, 300);
  queue.enqueue(scope, 242, true, 300);
  assert.equal(queue.pending, true); // renewal does not consume until restore runs
  assert.deepEqual(queue.consume(scope, 300), { ...scope, seconds: 242, play: true });
});
test('old source, part and login identities cannot receive a pending seek', () => {
  for (const changed of [{ request: 3 }, { partId: 'p3' }, { identity: 5 }]) {
    const queue = createCommentSeekQueue();
    queue.enqueue(scope, 242, true, 300);
    assert.equal(queue.consume({ ...scope, ...changed }, 300), null);
    assert.equal(queue.pending, false);
  }
});
test('seek validates both catalog duration and the real loaded duration instead of clamping', () => {
  const queue = createCommentSeekQueue();
  for (const seconds of [-1, NaN, Infinity, 300])
    assert.equal(queue.enqueue(scope, seconds, true, 300), false);
  queue.enqueue(scope, 242, true, 300);
  assert.equal(queue.consume(scope, 240), null);
  assert.equal(queue.pending, false);
});

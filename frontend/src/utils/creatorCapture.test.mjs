import test from 'node:test';
import assert from 'node:assert/strict';
import { submitCreatorCapture } from './creatorCapture.ts';

const ids = Array.from({ length: 30 }, (_, index) => `BV${String(index).padStart(10, '0')}`);
const counts = (batch) => ({ queued: batch.length, reused: 0, skipped: 0 });

test('30 selections are sent as 10 sequential batches, never overlapping', async () => {
  const batches = [],
    progress = [];
  let running = 0,
    peak = 0;
  const result = await submitCreatorCapture(
    ids,
    async (batch) => {
      running++;
      peak = Math.max(peak, running);
      batches.push(batch);
      await Promise.resolve();
      running--;
      return counts(batch);
    },
    (value) => progress.push(value),
  );
  assert.equal(peak, 1);
  assert.deepEqual(
    batches.map((batch) => batch.length),
    Array(10).fill(3),
  );
  assert.deepEqual(batches.flat(), ids);
  assert.deepEqual(progress[0].remaining, ids.slice(3));
  assert.equal(result.queued, 30);
  assert.deepEqual(result.remaining, []);
});

test('a source cooldown stops immediately and retains successful batches and remaining choices', async () => {
  const error = Object.assign(new Error('source cooldown'), {
    status: 429,
    retryAfterSeconds: 1800,
  });
  let calls = 0;
  const progress = [];
  const result = await submitCreatorCapture(
    ids,
    async (batch) => {
      if (++calls === 2) throw error;
      return { queued: 1, reused: 1, skipped: 1 };
    },
    (value) => progress.push(value),
  );
  assert.equal(calls, 2);
  assert.equal(result.failure, error);
  assert.equal(result.queued, 1);
  assert.equal(result.reused, 1);
  assert.equal(result.skipped, 1);
  assert.deepEqual(result.completed, ids.slice(0, 3));
  assert.deepEqual(result.remaining, ids.slice(3));
  assert.equal(progress.length, 1);
});

test('an uncertain network failure is not retried or treated as accepted', async () => {
  let calls = 0;
  const result = await submitCreatorCapture(
    ids,
    async () => {
      calls++;
      throw new Error('connection lost');
    },
    () => assert.fail('No confirmed batch'),
  );
  assert.equal(calls, 1);
  assert.deepEqual(result.remaining, ids);
  assert.equal(result.queued, 0);
});

test('switching creator/account/session during an active request stops later batches and stale UI updates', async () => {
  let active = true,
    calls = 0;
  const result = await submitCreatorCapture(
    ids,
    async (batch) => {
      calls++;
      active = false;
      return counts(batch);
    },
    () => assert.fail('Stale UI update'),
    () => active,
  );
  assert.equal(calls, 1);
  assert.equal(result.cancelled, true);
});

test('manual stop waits for current batch confirmation then retains its committed progress', async () => {
  let keepGoing = true,
    calls = 0;
  const progress = [];
  const result = await submitCreatorCapture(
    ids,
    async (batch) => {
      calls++;
      keepGoing = false;
      return counts(batch);
    },
    (value) => progress.push(value),
    () => true,
    () => keepGoing,
  );
  assert.equal(calls, 1);
  assert.equal(result.cancelled, true);
  assert.equal(result.queued, 3);
  assert.deepEqual(result.remaining, ids.slice(3));
  assert.equal(progress.length, 1);
});

test('duplicate selections are not submitted twice and empty selections do no work', async () => {
  const calls = [];
  const send = async (batch) => {
    calls.push(batch);
    return counts(batch);
  };
  const result = await submitCreatorCapture([ids[0], ids[0], ids[1]], send, () => {});
  assert.equal(result.queued, 2);
  await submitCreatorCapture([], send, () => {});
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], ids.slice(0, 2));
});

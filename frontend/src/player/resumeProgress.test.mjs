import test from 'node:test';
import assert from 'node:assert/strict';
import { preloadProgress, validResumePosition } from './resumeProgress.ts';

test('one preloaded progress result is shared by initial setup and protocol recovery', async () => {
  let reads = 0;
  const progress = preloadProgress(async () => {
    reads++;
    return { position: 38.85 };
  }, new AbortController().signal);
  assert.equal(progress.peek(), 0);
  assert.equal(await progress.ready, 38.85);
  assert.equal(await progress.ready, 38.85);
  assert.equal(progress.peek(), 38.85);
  assert.equal(reads, 1);
});
test('slow progress expires on the original request budget and cannot seek later', async () => {
  let respond, signal;
  const progress = preloadProgress(
    (requestSignal) => {
      signal = requestSignal;
      return new Promise((resolve) => {
        respond = resolve;
      });
    },
    new AbortController().signal,
    { timeoutMs: 5 },
  );
  assert.equal(await progress.ready, 0);
  assert.equal(signal.aborted, true);
  respond({ position: 80 });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(progress.peek(), 0);
});
test('switching part cancels pending resume and anonymous/non-resume setup does no read', async () => {
  const parent = new AbortController();
  const progress = preloadProgress(async () => new Promise(() => {}), parent.signal);
  parent.abort();
  assert.equal(await progress.ready, 0);
  const disabled = preloadProgress(
    () => assert.fail('disabled read'),
    new AbortController().signal,
    { enabled: false },
  );
  assert.equal(await disabled.ready, 0);
});
test('unavailable or invalid progress cannot block playback or seek outside the archive', async () => {
  for (const value of [undefined, { position: NaN }, { position: Infinity }, { position: -5 }]) {
    const progress = preloadProgress(async () => value, new AbortController().signal);
    assert.equal(await progress.ready, 0);
  }
  const failed = preloadProgress(async () => {
    throw new Error('offline');
  }, new AbortController().signal);
  assert.equal(await failed.ready, 0);
  assert.equal(validResumePosition(38.85, 331.936), 38.85);
  for (const [position, duration] of [
    [100, 100],
    [98, 100],
    [10, NaN],
    [Infinity, 100],
    [-3, 100],
    [10, 0],
  ])
    assert.equal(validResumePosition(position, duration), 0);
});

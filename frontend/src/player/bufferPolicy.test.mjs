import { test } from 'node:test';
import assert from 'node:assert/strict';
import { bufferPolicy, bufferedAhead, shouldRecoverStall } from './bufferPolicy.ts';

test('buffer sizing depends on bitrate, playback rate and observed network, never file duration/size', () => {
  const start = bufferPolicy({ bitrateBps: 8e6 });
  const stable = bufferPolicy({ bitrateBps: 8e6, throughputBps: 40e6 });
  const fluctuating = bufferPolicy({ bitrateBps: 8e6, throughputBps: 9e6 });
  assert.equal(start.maxMaxBufferLength, 12);
  assert.equal(stable.maxMaxBufferLength, 24);
  assert.ok(fluctuating.maxMaxBufferLength > stable.maxMaxBufferLength);
  assert.equal(
    bufferPolicy({ bitrateBps: 8e6, throughputBps: 40e6, playbackRate: 2 }).maxBufferLength,
    48,
  );
  const highRate = bufferPolicy({ bitrateBps: 80e6, throughputBps: 90e6 });
  assert.ok(highRate.maxBufferLength <= 6);
  assert.ok(highRate.backBufferLength <= 3);
  assert.equal(
    bufferPolicy({ bitrateBps: 8e6, stalled: true, connection: { saveData: true } })
      .maxBufferLength,
    8,
  );
  assert.equal(bufferPolicy({ bitrateBps: NaN, throughputBps: Infinity }).maxBufferLength, 12);
});

test('a discontinuity or stale buffered range never counts as usable look-ahead', () => {
  const ranges = [
    [0, 10],
    [20, 30],
  ];
  const video = {
    currentTime: 5,
    buffered: { length: 2, start: (i) => ranges[i][0], end: (i) => ranges[i][1] },
  };
  assert.equal(bufferedAhead(video), 5);
  video.currentTime = 15;
  assert.equal(bufferedAhead(video), 0);
  video.currentTime = 29.5;
  assert.equal(bufferedAhead(video), 0.5);
});

test('stall recovery excludes pause, decoder stalls, quick seeks and unknown native transfer rates', () => {
  const input = {
    elapsedMs: 8000,
    paused: false,
    readyState: 2,
    bufferedSeconds: 0,
    bytesPerSecond: 0,
    activeRequests: 1,
    transferState: 'stalled',
    bitrateBps: 8e6,
    playbackRate: 1,
  };
  assert.ok(shouldRecoverStall(input));
  for (const patch of [
    { elapsedMs: 7999 },
    { paused: true },
    { readyState: 3 },
    { bufferedSeconds: 2 },
    { bytesPerSecond: null },
    { bytesPerSecond: 2000000 },
    { activeRequests: 0 },
    { transferState: 'idle' },
    { transferState: 'unknown' },
  ])
    assert.equal(shouldRecoverStall({ ...input, ...patch }), false);
  assert.ok(shouldRecoverStall({ ...input, elapsedMs: 15000, bytesPerSecond: 500000 }));
  // Complete/aborted downloads can still be decoding while readyState is 2.
  assert.equal(
    shouldRecoverStall({ ...input, elapsedMs: 60000, activeRequests: 0, transferState: 'idle' }),
    false,
  );
});

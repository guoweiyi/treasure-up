import test from 'node:test';
import assert from 'node:assert/strict';
import { createTransferMeter, emptyTransferSnapshot, formatTransferRate } from './transferMeter.ts';

test('unobservable native playback stays unknown instead of showing fabricated zero or probe speed', () => {
  const meter = createTransferMeter(() => 0);
  assert.deepEqual(meter.snapshot(), emptyTransferSnapshot());
  meter.sample('unregistered-probe', 5_000_000);
  assert.deepEqual(meter.snapshot(), emptyTransferSnapshot());
});

test('real cumulative byte deltas produce byte/sec and count concurrent audio plus video once', () => {
  let now = 1000;
  const meter = createTransferMeter(() => now);
  const video = {},
    audio = {};
  meter.start(video);
  meter.start(audio);
  now += 500;
  meter.sample(video, 2_000_000);
  meter.sample(audio, 500_000);
  const value = meter.snapshot();
  assert.equal(value.bytesPerSecond, 5_000_000);
  assert.equal(value.state, 'receiving');
  assert.equal(value.transferredBytes, 2_500_000);
  assert.equal(value.activeRequests, 2);
  assert.equal(formatTransferRate(value.bytesPerSecond), '5 MB/s');
  meter.sample(video, 2_000_000);
  meter.sample(audio, 500_000);
  assert.equal(meter.snapshot().transferredBytes, 2_500_000);
});

test('stalled downloads age to zero while later bytes recover immediately', () => {
  let now = 0;
  const meter = createTransferMeter(() => now);
  meter.start('request');
  now = 250;
  meter.sample('request', 500_000);
  assert.equal(meter.snapshot().bytesPerSecond, 2_000_000);
  now = 1750;
  meter.sample('request', 500_000);
  assert.equal(meter.snapshot().state, 'stalled');
  assert.equal(meter.snapshot().bytesPerSecond, 0);
  now = 2000;
  meter.sample('request', 1_000_000);
  assert.equal(meter.snapshot().state, 'receiving');
  assert.ok(meter.snapshot().bytesPerSecond > 0);
});

test('idle, failed and completed traffic does not leave an old nonzero live rate', () => {
  let now = 0;
  const meter = createTransferMeter(() => now);
  meter.reset(true);
  assert.equal(meter.snapshot().state, 'idle');
  meter.start('one');
  assert.equal(meter.snapshot().state, 'waiting');
  now = 500;
  meter.finish('one', 1024);
  assert.equal(meter.snapshot().transferredBytes, 1024);
  assert.equal(meter.snapshot().state, 'idle');
  assert.equal(meter.snapshot().bytesPerSecond, 0);
  meter.sample('one', 2048);
  assert.equal(meter.snapshot().transferredBytes, 1024);
  now = 10000;
  meter.start('two');
  assert.equal(meter.snapshot().state, 'waiting');
  meter.abort('two');
  meter.sample('two', 99_000);
  assert.equal(meter.snapshot().transferredBytes, 1024);
  meter.reset();
  assert.deepEqual(meter.snapshot(), emptyTransferSnapshot());
});

test('retry counters, invalid measurements and old playback callbacks cannot inflate a transfer', () => {
  let now = 0;
  const meter = createTransferMeter(() => now);
  const id = { url: 'https://cdn.example.test/private?token=not-for-display' };
  meter.start(id);
  now = 500;
  meter.sample(id, 1024);
  for (const invalid of [NaN, Infinity, -5, 512, 1024]) meter.sample(id, invalid);
  assert.equal(meter.snapshot().transferredBytes, 1024);
  assert.equal(JSON.stringify(meter.snapshot()).includes('token'), false);
  meter.start(id); // Explicit retry, with a fresh cumulative counter.
  now = 750;
  meter.sample(id, 512);
  assert.equal(meter.snapshot().transferredBytes, 1536);
  meter.reset(true);
  meter.finish(id, 999_999);
  assert.equal(meter.snapshot().transferredBytes, 0);
  assert.equal(meter.snapshot().bytesPerSecond, 0);
});

test('display units distinguish bytes from bitrate and unknown from observed zero', () => {
  assert.equal(formatTransferRate(null), '—');
  assert.equal(formatTransferRate(NaN), '—');
  assert.equal(formatTransferRate(-1), '—');
  assert.equal(formatTransferRate(0), '0 B/s');
  assert.equal(formatTransferRate(950), '950 B/s');
  assert.equal(formatTransferRate(12_500), '12.5 KB/s');
  assert.equal(formatTransferRate(5_000_000), '5 MB/s');
  assert.equal(formatTransferRate(1_250_000_000), '1.25 GB/s');
});

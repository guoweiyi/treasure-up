import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createMediaAdapter } from './mediaAdapter.ts';

test('file playback keeps custom controls and explicitly requests inline video', async () => {
  const attributes = new Map();
  const video = {
    controls: true,
    playsInline: false,
    src: '',
    setAttribute(name, value) {
      attributes.set(name, value);
    },
  };
  const adapter = createMediaAdapter(() => assert.fail('file playback should not initialize HLS'));
  await adapter.attach(video, '/synthetic.mp4', 'file');
  assert.equal(video.src, '/synthetic.mp4');
  assert.equal(video.controls, false);
  assert.equal(video.playsInline, true);
  assert.ok(attributes.has('playsinline'));
  assert.ok(attributes.has('webkit-playsinline'));
  adapter.destroy();
});
test('switching media resets sampled statistics and ignores callbacks from a disposed observer', async () => {
  const previousObserver = globalThis.PerformanceObserver;
  const observers = [],
    published = [];
  globalThis.PerformanceObserver = class {
    constructor(callback) {
      this.callback = callback;
      observers.push(this);
    }
    observe() {}
    disconnect() {
      this.disconnected = true;
    }
  };
  const video = {
    videoWidth: 1920,
    videoHeight: 1080,
    setAttribute() {},
    getVideoPlaybackQuality: () => ({ droppedVideoFrames: 2, totalVideoFrames: 100 }),
  };
  const adapter = createMediaAdapter(
    () => assert.fail('unexpected media error'),
    (stats) => published.push(stats),
  );
  const sample = {
    name: 'http://localhost/a?token=secret',
    transferSize: 1000,
    encodedBodySize: 800,
    responseStart: 10,
    responseEnd: 20,
  };
  try {
    await adapter.attach(video, '/a?token=secret', 'file');
    observers[0].callback({ getEntries: () => [sample] });
    assert.equal(published.at(-1).networkBps, 640000);
    await adapter.attach(video, '/b?token=private', 'file');
    assert.equal(observers[0].disconnected, true);
    const count = published.length;
    observers[0].callback({ getEntries: () => [sample] });
    assert.equal(published.length, count);
    assert.equal(published.at(-1).networkBps, null);
    assert.equal(published.at(-1).loadedFragments, null);
    assert.equal(published.at(-1).droppedFrames, 2);
    assert.equal(JSON.stringify(published).includes('token='), false);
  } finally {
    adapter.destroy();
    globalThis.PerformanceObserver = previousObserver;
  }
});
test('late HLS engine initialization cannot replace a newer file attachment', async () => {
  const video = { src: '', setAttribute() {}, canPlayType: () => '' };
  const adapter = createMediaAdapter(() =>
    assert.fail('a disposed HLS request must not report errors'),
  );
  try {
    const stale = adapter.attach(video, '/old.m3u8', 'hls');
    await adapter.attach(video, '/current.mp4', 'file');
    await stale;
    assert.equal(video.src, '/current.mp4');
  } finally {
    adapter.destroy();
  }
});

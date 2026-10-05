import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createMediaAdapter } from './mediaAdapter.ts';

test('MSE requests a known resume position from its first fragment selection', async () => {
  const options = [];
  class FakeHls {
    static version = 'fixture';
    static Events = {};
    static isSupported() {
      return true;
    }
    constructor(value) {
      options.push(value);
    }
    on() {}
    destroy() {}
    loadSource() {}
    attachMedia() {}
  }
  const adapter = createMediaAdapter(
    () => assert.fail('unexpected error'),
    () => {},
    async () => ({ default: FakeHls }),
  );
  const video = { src: '', setAttribute() {}, canPlayType: () => '' };
  try {
    await adapter.attach(video, '/fixture.m3u8', 'hls', undefined, 38.85);
    assert.equal(options[0].startPosition, 38.85);
    await adapter.attach(video, '/fixture.m3u8', 'hls', undefined, NaN);
    assert.equal(Object.hasOwn(options[1], 'startPosition'), false);
    await adapter.attach(video, '/fixture.m3u8', 'hls', undefined, -1);
    assert.equal(Object.hasOwn(options[2], 'startPosition'), false);
  } finally {
    adapter.destroy();
  }
});

test('a capable non-Safari WebView uses native HLS without exposing native controls', async () => {
  const stats = [];
  const video = { src: '', controls: true, setAttribute() {}, canPlayType: () => 'maybe' };
  const adapter = createMediaAdapter(
    () => assert.fail('native HLS must not initialize MSE'),
    (value) => stats.push(value),
  );
  try {
    await adapter.attach(video, '/atmos.m3u8', 'hls');
    assert.equal(video.src, '/atmos.m3u8');
    assert.equal(video.controls, false);
    assert.equal(video.playsInline, true);
    assert.equal(stats.at(-1).engine, '浏览器原生 HLS');
    assert.equal(stats.at(-1).loadedFragments, null);
  } finally {
    adapter.destroy();
  }
});

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

test('native HLS probes file decoding while MSE probes media-source, without applying old results after a switch', async () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  const requests = [],
    pending = [],
    stats = [];
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: {
      mediaCapabilities: {
        decodingInfo(config) {
          requests.push(config);
          return new Promise((resolve) => pending.push(resolve));
        },
      },
    },
  });
  const adapter = createMediaAdapter(
    () => {},
    (value) => stats.push(value),
  );
  const video = { src: '', setAttribute() {}, canPlayType: () => 'probably' };
  try {
    await adapter.attach(video, '/native.m3u8', 'hls', { audio_codec: 'eac3' });
    assert.equal(requests[0].type, 'file');
    video.canPlayType = () => '';
    const mse = adapter.attach(video, '/mse.m3u8', 'hls', { audio_codec: 'eac3' });
    assert.equal(requests[1].type, 'media-source');
    await adapter.attach(video, '/aac.mp4', 'file', { audio_codec: 'aac' });
    await mse;
    pending.forEach((resolve) => resolve({ supported: false }));
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(stats.at(-1).engine, 'HTMLVideoElement · 文件直读');
    assert.equal(stats.at(-1).audioSupport.ec3, 'unknown');
    assert.equal(video.src, '/aac.mp4');
  } finally {
    adapter.destroy();
    if (descriptor) Object.defineProperty(globalThis, 'navigator', descriptor);
    else delete globalThis.navigator;
  }
});

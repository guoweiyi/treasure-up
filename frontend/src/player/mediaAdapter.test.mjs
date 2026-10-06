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
    async () => ({ default: FakeHls, workerPath: '/assets/hls.worker.fixture.js' }),
  );
  const video = { src: '', setAttribute() {}, canPlayType: () => '' };
  try {
    await adapter.attach(video, '/fixture.m3u8', 'hls', undefined, 38.85);
    assert.equal(options[0].startPosition, 38.85);
    assert.equal(options[0].workerPath, '/assets/hls.worker.fixture.js');
    assert.equal(options[0].enableWorker, true);
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
    assert.equal(stats.at(-1).transfer.state, 'unknown');
    assert.equal(stats.at(-1).transfer.bytesPerSecond, null);
  } finally {
    adapter.destroy();
  }
});

function transferFixture(t) {
  let now = 100;
  const timers = new Set(),
    instances = [],
    published = [],
    errors = [];
  t.mock.method(performance, 'now', () => now);
  t.mock.method(globalThis, 'setInterval', (callback, delay) => {
    assert.equal(delay, 250);
    timers.add(callback);
    return callback;
  });
  t.mock.method(globalThis, 'clearInterval', (callback) => timers.delete(callback));
  class FakeHls {
    static version = 'fixture';
    static Events = Object.fromEntries(
      ['ERROR', 'BUFFER_CREATED', 'FRAG_LOADING', 'FRAG_LOADED', 'FRAG_LOAD_EMERGENCY_ABORTED'].map(
        (key) => [key, key],
      ),
    );
    static ErrorTypes = { NETWORK_ERROR: 'network' };
    static isSupported() {
      return true;
    }
    constructor(config) {
      this.config = config;
      this.handlers = new Map();
      instances.push(this);
    }
    on(event, callback) {
      this.handlers.set(event, callback);
    }
    emit(event, data) {
      this.handlers.get(event)?.(event, data);
    }
    destroy() {
      this.destroyed = true;
    }
    stopLoad() {
      this.stopped = true;
    }
    loadSource() {}
    attachMedia() {}
  }
  const video = Object.assign(new EventTarget(), {
    src: '',
    playbackRate: 1,
    setAttribute() {},
    canPlayType: () => '',
  });
  const adapter = createMediaAdapter(
    (...error) => errors.push(error),
    (value) => published.push(value),
    async () => ({ default: FakeHls }),
  );
  t.after(() => adapter.destroy());
  return {
    Hls: FakeHls,
    adapter,
    video,
    instances,
    published,
    errors,
    timers,
    tick(ms = 250) {
      now += ms;
      for (const callback of timers) callback();
    },
    latest: () => published.at(-1),
  };
}
const fragment = (sn = 1, type = 'main') => ({
  sn,
  type,
  level: 0,
  cc: 0,
  url: `/media/${type}/${sn}.m4s?token=private`,
  stats: { loaded: 0, aborted: false, loading: { start: 100, end: 0 } },
});

test('progressive HLS reports actual body progress before completion and drops stopped transfers to zero', async (t) => {
  const f = transferFixture(t);
  // Chromium 154 advertises native HLS as well; ordinary desktop media still uses MSE.
  f.video.canPlayType = () => 'maybe';
  await f.adapter.attach(f.video, '/media/index.m3u8', 'hls');
  const hls = f.instances[0],
    frag = fragment();
  assert.equal(hls.config.progressive, true);
  assert.equal(Object.hasOwn(hls.config, 'fLoader'), false);
  assert.equal(Object.hasOwn(hls.config, 'loader'), false);
  hls.emit('FRAG_LOADING', { frag });
  // The official loader replaces this reference after FRAG_LOADING for init segments.
  frag.stats = { ...frag.stats, loaded: 1_250_000 };
  f.tick();
  assert.equal(f.latest().loadedFragments, 0);
  assert.equal(f.latest().transfer.bytesPerSecond, 5_000_000);
  assert.equal(f.latest().transfer.activeRequests, 1);
  f.tick(1500);
  assert.equal(f.latest().transfer.state, 'stalled');
  assert.equal(f.latest().transfer.bytesPerSecond, 0);
  frag.stats.loaded += 250_000;
  f.tick();
  assert.ok(f.latest().transfer.bytesPerSecond > 0);
  hls.emit('FRAG_LOADED', { frag });
  assert.equal(f.latest().loadedFragments, 1);
  assert.equal(f.latest().transfer.state, 'idle');
  assert.equal(f.latest().transfer.bytesPerSecond, 0);
  assert.equal(f.latest().transfer.transferredBytes, 1_500_000);
  assert.equal(JSON.stringify(f.published).includes('token='), false);
});

test('native-only and native EC-3 playback keep unknown transfer without constructing an MSE engine', async (t) => {
  const f = transferFixture(t);
  f.video.canPlayType = () => 'probably';
  await f.adapter.attach(f.video, '/atmos.m3u8', 'hls', { audio_codec: 'eac3', dolby_atmos: true });
  assert.equal(f.instances.length, 0);
  assert.equal(f.latest().engine, '浏览器原生 HLS');
  assert.equal(f.latest().transfer.bytesPerSecond, null);
  f.Hls.isSupported = () => false;
  await f.adapter.attach(f.video, '/native-only.m3u8', 'hls', { audio_codec: 'aac' });
  assert.equal(f.instances.length, 0);
  assert.equal(f.latest().engine, '浏览器原生 HLS');
  assert.equal(f.video.src, '/native-only.m3u8');
  assert.equal(f.latest().transfer.state, 'unknown');
});

test('HLS observes concurrent audio/video, resets retries, and aborts real errored requests', async (t) => {
  const f = transferFixture(t);
  await f.adapter.attach(f.video, '/media/index.m3u8', 'hls');
  const hls = f.instances[0],
    main = fragment(),
    audio = fragment(1, 'audio');
  for (const frag of [main, audio]) hls.emit('FRAG_LOADING', { frag });
  main.stats.loaded = 1_000_000;
  audio.stats.loaded = 250_000;
  f.tick();
  assert.equal(f.latest().transfer.bytesPerSecond, 5_000_000);
  assert.equal(f.latest().transfer.activeRequests, 2);
  main.stats.aborted = true;
  hls.emit('FRAG_LOAD_EMERGENCY_ABORTED', { frag: audio });
  f.tick();
  assert.equal(f.latest().transfer.activeRequests, 0);
  main.stats = { loaded: 0, aborted: false, loading: { start: 600, end: 0 } };
  hls.emit('FRAG_LOADING', { frag: main });
  main.stats.loaded = 250_000;
  f.tick();
  assert.equal(f.latest().transfer.transferredBytes, 1_500_000);
  hls.emit('ERROR', { type: 'network', fatal: false, frag: main });
  assert.equal(f.latest().transfer.activeRequests, 0);
  assert.equal(f.latest().transfer.bytesPerSecond, 0);
  hls.emit('FRAG_LOADING', { frag: main });
  hls.emit('ERROR', { type: 'network', fatal: true });
  assert.equal(hls.stopped, true);
  assert.equal(f.latest().transfer.activeRequests, 0);
  assert.equal(f.errors[0][0], 'network');
});

test('init segments finish from loader timing without FRAG_LOADED and stale retry stats are not counted twice', async (t) => {
  const f = transferFixture(t);
  await f.adapter.attach(f.video, '/media/index.m3u8', 'hls');
  const hls = f.instances[0],
    init = fragment('initSegment');
  hls.emit('FRAG_LOADING', { frag: init });
  init.stats.loaded = 32_000;
  f.tick();
  assert.equal(f.latest().transfer.activeRequests, 1);
  init.stats.loading.end = 400;
  f.tick();
  assert.equal(f.latest().transfer.activeRequests, 0);
  assert.equal(f.latest().transfer.state, 'idle');
  assert.equal(f.latest().transfer.bytesPerSecond, 0);
  assert.equal(f.latest().transfer.transferredBytes, 32_000);
  // FRAG_LOADING for a repeated init is emitted before the official loader
  // assigns its new stats. Do not re-count the completed previous request.
  hls.emit('FRAG_LOADING', { frag: init });
  f.tick();
  assert.equal(f.latest().transfer.transferredBytes, 32_000);
  init.stats = { loaded: 16_000, aborted: false, loading: { start: 700, end: 0 } };
  f.tick();
  assert.equal(f.latest().transfer.activeRequests, 1);
  assert.equal(f.latest().transfer.transferredBytes, 48_000);
  init.stats.loading.end = 1100;
  f.tick();
  assert.equal(f.latest().transfer.activeRequests, 0);
});

test('unknown HLS counters remain unknown and abandoned fragment references are bounded', async (t) => {
  const f = transferFixture(t);
  await f.adapter.attach(f.video, '/media/index.m3u8', 'hls');
  const hls = f.instances[0],
    unknown = { ...fragment(), stats: {} };
  hls.emit('FRAG_LOADING', { frag: unknown });
  f.tick();
  assert.equal(f.latest().transfer.bytesPerSecond, null);
  hls.emit('FRAG_LOADED', { frag: unknown });
  assert.equal(f.latest().transfer.state, 'unknown');
  assert.equal(hls.config.maxBufferLength, 12);
  for (let index = 0; index < 80; index++) hls.emit('FRAG_LOADING', { frag: fragment(index) });
  f.tick();
  assert.equal(f.latest().transfer.activeRequests, 64);
});

test('completed media throughput, rate changes and connection hints only adapt buffer policy', async (t) => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  const connection = Object.assign(new EventTarget(), { saveData: false, effectiveType: '4g' });
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { connection } });
  const f = transferFixture(t);
  try {
    await f.adapter.attach(f.video, '/media/index.m3u8', 'hls', {
      video_bitrate_bps: 7_000_000,
      audio_bitrate_bps: 1_000_000,
    });
    const hls = f.instances[0];
    assert.equal(hls.config.maxBufferLength, 12);
    const fast = fragment();
    hls.emit('FRAG_LOADING', { frag: fast });
    fast.stats.loaded = 2_000_000;
    fast.stats.loading.end = 1100;
    hls.emit('FRAG_LOADED', { frag: fast });
    assert.equal(hls.config.maxBufferLength, 24);
    const slow = fragment(2);
    hls.emit('FRAG_LOADING', { frag: slow });
    slow.stats.loaded = 100_000;
    slow.stats.loading.end = 2100;
    hls.emit('FRAG_LOADED', { frag: slow });
    assert.equal(hls.config.maxBufferLength, 48);
    connection.saveData = true;
    connection.dispatchEvent(new Event('change'));
    assert.equal(hls.config.maxBufferLength, 8);
    f.video.playbackRate = 2;
    f.video.dispatchEvent(new Event('ratechange'));
    assert.equal(hls.config.maxBufferLength, 16);
    connection.saveData = false;
    connection.dispatchEvent(new Event('change'));
    f.adapter.onWaiting();
    assert.equal(hls.config.maxBufferLength, 50);
    assert.equal(f.latest().transfer.bytesPerSecond, 0);
    assert.equal(Object.hasOwn(hls, 'currentLevel'), false);
    const prior = { ...hls.config };
    await f.adapter.attach(f.video, '/native.mp4', 'file');
    connection.saveData = true;
    connection.dispatchEvent(new Event('change'));
    f.video.dispatchEvent(new Event('ratechange'));
    assert.deepEqual(hls.config, prior);
  } finally {
    f.adapter.destroy();
    if (descriptor) Object.defineProperty(globalThis, 'navigator', descriptor);
    else delete globalThis.navigator;
  }
});

test('a media generation switch clears requests and timers and ignores disposed HLS events', async (t) => {
  const f = transferFixture(t);
  await f.adapter.attach(f.video, '/old.m3u8', 'hls');
  const hls = f.instances[0],
    frag = fragment();
  hls.emit('FRAG_LOADING', { frag });
  frag.stats.loaded = 1_250_000;
  f.tick();
  assert.ok(f.latest().transfer.bytesPerSecond > 0);
  await f.adapter.attach(f.video, '/new.mp4', 'file');
  assert.equal(hls.destroyed, true);
  assert.equal(f.timers.size, 1);
  assert.equal(f.latest().transfer.state, 'unknown');
  const count = f.published.length;
  hls.emit('FRAG_LOADING', { frag });
  hls.emit('FRAG_LOADED', { frag });
  hls.emit('ERROR', { type: 'network', fatal: true });
  assert.equal(f.published.length, count);
  assert.equal(f.errors.length, 0);
  f.adapter.destroy();
  assert.equal(f.timers.size, 0);
  f.tick();
  assert.equal(f.published.length, count);
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
    async () => ({
      default: class {
        static Events = {};
        static version = 'fixture';
        static isSupported() {
          return true;
        }
        constructor(config) {
          this.config = config;
        }
        on() {}
        destroy() {}
        loadSource() {}
        attachMedia() {}
      },
    }),
  );
  const video = { src: '', setAttribute() {}, canPlayType: () => 'probably' };
  try {
    await adapter.attach(video, '/native.m3u8', 'hls', { audio_codec: 'eac3' });
    assert.equal(requests[0].type, 'file');
    video.canPlayType = () => '';
    const mse = adapter.attach(video, '/mse.m3u8', 'hls', { audio_codec: 'eac3' });
    await mse;
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

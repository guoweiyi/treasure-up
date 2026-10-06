import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

// Exercise the installed, postinstall-patched implementation. Only parsed timing
// and unrelated metadata helpers are supplied here; MP4 bytes and browser MSE
// behavior belong to the media integration checks.
function loadSource(relativePath, dependencies = {}) {
  const source = readFileSync(
    new URL(`../../node_modules/hls.js/src/${relativePath}`, import.meta.url),
    'utf8',
  );
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  });
  const exports = {};
  const require = (name) => {
    assert.ok(Object.hasOwn(dependencies, name), `Unexpected dependency: ${name}`);
    return dependencies[name];
  };
  new Function('exports', 'require', '__USE_IFRAMES__', outputText)(exports, require, false);
  return exports;
}

const { ChunkMetadata } = loadSource('types/transmuxer.ts');
const { default: PassThroughRemuxer } = loadSource('remux/passthrough-remuxer.ts', {
  './mp4-remuxer': {
    flushTextTrackMetadataCueSamples: () => undefined,
    flushTextTrackUserdataCueSamples: () => undefined,
  },
  '../loader/fragment': { ElementaryStreamTypes: { AUDIO: 'audio', VIDEO: 'video' } },
  '../utils/codecs': { getCodecCompatibleName: (codec) => codec },
  '../utils/logger': {
    Logger: class {
      log() {}
      warn() {}
    },
  },
  '../utils/mp4-tools': {
    getSampleData: () => assert.fail('The fixture supplies parsed sample timing'),
    parseInitSegment: () => assert.fail('The fixture supplies parsed initialization'),
  },
});

function fixture() {
  const initData = [];
  initData[1] = initData.video = {
    id: 1,
    timescale: 15360,
    type: 'video',
    codec: 'av01.0.08M.08',
  };
  initData[2] = initData.audio = {
    id: 2,
    timescale: 48000,
    type: 'audio',
    codec: 'mp4a.40.2',
  };
  const remuxer = new PassThroughRemuxer(
    { removeAllListeners() {} },
    { progressive: true },
    {},
    {},
  );
  const resetInitSegment = () =>
    remuxer.resetInitSegment(new Uint8Array([1]), undefined, undefined, null);
  resetInitSegment();
  function push({
    start = 0,
    duration = 1,
    timeOffset = 0,
    level = 0,
    sn = 0,
    part = -1,
    id = 1,
    types = ['video', 'audio'],
    flush = false,
    iframe = false,
  } = {}) {
    const tracks = {};
    for (const type of types) {
      const { id: trackId, timescale } = initData[type];
      tracks[trackId] = {
        start: start * timescale,
        duration: duration * timescale,
        timescale,
        sampleCount: type === 'video' ? 30 : 48,
        trun: [],
        ptsMin: start * timescale,
        ptsMax: (start + duration) * timescale,
        keyFrameIndex: 0,
        keyFrameStart: start * timescale,
      };
    }
    return remuxer.remux(
      {},
      { samples: new Uint8Array([1]) },
      { samples: [] },
      { samples: [] },
      timeOffset,
      true,
      flush,
      'main',
      new ChunkMetadata(level, sn, id, 1, part, false, 6, iframe),
      initData,
      { tracks, includeSampleDetails: false },
    );
  }
  return { remuxer, push, resetInitSegment };
}

function near(actual, expected, label) {
  assert.ok(
    Number.isFinite(actual) && Math.abs(actual - expected) < 1e-8,
    `${label}: expected ${expected}, received ${actual}`,
  );
}

function assertTiming(result, start, end, initOffset) {
  const track = result.video || result.audio;
  assert.ok(track, 'Valid samples must produce an appendable track');
  near(track.startPTS, start, 'startPTS');
  near(track.endPTS, end, 'endPTS');
  near(track.startDTS, start, 'startDTS');
  near(track.endDTS, end, 'endDTS');
  near(
    result.initSegment.initPTS / result.initSegment.timescale,
    initOffset,
    'SourceBuffer timestamp offset basis',
  );
}

test('successive progressive moofs retain their timeline and the final flush retains its offset', () => {
  const { push } = fixture();
  for (let index = 0; index < 4; index++) {
    assertTiming(push({ start: index, id: index + 1 }), index, index + 1, 0);
  }
  // A flush may reuse the final push metadata rather than increment its id.
  assertTiming(push({ start: 4, id: 4, flush: true }), 4, 5, 0);
});

test('single-track moofs with different video and audio timescales keep their original alignment', () => {
  const { push } = fixture();
  const chunks = [
    { start: 10, duration: 1, types: ['video'] },
    { start: 10.95, duration: 0.05, types: ['audio'] },
    { start: 11, duration: 1, types: ['video'] },
    { start: 11.95, duration: 0.05, types: ['audio'] },
  ];
  for (const [index, chunk] of chunks.entries()) {
    const result = push({ ...chunk, timeOffset: 10, id: index + 1 });
    assertTiming(result, chunk.start, chunk.start + chunk.duration, 0);
    assert.equal(result.video.type, 'audiovideo');
  }
});

test('a new HLS fragment still validates a changed timestamp origin', () => {
  const { push } = fixture();
  assertTiming(push({ start: 100 }), 0, 1, 100);
  assertTiming(push({ start: 101, id: 2 }), 1, 2, 100);
  assertTiming(push({ start: 110, timeOffset: 6, sn: 1 }), 6, 7, 104);
  assertTiming(push({ start: 111, timeOffset: 6, sn: 1, id: 2 }), 7, 8, 104);
});

test('new LL-HLS parts and levels keep their own timestamp validation boundaries', () => {
  const { push } = fixture();
  assertTiming(push({ start: 100, timeOffset: 10, sn: 5, part: 0 }), 10, 11, 90);
  assertTiming(push({ start: 101, timeOffset: 10, sn: 5, part: 0, id: 2 }), 11, 12, 90);
  assertTiming(push({ start: 105, timeOffset: 12, sn: 5, part: 1, id: 3 }), 12, 13, 93);
  assertTiming(push({ start: 115, timeOffset: 12, sn: 5, part: 1, level: 1, id: 4 }), 12, 13, 103);
});

test('a chunk with no parsed media does not consume the next fragment timestamp validation', () => {
  const { push } = fixture();
  assertTiming(push(), 0, 1, 0);
  const empty = push({ sn: 1, timeOffset: 6, types: [] });
  assert.equal(empty.video, undefined);
  assert.equal(empty.audio, undefined);
  assertTiming(push({ sn: 1, start: 100, timeOffset: 6, id: 2 }), 6, 7, 94);
});

test('retry chunk ids that move backwards revalidate the same fragment identity', () => {
  const { push } = fixture();
  assertTiming(push({ start: 100, timeOffset: 10, id: 5 }), 10, 11, 90);
  assertTiming(push({ start: 101, timeOffset: 10, id: 6 }), 11, 12, 90);
  assertTiming(push({ start: 130, timeOffset: 30, id: 1 }), 30, 31, 100);
  assertTiming(push({ start: 131, timeOffset: 30, id: 2 }), 31, 32, 100);
});

test('I-frame metadata never bypasses timestamp validation for a repeated fragment', () => {
  const { push } = fixture();
  // Isolate the validation guard; this fixture does not exercise I-frame MP4 rewriting.
  assertTiming(push({ start: 100, timeOffset: 10, iframe: true }), 10, 11, 90);
  assertTiming(push({ start: 101, timeOffset: 10, id: 2, iframe: true }), 10, 11, 91);
  assertTiming(push({ start: 102, timeOffset: 10, id: 3, iframe: true }), 10, 11, 92);
});

const resets = {
  'resetTimeStamp with an unchanged default offset': ({ remuxer }, initial) => {
    remuxer.resetTimeStamp({
      baseTime: initial.initSegment.initPTS,
      timescale: initial.initSegment.timescale,
      trackId: initial.initSegment.trackId,
    });
  },
  'resetTimeStamp without a default offset': ({ remuxer }) => remuxer.resetTimeStamp(null),
  'resetNextTimestamp after a noncontiguous seek': ({ remuxer }) => remuxer.resetNextTimestamp(),
  'resetInitSegment after a track or initialization change': ({ resetInitSegment }) =>
    resetInitSegment(),
};
for (const [name, reset] of Object.entries(resets)) {
  test(`${name} revalidates even when the fragment identity is reused`, () => {
    const f = fixture();
    const initial = f.push({ start: 100, timeOffset: 10, sn: 3, level: 1 });
    assertTiming(initial, 10, 11, 90);
    assertTiming(f.push({ start: 101, timeOffset: 10, sn: 3, level: 1, id: 2 }), 11, 12, 90);
    reset(f, initial);
    assertTiming(f.push({ start: 130, timeOffset: 30, sn: 3, level: 1, id: 3 }), 30, 31, 100);
    assertTiming(f.push({ start: 131, timeOffset: 30, sn: 3, level: 1, id: 4 }), 31, 32, 100);
  });
}

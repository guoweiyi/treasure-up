import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { createCommentSeekQueue } from './commentSeek.ts';
import {
  loadWithProtocolFallback,
  withProtocolPreference,
  recoveryStartState,
  MediaLoadError,
} from './playbackRecovery.ts';

// Execute the component's actual renewal/seek functions with a deferred media
// play promise. Queue-only tests cannot detect the gap after restore consumes
// its first intent but before play finishes buffering. No browser is simulated.
const component = readFileSync(new URL('../components/ArchivePlayer.vue', import.meta.url), 'utf8');
const functions =
  component.slice(
    component.indexOf('async function renew('),
    component.indexOf('async function setup('),
  ) +
  component.slice(
    component.indexOf('function applyCommentSeek('),
    component.indexOf('defineExpose('),
  );
const executable = ts.transpile(functions, {
  target: ts.ScriptTarget.ES2022,
  module: ts.ModuleKind.ES2022,
});

function fixture() {
  let enter,
    resolve,
    reject,
    plays = 0;
  const playing = new Promise((done) => {
    enter = done;
  });
  const buffering = new Promise((done, fail) => {
    resolve = done;
    reject = fail;
  });
  const player = {
    currentTime: 10,
    duration: 300,
    video: { paused: true },
    playbackRate: 1,
    muted: false,
    template: { $player: { classList: { remove() {} } } },
    notice: {},
    pause() {
      this.video.paused = true;
    },
    play() {
      this.video.paused = false;
      plays++;
      enter();
      return buffering;
    },
  };
  const queue = createCommentSeekQueue();
  queue.enqueue({ request: 1, partId: 'p1', identity: 1 }, 120, true, 300);
  const scope = vm.createContext({
    art: player,
    requestedSeek: queue,
    renewal: 0,
    request: 1,
    activePartId: 'p1',
    renewalController: null,
    rejectMediaLoad: null,
    renewalSnapshot: null,
    startupPending: false,
    startupPlayRequested: false,
    renewing: false,
    waitingSince: 0,
    initialProgress: null,
    userVolume: { value: 0.7 },
    switching: { value: false },
    mediaLoading: { value: false },
    playbackProtocol: { value: 'auto' },
    variantId: { value: 'v1' },
    routeId: { value: '' },
    error: { value: '' },
    probing: { value: false },
    playback: { value: null },
    note: { value: '' },
    routeRecovery: {
      claim() {
        return null;
      },
    },
    protocolFallback: { input: (value) => value },
    sessionRevision: { value: 1 },
    props: { part: { id: 'p1', duration: 300 } },
    AbortController,
    MediaLoadError,
    withProtocolPreference,
    loadWithProtocolFallback,
    recoveryStartState,
    createPlayback: async () => ({ url: 'fixture', variant_id: 'v1' }),
    switchMedia: async () => {},
    applyVolume() {},
    clearBuffering() {},
    errorText: String,
  });
  vm.runInContext(executable, scope);
  return {
    scope,
    player,
    queue,
    playing,
    resolve,
    reject,
    plays: () => plays,
    renew: () => vm.runInContext('renew()', scope),
    seek: (seconds) => vm.runInContext(`seekTo(${seconds}, 'p1', true)`, scope),
  };
}

test('a later comment during signed URL restore buffering is applied when renewal finishes', async () => {
  const f = fixture();
  const renewal = f.renew();
  await f.playing;
  assert.equal(f.player.currentTime, 120);
  assert.equal(
    f.scope.renewalSnapshot.position,
    120,
    'a second renewal must retain the applied timestamp',
  );
  assert.equal(f.seek(180), true);
  assert.equal(f.seek(242), true);
  f.resolve();
  await renewal;
  assert.equal(f.player.currentTime, 242);
  assert.equal(f.queue.pending, false);
  assert.equal(f.player.video.paused, false);
});

test('a manual pause while restore buffers wins over an earlier comment play intent', async () => {
  const f = fixture();
  const renewal = f.renew();
  await f.playing;
  assert.equal(f.seek(242), true);
  f.player.pause();
  f.reject(new Error('play interrupted by pause'));
  await renewal;
  assert.equal(f.player.currentTime, 242);
  assert.equal(f.player.video.paused, true);
  assert.equal(f.plays(), 1, 'draining the queue must not resume a newly paused player');
  assert.equal(f.queue.pending, false);
});

test('an old renewal cannot consume a replacement part intent or seek after logout', async () => {
  for (const changePart of [false, true]) {
    const f = fixture();
    const renewal = f.renew();
    await f.playing;
    f.seek(242);
    if (changePart) {
      f.scope.request = 2;
      f.scope.activePartId = 'p2';
      f.queue.enqueue({ request: 2, partId: 'p2', identity: 1 }, 90, false, 300);
    } else f.scope.sessionRevision.value = 2;
    f.resolve();
    await renewal;
    assert.equal(f.player.currentTime, 120);
    assert.equal(f.queue.pending, changePart);
  }
});

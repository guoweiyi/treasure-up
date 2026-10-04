import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  queueTarget,
  createEndGuard,
  readQueuePreferences,
  queuePreferenceKey,
  playlistQuery,
  playlistScope,
} from './queue.ts';
const parts = [
  { id: 'p1', variants: [{}] },
  { id: 'missing', variants: [] },
  { id: 'p3', variants: [{}] },
];
const videos = [{ id: 'v1' }, { id: 'v2' }];
test('all modes finish playable parts of one BV before acting; missing parts are skipped', () => {
  for (const mode of ['pause', 'repeat', 'continuous'])
    assert.deepEqual(queueTarget(parts, 'p1', videos, 'v1', 1, mode), { kind: 'part', id: 'p3' });
  assert.deepEqual(queueTarget(parts, 'p3', videos, 'v1', 1, 'pause'), { kind: 'stop' });
  assert.deepEqual(queueTarget(parts, 'p3', videos, 'v1', 1, 'repeat'), { kind: 'part', id: 'p1' });
  assert.deepEqual(queueTarget(parts, 'p3', videos, 'v1', 1, 'continuous'), {
    kind: 'video',
    id: 'v2',
  });
  assert.deepEqual(queueTarget(parts, 'p3', videos, 'v2', 1, 'continuous'), { kind: 'stop' });
  assert.deepEqual(queueTarget(parts, 'p1', videos, 'v2', -1), { kind: 'video', id: 'v1' });
  assert.deepEqual(queueTarget([parts[0]], 'p1', videos, 'v1', 1, 'repeat'), { kind: 'replay' });
  const singleVideo = [{ id: 'v1' }];
  assert.deepEqual(queueTarget(parts, 'p1', singleVideo, 'v1', 1, 'continuous'), {
    kind: 'part',
    id: 'p3',
  });
  assert.deepEqual(queueTarget(parts, 'p3', singleVideo, 'v1', 1, 'continuous'), { kind: 'stop' });
  assert.deepEqual(queueTarget(parts, 'p3', singleVideo, 'v1', 1, 'repeat'), {
    kind: 'part',
    id: 'p1',
  });
});
test('duplicate ended, stale player events and false ended cannot double advance; replay rearms after real progress', () => {
  const guard = createEndGuard(),
    old = guard.reset();
  assert.equal(guard.consume(old, false), false);
  assert.equal(guard.consume(old, true), true);
  assert.equal(guard.consume(old, true), false);
  guard.rearm(old, 100, 100, true);
  assert.equal(guard.consume(old, true), false);
  guard.rearm(old, 0, 100, false);
  assert.equal(guard.consume(old, true), true);
  const next = guard.reset();
  assert.equal(guard.consume(old, true), false);
  assert.equal(guard.consume(next, true), true);
});
test('preferences isolate account and guest, validate persisted values and preserve queue query', () => {
  assert.notEqual(queuePreferenceKey('a'), queuePreferenceKey('b'));
  assert.notEqual(queuePreferenceKey('a'), queuePreferenceKey());
  for (const raw of ['null', 'invalid', '{"mode":"bad","autoStart":"true"}'])
    assert.deepEqual(readQueuePreferences(raw), { mode: 'pause', autoStart: false });
  assert.deepEqual(readQueuePreferences('{"mode":"continuous","autoStart":false}'), {
    mode: 'continuous',
    autoStart: false,
  });
  assert.deepEqual(playlistQuery(playlistScope({ list_type: 'collection', list_id: 'a' })), {
    list_type: 'collection',
    list_id: 'a',
  });
  assert.equal(playlistScope({ list_type: 'bad', list_id: 'a' }), null);
});

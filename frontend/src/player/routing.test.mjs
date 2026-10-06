import test from 'node:test';
import assert from 'node:assert/strict';
import { createPlayback } from './routing.ts';
import { session } from '../api.ts';
const json = (value) =>
  new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
test('slow node probes do not hold first playback or replace it after the viewer starts', async (t) => {
  const previous = globalThis.location;
  globalThis.location = { origin: 'https://video.example.test' };
  t.after(() => {
    globalThis.location = previous;
  });
  session.user = { id: 'route-probe-test', username: 'test', role: 'reader' };
  session.csrf = 'local-test-token';
  const pending = [],
    probes = [],
    states = [];
  let creates = 0,
    reports = 0;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (String(url) === '/api/v1/playback-sessions') {
      creates++;
      return json({
        id: 'session',
        url: 'https://storage.example.test/movie.mp4',
        variant_id: 'video',
        asset_id: 'asset',
        routes: [1, 2].map((id) => ({
          id: `node-${id}`,
          status: 'available',
          probe_url: `/probe/${id}`,
        })),
      });
    }
    if (String(url).includes('/observations')) {
      reports++;
      return json({ ok: true });
    }
    probes.push(options);
    return new Promise((resolve) => pending.push(resolve));
  });
  const value = await createPlayback({ part_id: 'part' }, new AbortController().signal, (active) =>
    states.push(active),
  );
  assert.equal(value.url, 'https://storage.example.test/movie.mp4');
  assert.equal(pending.length, 2);
  assert.equal(reports, 0);
  assert.equal(creates, 1);
  assert.deepEqual(states, [true]);
  for (const request of probes) {
    assert.equal(request.credentials, 'same-origin');
    assert.deepEqual(Object.keys(request.headers), ['Range']);
  }
  for (const resolve of pending) resolve(new Response(new Uint8Array(64), { status: 206 }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(reports, 2);
  assert.equal(creates, 1);
  assert.deepEqual(states, [true, false]);
});
test('an explicit selected node does not generate competing probe requests', async (t) => {
  let requests = 0;
  t.mock.method(globalThis, 'fetch', async () => {
    requests++;
    return json({
      routes: [
        { id: 'one', probe_url: '/a' },
        { id: 'two', probe_url: '/b' },
      ],
    });
  });
  await createPlayback({ part_id: 'part', route_id: 'one' }, new AbortController().signal, () =>
    assert.fail('unexpected probe'),
  );
  assert.equal(requests, 1);
});

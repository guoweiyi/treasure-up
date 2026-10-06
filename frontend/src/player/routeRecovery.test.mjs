import test from 'node:test';
import assert from 'node:assert/strict';
import { createRouteRecovery } from './routeRecovery.ts';
const playback = {
  variant_id: 'hls-package',
  source_variant_id: 'original',
  selected_route_id: 'first',
  protocol: 'hls',
  routes: [
    { id: 'first', status: 'available' },
    { id: 'broken', status: 'unavailable' },
    { id: 'second', status: 'unmeasured' },
    { id: 'third', status: 'available' },
  ],
};
test('automatic network recovery preserves media and tries one other complete available node', () => {
  const recovery = createRouteRecovery();
  const original = structuredClone(playback);
  assert.deepEqual(recovery.claim('part', 'original', '', playback), {
    part_id: 'part',
    variant_id: 'original',
    route_id: 'second',
    protocol: 'hls',
  });
  assert.deepEqual(playback, original);
  assert.equal(
    recovery.claim('part', 'original', '', { ...playback, selected_route_id: 'second' }),
    null,
  );
  recovery.reset();
  assert.equal(
    recovery.claim('part', 'original', '', { ...playback, selected_route_id: 'second' }).route_id,
    'first',
  );
});
test('manual selection and a single remaining node retry their own URL without changing protocol', () => {
  const recovery = createRouteRecovery();
  assert.equal(recovery.claim('part', 'original', 'first', playback).route_id, 'first');
  assert.equal(recovery.claim('part', 'original', 'first', playback), null);
  recovery.reset();
  assert.deepEqual(
    recovery.claim('part', 'original', '', {
      ...playback,
      protocol: 'file',
      routes: [playback.routes[0]],
    }),
    { part_id: 'part', variant_id: 'original', route_id: 'first', protocol: 'file' },
  );
});
test('missing startup data cannot consume a later valid recovery', () => {
  const recovery = createRouteRecovery();
  assert.equal(recovery.claim('part', '', '', null), null);
  assert.equal(recovery.claim('part', '', '', playback).variant_id, 'original');
});

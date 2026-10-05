import test from 'node:test';
import assert from 'node:assert/strict';
import {
  createProtocolFallback,
  loadWithProtocolFallback,
  MediaLoadError,
  mediaFailureKind,
  recoveryStartState,
} from './playbackRecovery.ts';

const hls = {
  protocol: 'hls',
  variant_id: 'hls-package',
  source_variant_id: 'original-dolby',
  url: '/manifest',
};
const file = {
  protocol: 'file',
  variant_id: 'original-dolby',
  source_variant_id: 'original-dolby',
  url: '/original',
};

test('startup fallback keeps autoplay intent and normal renewal keeps explicit pause', () => {
  assert.deepEqual(recoveryStartState(NaN, true, true, true), { position: 0, paused: false });
  assert.deepEqual(recoveryStartState(0, true, true, false), { position: 0, paused: true });
  assert.deepEqual(recoveryStartState(37, true, false, true), { position: 37, paused: true });
  assert.deepEqual(recoveryStartState(37, false, false, false), { position: 37, paused: false });
  assert.equal(recoveryStartState(Infinity, true, false, false).position, 0);
});

test('initial HLS decode failure claims the same original once and stays file on route renewal', async () => {
  const recovery = createProtocolFallback();
  const selection = recovery.claim('part', hls, 'route-a');
  assert.deepEqual(selection, {
    part_id: 'part',
    variant_id: 'original-dolby',
    route_id: 'route-a',
    protocol: 'file',
  });
  assert.equal(recovery.claim('part', hls), null);
  assert.equal(recovery.claim('part', file), null);
  let restored = 0;
  const result = await loadWithProtocolFallback(selection, recovery, {
    current: () => true,
    create: async (input) => {
      assert.equal(input.protocol, 'file');
      return file;
    },
    attach: async (data) => assert.equal(data.variant_id, 'original-dolby'),
    restore: async () => {
      restored++;
    },
  });
  assert.equal(result, file);
  assert.equal(restored, 1);
  assert.equal(
    recovery.input({ part_id: 'part', variant_id: 'original-dolby', route_id: 'route-b' }).protocol,
    'file',
  );
  assert.equal(
    recovery.input({ part_id: 'part', variant_id: 'user-chosen-copy' }).protocol,
    undefined,
  );
});

test('HLS media error during renewal retries file once before restoring the original snapshot', async () => {
  const recovery = createProtocolFallback();
  for (const paused of [true, false]) {
    recovery.reset();
    const calls = [],
      attached = [],
      snapshots = [];
    const snapshot = { position: 31.5, paused, rate: 1.5, volume: 0.4, muted: true };
    await loadWithProtocolFallback({ part_id: 'part', variant_id: 'original-dolby' }, recovery, {
      current: () => true,
      create: async (input) => {
        calls.push(input);
        return input.protocol === 'file' ? file : hls;
      },
      attach: async (data) => {
        attached.push(data.protocol);
        if (data.protocol === 'hls') throw new MediaLoadError('media', 'decode failure');
      },
      restore: async () => {
        snapshots.push({ ...snapshot });
      },
    });
    assert.deepEqual(attached, ['hls', 'file']);
    assert.equal(calls[1].variant_id, 'original-dolby');
    assert.deepEqual(snapshots, [snapshot]);
  }
});

test('network errors retain the route retry path and do not consume protocol fallback', async () => {
  const recovery = createProtocolFallback();
  let created = 0;
  await assert.rejects(
    loadWithProtocolFallback({ part_id: 'part' }, recovery, {
      current: () => true,
      create: async () => {
        created++;
        return hls;
      },
      attach: async () => {
        throw new MediaLoadError('network', 'temporary address error');
      },
      restore: async () => assert.fail('failed network must not restore'),
    }),
    /temporary address error/,
  );
  assert.equal(created, 1);
  assert.ok(recovery.claim('part', hls));
});

test('file decode failure cannot loop or silently select another codec', async () => {
  const recovery = createProtocolFallback();
  let calls = 0;
  await assert.rejects(
    loadWithProtocolFallback({ part_id: 'part' }, recovery, {
      current: () => true,
      create: async (input) => {
        calls++;
        return input.protocol === 'file' ? file : hls;
      },
      attach: async () => {
        throw new MediaLoadError('media', 'still unsupported');
      },
      restore: async () => assert.fail('both formats failed'),
    }),
    /still unsupported/,
  );
  assert.equal(calls, 2);
  assert.equal(recovery.claim('part', hls), null);
  assert.equal(
    createProtocolFallback().claim('part', { ...hls, source_variant_id: undefined }),
    null,
  );
});

test('an old response or attach failure after switching cannot attach, restore or claim fallback', async () => {
  for (const failAt of ['create', 'attach']) {
    const recovery = createProtocolFallback();
    let current = true,
      attaches = 0;
    const result = await loadWithProtocolFallback({ part_id: 'part' }, recovery, {
      current: () => current,
      create: async () => {
        if (failAt === 'create') current = false;
        return hls;
      },
      attach: async () => {
        attaches++;
        current = false;
        throw new MediaLoadError('media', 'old media');
      },
      restore: async () => assert.fail('stale playback must not restore'),
    });
    assert.equal(result, null);
    assert.equal(attaches, failAt === 'create' ? 0 : 1);
    assert.ok(recovery.claim('part', hls));
  }
});

test('fallback rejects a server response for a different media version', async () => {
  const recovery = createProtocolFallback();
  const input = recovery.claim('part', hls);
  await assert.rejects(
    loadWithProtocolFallback(input, recovery, {
      current: () => true,
      create: async () => ({ ...file, variant_id: 'aac-copy' }),
      attach: async () => assert.fail('do not attach changed media'),
      restore: async () => assert.fail('do not restore changed media'),
    }),
    /指定原档不符/,
  );
  assert.equal(mediaFailureKind(3), 'media');
  assert.equal(mediaFailureKind(4), 'media');
  assert.equal(mediaFailureKind(2), 'network');
});

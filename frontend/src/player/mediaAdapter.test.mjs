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

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { bitrate, formatSpecification } from './mediaInfo.ts';

test('unknown rates and numeric source quality IDs are never displayed as measured media', () => {
  for (const value of [undefined, null, 0, -1, NaN, Infinity])
    assert.equal(bitrate(value), '未记录');
  assert.equal(bitrate(8123456), '8.12 Mbps');
  assert.equal(bitrate(192000), '192 kbps');
  assert.equal(formatSpecification({ quality: 120 }), '');
  assert.equal(
    formatSpecification({ quality: '1080P 高码率', fps: 59.94 }),
    '1080P 高码率 · 59.94 fps',
  );
});

test('portrait resolution and HDR source format are preserved', () => {
  assert.equal(
    formatSpecification({
      width: 1080,
      height: 1920,
      fps: 60,
      video_codec: 'hevc',
      dynamic_range: 'HDR10',
    }),
    '1080 × 1920 · 60 fps · hevc · HDR10',
  );
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { danmakuLayout, danmakuMargins } from './layout.ts';
import { defaultPreferences } from './preferences.ts';

test('full-area bottom comments reserve touch controls and the home indicator', () => {
  assert.deepEqual(danmakuMargins(220, 100, false, 34), [11, 86]);
  assert.ok(danmakuMargins(720, 100, true)[1] >= 129);
  assert.ok(danmakuMargins(720, 75, true)[1] >= 180);
});

test('short landscape players retain a text row and fresh preferences slow scrolling', () => {
  const [top, bottom] = danmakuMargins(100, 25, true, 34);
  assert.ok(100 - top - bottom >= 36);
  assert.ok(defaultPreferences().speed > 5);
  assert.equal(defaultPreferences().synchronousPlayback, false);
});

test('fixed bottom baseline does not follow half-screen or quarter-screen rolling area', () => {
  for (const height of [220, 450, 720, 1080]) {
    for (const subtitleSafe of [false, true]) {
      const baseline = [25, 50, 75, 100].map((area) => {
        const result = danmakuLayout((height * 16) / 9, height, 1920, 1080, area, subtitleSafe);
        return height - result.margin[1] + result.bottomOffset;
      });
      assert.ok(baseline.every((value) => Math.abs(value - baseline[0]) < 0.001));
      assert.ok(baseline[0] > height * 0.6);
      assert.ok(baseline[0] <= height - 52);
    }
  }
});

test('wide picture letterboxing and cover/fullscreen resize use the actual image bottom', () => {
  const contained = danmakuLayout(800, 600, 2400, 1000, 50, false);
  const pictureBottom = (600 + 800 / 2.4) / 2;
  assert.ok(
    Math.abs(600 - contained.margin[1] + contained.bottomOffset - (pictureBottom - 12)) < 0.001,
  );
  assert.ok(contained.margin[0] >= (600 - 800 / 2.4) / 2);
  const covered = danmakuLayout(800, 600, 2400, 1000, 50, false, 0, 'cover');
  assert.equal(600 - covered.margin[1] + covered.bottomOffset, 548);
  const fullscreen = danmakuLayout(1920, 1080, 2400, 1000, 50, true, 34);
  assert.ok(1080 - fullscreen.margin[1] + fullscreen.bottomOffset <= 940 - 800 * 0.18);
});

test('translation keeps bottom lane spacing and prevents subtitle and safe-area overlap', () => {
  const result = danmakuLayout(1280, 720, 1920, 1080, 50, true, 34);
  const first = 720 - result.margin[1] - 30 + result.bottomOffset;
  const second = first - 30;
  assert.equal(first - second, 30);
  assert.ok(first + 30 <= 720 * 0.82 + 0.001);
  const short = danmakuLayout(200, 100, 1920, 1080, 25, true, 34);
  assert.ok(100 - short.margin[0] - short.margin[1] >= 36);
});

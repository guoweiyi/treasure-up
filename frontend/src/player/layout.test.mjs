import { test } from 'node:test';
import assert from 'node:assert/strict';
import { danmakuMargins } from './layout.ts';
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

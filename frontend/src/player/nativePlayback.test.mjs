import test from 'node:test';
import assert from 'node:assert/strict';
import { canUseElementFullscreen } from './nativePlayback.ts';

const element = { requestFullscreen() {} };
const android = {
  userAgent:
    'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 Chrome/134.0 Mobile Safari/537.36',
};

test('only a Tauri Android webview hides the unsupported system fullscreen control', () => {
  assert.equal(canUseElementFullscreen(element, { isTauri: true, navigator: android }), false);
  assert.equal(canUseElementFullscreen(element, { navigator: android }), true);
  assert.equal(canUseElementFullscreen(element, { isTauri: false, navigator: android }), true);
  assert.equal(canUseElementFullscreen(element, { isTauri: 'true', navigator: android }), true);
  // Query parameters and a stored UI navigation preference are insufficient.
  assert.equal(
    canUseElementFullscreen(element, {
      navigator: android,
      location: { search: '?client=native' },
    }),
    true,
  );
});

test('iOS and desktop shells keep feature detection without forcing unavailable APIs', () => {
  for (const userAgent of [
    'Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X)',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    '',
  ]) {
    assert.equal(
      canUseElementFullscreen(element, { isTauri: true, navigator: { userAgent } }),
      true,
    );
    assert.equal(canUseElementFullscreen({}, { isTauri: true, navigator: { userAgent } }), false);
  }
  assert.equal(canUseElementFullscreen({ requestFullscreen: true }, {}), false);
});

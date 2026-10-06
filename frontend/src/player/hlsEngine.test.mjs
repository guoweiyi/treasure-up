import { test } from 'node:test';
import assert from 'node:assert/strict';
import { selectHlsEngine } from './hlsEngine.ts';

const capable = { nativeAvailable: true, mseAvailable: true };
test('Chromium native HLS support does not bypass progressive MSE for ordinary non-Apple media', () => {
  for (const environment of [
    {
      platform: 'Win32',
      userAgent:
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/154.0.0.0 Safari/537.36',
    },
    {
      platform: 'Linux x86_64',
      userAgent:
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/154.0.0.0 Safari/537.36',
    },
    { platform: '', userAgent: '' },
  ])
    assert.equal(
      selectHlsEngine({ ...capable, ...environment, media: { audio_codec: 'aac' } }),
      'mse',
    );
});
test('Apple Safari, non-Safari iOS WebViews and desktop-mode iPad retain native HLS', () => {
  for (const environment of [
    {
      platform: 'MacIntel',
      userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Version/27.0 Safari/605.1.15',
    },
    { platform: 'iPhone', userAgent: 'TreasureUp WKWebView', maxTouchPoints: 5 },
    { platform: 'MacIntel', userAgent: 'TreasureUp WKWebView', maxTouchPoints: 5 },
    {
      platform: '',
      userAgent: 'Mozilla/5.0 (iPad; CPU OS 27_0 like Mac OS X) AppleWebKit/605.1.15',
    },
  ])
    assert.equal(selectHlsEngine({ ...capable, ...environment }), 'native');
});
test('EC-3 and potential Atmos retain the native audio path and native-only devices can still play', () => {
  for (const media of [
    { audio_codec: 'eac3' },
    { audio_codec: 'EC-3' },
    { audio_codec: 'ec3' },
    { dolby_atmos: true },
  ])
    assert.equal(selectHlsEngine({ ...capable, platform: 'Win32', media }), 'native');
  assert.equal(selectHlsEngine({ nativeAvailable: true, mseAvailable: false }), 'native');
  assert.equal(
    selectHlsEngine({ nativeAvailable: false, mseAvailable: true, platform: 'MacIntel' }),
    'mse',
  );
  assert.equal(selectHlsEngine({ nativeAvailable: false, mseAvailable: false }), 'unsupported');
});

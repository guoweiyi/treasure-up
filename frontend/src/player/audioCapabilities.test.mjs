import { test } from 'node:test';
import assert from 'node:assert/strict';
import { probeAudioSupport } from './audioCapabilities.ts';

test('codec support alone never proves spatial output when an engine ignores the field', async () => {
  const requests = [];
  const result = await probeAudioSupport(
    { audio_codec: 'eac3', dolby_atmos: true },
    {
      async decodingInfo(config) {
        requests.push(config);
        return { supported: true };
      },
    },
  );
  assert.deepEqual(result, { ec3: 'supported', spatial: 'unknown' });
  assert.equal(requests[0].audio.contentType, 'audio/mp4; codecs="ec-3"');
  assert.equal(requests[1].audio.spatialRendering, true);
  assert.equal('channels' in requests[1].audio, false);
});

test('spatial results require the requested configuration and preserve real audio measurements', async () => {
  const result = await probeAudioSupport(
    {
      audio_codec: 'ec-3',
      audio_channels: 6,
      audio_sample_rate: 48000,
      audio_bitrate_bps: 1024000,
      dolby_atmos: true,
    },
    {
      async decodingInfo(config) {
        assert.equal(config.audio.channels, '6');
        assert.equal(config.audio.samplerate, 48000);
        assert.equal(config.audio.bitrate, 1024000);
        return { supported: !config.audio.spatialRendering, configuration: config };
      },
    },
  );
  assert.deepEqual(result, { ec3: 'supported', spatial: 'unsupported' });
});

test('absent APIs, exceptions and ordinary AAC do not manufacture Atmos capability', async () => {
  assert.deepEqual(await probeAudioSupport({ audio_codec: 'eac3' }), {
    ec3: 'unknown',
    spatial: 'unknown',
  });
  assert.deepEqual(
    await probeAudioSupport(
      { audio_codec: 'eac3' },
      {
        decodingInfo() {
          throw new Error('unavailable');
        },
      },
    ),
    { ec3: 'unknown', spatial: 'unknown' },
  );
  await probeAudioSupport(
    { audio_codec: 'aac' },
    {
      decodingInfo() {
        assert.fail('AAC is not an Atmos candidate');
      },
    },
  );
});

test('MSE and file capability are independent and malformed results stay unknown', async () => {
  const capabilities = {
    async decodingInfo(config) {
      return { supported: config.type === 'file', configuration: config };
    },
  };
  assert.equal(
    (await probeAudioSupport({ audio_codec: 'eac3' }, capabilities, 'file')).ec3,
    'supported',
  );
  assert.equal(
    (await probeAudioSupport({ audio_codec: 'eac3' }, capabilities, 'media-source')).ec3,
    'unsupported',
  );
  assert.deepEqual(
    await probeAudioSupport(
      { audio_codec: 'eac3', dolby_atmos: true },
      {
        async decodingInfo() {
          return {};
        },
      },
    ),
    { ec3: 'unknown', spatial: 'unknown' },
  );
});

test('a mismatched spatial response cannot prove the current engine supports spatial rendering', async () => {
  const result = await probeAudioSupport(
    { audio_codec: 'eac3', dolby_atmos: true },
    {
      async decodingInfo(config) {
        return { supported: true, configuration: { ...config, type: 'file' } };
      },
    },
    'media-source',
  );
  assert.deepEqual(result, { ec3: 'supported', spatial: 'unknown' });
});

test('an unresponsive capability query is bounded and does not become unsupported', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const promise = probeAudioSupport(
    { audio_codec: 'eac3' },
    { decodingInfo: () => new Promise(() => {}) },
  );
  t.mock.timers.tick(1201);
  assert.deepEqual(await promise, { ec3: 'unknown', spatial: 'unknown' });
});

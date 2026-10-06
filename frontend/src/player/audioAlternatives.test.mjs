import test from 'node:test';
import assert from 'node:assert/strict';
import {
  audioCompatibilityMessage,
  audioOnlyCopy,
  audioVariantSuffix,
  sameOriginalAudioAlternative,
} from './audioAlternatives.ts';

const original = { id: 'original', kind: 'archive', audio_codec: 'eac3' };
const details = {
  source_variant_id: original.id,
  compatibility_mode: 'audio_only',
  video_stream_copy: true,
  audio_transcoded: true,
  audio_codec: 'aac',
  audio_channels: 2,
  dolby_atmos: false,
};
const copy = { id: 'copy', kind: 'playback', audio_codec: 'aac', metadata: details };
const current = { audio_codec: 'eac3', dolby_atmos: true };

test('unsupported/unknown EC3 gets clear guidance while spatial limitations do not block ordinary decode', () => {
  assert.match(audioCompatibilityMessage(current, 'unsupported'), /没有声音/);
  assert.match(audioCompatibilityMessage(current, 'unknown'), /未提供/);
  assert.equal(audioCompatibilityMessage(current, 'supported'), '');
  assert.equal(audioCompatibilityMessage({ audio_codec: 'aac' }, 'unknown'), '');
  assert.equal(audioCompatibilityMessage(undefined, 'unsupported'), '');
});

test('only an explicitly verified AAC copy of the selected original is offered', () => {
  const unrelated = {
    ...copy,
    id: 'other',
    metadata: { ...details, source_variant_id: 'other-original' },
  };
  const hls = { ...copy, id: 'package', kind: 'hls' };
  assert.equal(
    sameOriginalAudioAlternative(original.id, [original, unrelated, hls, copy], undefined, current),
    copy,
  );
  assert.equal(
    sameOriginalAudioAlternative(original.id, [original, unrelated, hls], undefined, current),
    undefined,
  );
  assert.equal(
    sameOriginalAudioAlternative(copy.id, [original, copy], undefined, { audio_codec: 'aac' }),
    undefined,
  );
  assert.equal(
    sameOriginalAudioAlternative('different-selection', [original, copy], undefined, current),
    undefined,
  );
});

test('audio-only labels never hide lossy audio or transfer an original Atmos label to AAC', () => {
  assert.equal(audioOnlyCopy(details), true);
  for (const changed of [
    { video_stream_copy: false },
    { audio_transcoded: false },
    { dolby_atmos: true },
    { audio_codec: 'eac3' },
    { audio_channels: 6 },
  ]) {
    assert.equal(audioOnlyCopy({ ...details, ...changed }), false);
  }
  assert.match(audioVariantSuffix(copy), /AAC 立体声/);
  assert.equal(audioVariantSuffix({ ...copy, metadata: {} }), '兼容副本');
  assert.equal(audioVariantSuffix(original), '原档');
});

test('properties supplied by the catalog are accepted without guessing absent proof', () => {
  const bare = { ...copy, metadata: undefined };
  assert.equal(
    sameOriginalAudioAlternative(original.id, [original, bare], { [copy.id]: details }, current),
    bare,
  );
  assert.equal(
    sameOriginalAudioAlternative(original.id, [original, bare], undefined, current),
    undefined,
  );
});

test('the AAC shortcut prefers the corrected mix only for the same original', () => {
  const updated = { ...copy, id: 'updated', metadata: { ...details, audio_mix_revision: 2 } };
  const unrelated = {
    ...updated,
    id: 'other',
    metadata: { ...updated.metadata, source_variant_id: 'other-original' },
  };
  assert.equal(
    sameOriginalAudioAlternative(
      original.id,
      [original, copy, unrelated, updated],
      undefined,
      current,
    ),
    updated,
  );
  assert.equal(
    sameOriginalAudioAlternative(original.id, [original, copy, unrelated], undefined, current),
    copy,
  );
  assert.match(audioVariantSuffix(updated), /新版/);
  assert.match(audioVariantSuffix(copy), /旧版/);
});

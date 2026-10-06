import type { AudioFormat } from './audioCapabilities.ts';

export type PlaybackEnvironment = {
  userAgent?: string;
  platform?: string;
  maxTouchPoints?: number;
};

/** Keep Apple's native media path and potential EC-3/JOC spatial output intact. */
export function prefersNativeHls(environment: PlaybackEnvironment = {}, media?: AudioFormat) {
  const apple =
    /iPhone|iPad|iPod|Macintosh|Mac OS X/i.test(environment.userAgent || '') ||
    /^(Mac|iPhone|iPad|iPod)/i.test(environment.platform || '');
  const codec = (media?.audio_codec || '').toLowerCase();
  return apple || media?.dolby_atmos === true || ['ec-3', 'eac3', 'ec3'].includes(codec);
}

export function selectHlsEngine(
  input: PlaybackEnvironment & {
    nativeAvailable: boolean;
    mseAvailable: boolean;
    media?: AudioFormat;
  },
): 'native' | 'mse' | 'unsupported' {
  if (input.nativeAvailable && prefersNativeHls(input, input.media)) return 'native';
  if (input.mseAvailable) return 'mse';
  return input.nativeAvailable ? 'native' : 'unsupported';
}

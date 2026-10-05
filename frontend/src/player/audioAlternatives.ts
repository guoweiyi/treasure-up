import type { Variant } from '../types';
import type { AudioCapability } from './audioCapabilities.ts';
import type { MeasuredMedia } from './mediaInfo.ts';

type Properties = Record<string, MeasuredMedia> | undefined;
function properties(variant: Variant, media: Properties): MeasuredMedia {
  return { ...variant.metadata, ...media?.[variant.id] };
}
export function isEc3(media: MeasuredMedia | undefined) {
  return ['eac3', 'ec-3'].includes(media?.audio_codec || '');
}
export function audioOnlyCopy(media: MeasuredMedia | undefined) {
  return (
    media?.compatibility_mode === 'audio_only' &&
    media.video_stream_copy === true &&
    media.audio_transcoded === true &&
    media.audio_codec === 'aac' &&
    media.audio_channels === 2 &&
    media.dolby_atmos === false
  );
}
export function sameOriginalAudioAlternative(
  variantId: string,
  variants: Variant[],
  media: Properties,
  current?: MeasuredMedia,
): Variant | undefined {
  if (!variantId || !isEc3(current)) return undefined;
  const selected = variants.find((variant) => variant.id === variantId);
  // Only a registered original's explicit derived copy is an eligible shortcut.
  // Never select another original/quality, an HLS package, or an unrelated AAC.
  if (selected?.kind !== 'archive') return undefined;
  return variants.find((variant) => {
    const details = properties(variant, media);
    return (
      variant.kind === 'playback' &&
      details.source_variant_id === selected.id &&
      audioOnlyCopy(details)
    );
  });
}
export function audioCompatibilityMessage(
  media: MeasuredMedia | undefined,
  support: AudioCapability,
) {
  if (!isEc3(media) || support === 'supported') return '';
  return support === 'unsupported'
    ? '当前播放引擎报告不支持这条 EC-3 音轨，可能只有画面、没有声音。'
    : '当前浏览器未提供这条 EC-3 音轨的解码能力信息；如无声音，可尝试声音兼容版。';
}
export function audioVariantSuffix(variant: Variant, media: Properties) {
  const value = properties(variant, media);
  if (variant.kind === 'playback' && audioOnlyCopy(value)) return 'AAC 立体声 · 原视频流';
  return variant.kind === 'playback' ? '兼容副本' : '原档';
}

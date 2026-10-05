export type AudioCapability = 'unknown' | 'supported' | 'unsupported';
export interface AudioFormat {
  audio_codec?: string | null;
  audio_channels?: number | null;
  audio_sample_rate?: number | null;
  audio_bitrate_bps?: number | null;
  dolby_atmos?: boolean;
}
export const unknownAudioSupport = () => ({
  ec3: 'unknown' as AudioCapability,
  spatial: 'unknown' as AudioCapability,
});

export async function probeAudioSupport(
  media: AudioFormat | undefined,
  capabilities?: Pick<MediaCapabilities, 'decodingInfo'>,
  decodingType: 'file' | 'media-source' = 'file',
) {
  const result = unknownAudioSupport();
  if (!media || !['ec-3', 'eac3'].includes(media.audio_codec || '') || !capabilities) return result;
  const audio: AudioConfiguration = { contentType: 'audio/mp4; codecs="ec-3"' };
  if (media.audio_channels && media.audio_channels > 0)
    audio.channels = String(media.audio_channels);
  if (media.audio_sample_rate && media.audio_sample_rate > 0)
    audio.samplerate = media.audio_sample_rate;
  if (media.audio_bitrate_bps && media.audio_bitrate_bps > 0)
    audio.bitrate = media.audio_bitrate_bps;
  async function query(spatial: boolean) {
    let timer: ReturnType<typeof globalThis.setTimeout> | undefined;
    try {
      return await Promise.race([
        capabilities!.decodingInfo({
          type: decodingType,
          audio: { ...audio, spatialRendering: spatial },
        }),
        new Promise<undefined>((resolve) => {
          timer = globalThis.setTimeout(() => resolve(undefined), 1200);
        }),
      ]);
    } catch {
      return undefined;
    } finally {
      clearTimeout(timer);
    }
  }
  const decoded = await query(false);
  if (typeof decoded?.supported === 'boolean')
    result.ec3 = decoded.supported ? 'supported' : 'unsupported';
  if (media.dolby_atmos) {
    const spatial = await query(true);
    // Older engines can ignore the requested spatialRendering member. Do not
    // turn an ordinary codec-support result into a spatial-output claim.
    const configuration = (
      spatial as
        | (MediaCapabilitiesDecodingInfo & { configuration?: MediaDecodingConfiguration })
        | undefined
    )?.configuration;
    if (
      configuration?.type === decodingType &&
      configuration?.audio?.spatialRendering === true &&
      typeof spatial?.supported === 'boolean'
    )
      result.spatial = spatial.supported ? 'supported' : 'unsupported';
  }
  return result;
}

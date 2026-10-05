import type { Creator, MediaProperties, Video } from '../types';

export interface SourceFormat {
  quality?: string | number;
  width?: number | null;
  height?: number | null;
  fps?: number | null;
  video_codec?: string | null;
  audio_codec?: string | null;
  dynamic_range?: string | null;
  video_bitrate_bps?: number | null;
  audio_bitrate_bps?: number | null;
  total_bitrate_bps?: number | null;
}
export interface MeasuredMedia extends MediaProperties {
  audio_channels?: number | null;
  audio_sample_rate?: number | null;
  ec3?: { joc?: boolean; complexity_index_type_a?: number | null };
  ec3_configuration_verified?: boolean;
  atmos_evidence?: string;
  source_variant_id?: string;
  mime_type?: string | null;
  width?: number | null;
  height?: number | null;
  video_codec?: string | null;
  audio_codec?: string | null;
  segment_count?: number | null;
  fps?: number | null;
  video_bitrate_bps?: number | null;
  audio_bitrate_bps?: number | null;
  total_bitrate_bps?: number | null;
  size_bytes?: number | null;
  total_bitrate_basis?: string;
  duration_seconds?: number | null;
  measured_at?: string;
}
export interface SourceQuality {
  observed_at?: string;
  scope?: string;
  parts?: Record<
    string,
    {
      status?: string;
      observed_at?: string;
      maximum?: SourceFormat | null;
      maximum_audio?: SourceFormat | null;
      formats?: SourceFormat[];
    }
  >;
}
export type VideoDetail = Video & {
  content_features?: {
    charging_exclusive?: boolean;
    dolby_vision?: boolean;
    dolby_atmos?: boolean;
  };
  published_at?: string | null;
  source_quality?: SourceQuality | null;
  media_properties?: Record<string, MeasuredMedia>;
};
export function compactSpecification(format?: SourceFormat | null) {
  if (!format) return '';
  const resolution =
    format.width && format.height
      ? `${format.width} × ${format.height}`
      : format.height
        ? `${format.height}P`
        : '';
  const rate = bitrate(format.total_bitrate_bps || format.video_bitrate_bps);
  return [
    resolution,
    format.fps && Number.isFinite(format.fps) && format.fps > 0
      ? `${Number(format.fps.toFixed(2))} fps`
      : '',
    rate === '未记录' ? '' : rate,
  ]
    .filter(Boolean)
    .join(' · ');
}
export function measuredVariant(properties: Record<string, MeasuredMedia> | undefined, id: string) {
  const current = properties?.[id];
  const source = current?.source_variant_id ? properties?.[current.source_variant_id] : undefined;
  return { ...source, ...current };
}
export type VideoCreator = Creator & { role_title?: string | null };

export function bitrate(value?: number | null) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return '未记录';
  return value >= 1_000_000
    ? `${(value / 1_000_000).toFixed(2)} Mbps`
    : `${Math.round(value / 1000)} kbps`;
}
export function formatSpecification(format?: SourceFormat | null) {
  if (!format) return '';
  const resolution =
    format.width && format.height
      ? `${format.width} × ${format.height}`
      : format.height
        ? `${format.height}P`
        : typeof format.quality === 'string'
          ? format.quality
          : '';
  return [
    resolution,
    format.fps ? `${Number(format.fps.toFixed(2))} fps` : '',
    format.video_codec,
    format.dynamic_range && !['SDR', 'unknown'].includes(format.dynamic_range)
      ? format.dynamic_range
      : '',
  ]
    .filter(Boolean)
    .join(' · ');
}

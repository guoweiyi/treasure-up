export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}
export interface User {
  id: string;
  username: string;
  role: string;
  disabled?: boolean;
}
export interface Creator {
  id: string;
  uid?: string;
  name: string;
  source_name?: string;
  avatar_url?: string;
  description?: string;
  notes?: string;
  saved_count?: number;
  tags?: string[];
  role?: string;
  role_title?: string;
}
export interface Variant {
  id: string;
  quality: number | string;
  kind: string;
  width?: number;
  height?: number;
  video_codec?: string;
  audio_codec?: string;
}
export interface MediaProperties {
  color_transfer?: string;
  primaries?: string;
  pix_fmt?: string;
  hdr?: boolean;
  wide_gamut?: boolean;
  dolby_vision?: boolean;
  dolby_atmos?: boolean;
  compatibility?: string;
}
export interface VideoStats {
  view: number | null;
  like: number | null;
  coin: number | null;
  favorite: number | null;
  share: number | null;
  reply: number | null;
  danmaku: number | null;
  observed_at?: string | null;
}
export interface Part {
  id: string;
  cid: string;
  position: number;
  title: string;
  duration: number;
  variants: Variant[];
}
export interface Video {
  id: string;
  bvid: string;
  title: string;
  source_title?: string;
  description: string;
  duration: number;
  cover_url?: string;
  creators: Creator[];
  tags: string[];
  starred: boolean;
  parts_count: number;
  playable: boolean;
  capture_status: string;
  created_at: string;
  published_at?: string | null;
  notes?: string;
  source_state?: string;
  parts?: Part[];
  media_properties?: Record<string, MediaProperties>;
  stats?: VideoStats;
  capture_runs?: Record<string, unknown>[];
}
export interface Collection {
  id: string;
  title: string;
  kind: string;
  source_id: string;
  saved_count: number;
  enabled: boolean;
  cover_url?: string | null;
}
export interface Comment {
  id: string;
  rpid: string;
  root_rpid?: string;
  parent_rpid?: string;
  content: string;
  author: { uid?: string; name?: string; avatar_url?: string; creator_id?: string };
  posted_at: string;
  like_count: number;
  reply_count: number;
  images: (string | { url?: string; asset_url?: string })[];
  emotes?: { text: string; asset_url: string }[];
}
export interface Danmaku {
  text: string;
  time: number;
  color: string | number;
  mode: number;
  size?: number;
}
export interface Playback {
  id?: string;
  session_id?: string;
  asset_id: string;
  variant_id: string;
  source_variant_id?: string;
  protocol?: 'hls' | 'file';
  routes?: PlaybackRoute[];
  selected_route_id?: string;
  media?: MediaProperties;
  loudness?: Loudness | null;
  url: string;
  expires_at?: string;
  danmaku_url?: string;
  subtitles: { url: string; label: string; language?: string; is_auto?: boolean }[];
}
export interface PlaybackRoute {
  id: string;
  name: string;
  status: 'available' | 'unmeasured' | 'unavailable';
  latency_ms: number | null;
  throughput_bps: number | null;
  measurement_scope: 'server_storage' | 'browser_delivery' | null;
  measured_at: string | null;
  probe_url?: string;
  probe_bytes?: number;
}
export interface Loudness {
  status: 'ready' | 'bypassed' | 'silent';
  gain_db: number;
  gain_linear: number;
  target_lufs: number;
  input_lufs: number | null;
  true_peak_dbfs: number | null;
  atmos_bypass: boolean;
  reason: string | null;
  analysis_only: boolean;
}
export type Row = Record<string, any>;

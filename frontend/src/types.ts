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
  compatibility?: string;
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
  notes?: string;
  source_state?: string;
  parts?: Part[];
  media_properties?: Record<string, MediaProperties>;
  capture_runs?: Record<string, unknown>[];
}
export interface Collection {
  id: string;
  title: string;
  kind: string;
  source_id: string;
  saved_count: number;
  enabled: boolean;
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
}
export interface Danmaku {
  text: string;
  time: number;
  color: string | number;
  mode: number;
  size?: number;
}
export interface Playback {
  asset_id: string;
  variant_id: string;
  url: string;
  expires_at?: string;
  danmaku_url?: string;
  subtitles: { url: string; label: string; language?: string; is_auto?: boolean }[];
}
export type Row = Record<string, any>;

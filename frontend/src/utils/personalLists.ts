import type { VideoDetail } from '../player/mediaInfo';
export interface PersonalList {
  id: string;
  name: string;
  kind: 'watch_later' | 'custom';
  description: string;
  item_count: number;
  unwatched_count: number;
}
export interface SavedVideo extends VideoDetail {
  playlist_item: { note: string; watched: boolean; added_at: string; updated_at: string };
}

export type QueueMode = 'pause' | 'repeat' | 'continuous';
export interface PlaylistScope {
  type: 'collection' | 'creator';
  id: string;
}
export interface QueuePreferences {
  mode: QueueMode;
  autoStart: boolean;
}
export interface QueuePart {
  id: string;
  variants: unknown[];
}
export type QueueTarget =
  | { kind: 'part'; id: string }
  | { kind: 'video'; id: string }
  | { kind: 'replay' }
  | { kind: 'stop' };

export function playlistScope(query: Record<string, unknown>): PlaylistScope | null {
  return (query.list_type === 'collection' || query.list_type === 'creator') &&
    typeof query.list_id === 'string' &&
    query.list_id
    ? { type: query.list_type, id: query.list_id }
    : null;
}
export function playlistQuery(scope: PlaylistScope | null) {
  return scope ? { list_type: scope.type, list_id: scope.id } : {};
}
export function queuePreferenceKey(userId?: string) {
  return `treasure-up:queue:v1:${userId ? `user:${userId}` : 'guest'}`;
}
export function readQueuePreferences(raw: string | null): QueuePreferences {
  try {
    const value = JSON.parse(raw || '{}');
    return {
      mode: ['pause', 'repeat', 'continuous'].includes(value?.mode) ? value.mode : 'pause',
      autoStart: value?.autoStart === true,
    };
  } catch {
    return { mode: 'pause', autoStart: false };
  }
}
// A BV may have missing parts. Only actually archived parts participate in playback.
export function queueTarget(
  parts: QueuePart[],
  partId: string,
  videos: { id: string }[],
  videoId: string,
  direction: -1 | 1,
  mode?: QueueMode,
): QueueTarget {
  const playable = parts.filter((part) => part.variants.length > 0);
  const index = playable.findIndex((part) => part.id === partId);
  const adjacent = playable[index + direction];
  if (index >= 0 && adjacent) return { kind: 'part', id: adjacent.id };
  if (mode === 'pause') return { kind: 'stop' };
  if (mode === 'repeat') {
    const first = playable[0];
    return !first
      ? { kind: 'stop' }
      : first.id === partId
        ? { kind: 'replay' }
        : { kind: 'part', id: first.id };
  }
  const videoIndex = videos.findIndex((video) => video.id === videoId);
  const next = videoIndex >= 0 ? videos[videoIndex + direction] : undefined;
  return next ? { kind: 'video', id: next.id } : { kind: 'stop' };
}
// Duplicate native ended notifications and callbacks from disposed players cannot advance twice.
export function createEndGuard() {
  let generation = 0,
    consumed = false;
  return {
    reset() {
      consumed = false;
      return ++generation;
    },
    consume(key: number, ended: boolean) {
      if (key !== generation || consumed || !ended) return false;
      consumed = true;
      return true;
    },
    rearm(key: number, position: number, duration: number, ended: boolean) {
      if (key === generation && !ended && duration > 0 && position < duration - 0.5)
        consumed = false;
    },
  };
}

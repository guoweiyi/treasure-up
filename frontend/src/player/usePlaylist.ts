import { ref, watch, onScopeDispose, type ComputedRef } from 'vue';
import type { Page } from '../types';
import type { VideoDetail } from './mediaInfo';
import type { PlaylistScope } from './queue';

export type PlaylistPage = Page<VideoDetail> & { scope_title?: string };
export function playlistPath(scope: PlaylistScope, page = 1) {
  if (scope.type === 'personal')
    return `/me/playlists/${encodeURIComponent(scope.id)}/videos?${new URLSearchParams({ page: String(page), page_size: '100', playable_only: 'true' })}`;
  return `/playlists?${new URLSearchParams({ [`${scope.type}_id`]: scope.id, page: String(page), page_size: '100', view: 'card' })}`;
}
export function usePlaylist(
  scope: ComputedRef<PlaylistScope | null>,
  currentId: ComputedRef<string>,
  fetchPage: (scope: PlaylistScope, page: number, signal: AbortSignal) => Promise<PlaylistPage>,
) {
  const items = ref<VideoDetail[]>([]),
    title = ref(''),
    total = ref(0),
    busy = ref(false),
    error = ref('');
  let controller = new AbortController(),
    page = 0,
    revision = 0,
    pending: Promise<void> | null = null;
  async function more() {
    if (pending) return pending;
    const currentScope = scope.value;
    if (!currentScope || (page && items.value.length >= total.value)) return;
    const key = revision,
      signal = controller.signal;
    busy.value = true;
    error.value = '';
    pending = (async () => {
      try {
        const data = await fetchPage(currentScope, page + 1, signal);
        if (key !== revision) return;
        const ids = new Set(items.value.map((item) => item.id));
        items.value.push(
          ...data.items.filter((item) => {
            if (ids.has(item.id)) return false;
            ids.add(item.id);
            return true;
          }),
        );
        title.value = data.scope_title || '播放列表';
        total.value = data.total;
        page++;
        // A concurrently changed list or empty page must not trigger an endless search.
        if (!data.items.length) total.value = items.value.length;
      } catch (e) {
        if (key === revision && !signal.aborted)
          error.value = e instanceof Error ? e.message : '播放列表读取失败';
      } finally {
        if (key === revision) {
          busy.value = false;
          pending = null;
        }
      }
    })();
    return pending;
  }
  async function locate() {
    const key = revision;
    // Bound background work for very large lists. The UI offers another explicit batch.
    for (let pages = 0; pages < 5; pages++) {
      if (items.value.some((item) => item.id === currentId.value)) return;
      await more();
      if (key !== revision || error.value || items.value.length >= total.value) return;
    }
  }
  async function ensureNext() {
    await locate();
    const index = items.value.findIndex((item) => item.id === currentId.value);
    if (index === items.value.length - 1 && items.value.length < total.value) await more();
  }
  watch(
    () => `${scope.value?.type}:${scope.value?.id}`,
    () => {
      revision++;
      controller.abort();
      controller = new AbortController();
      pending = null;
      page = 0;
      items.value = [];
      title.value = '';
      total.value = 0;
      error.value = '';
      busy.value = false;
      if (scope.value) void locate();
    },
    { immediate: true },
  );
  watch(currentId, () => {
    if (scope.value) void locate();
  });
  onScopeDispose(() => {
    revision++;
    controller.abort();
  });
  return { items, title, total, busy, error, more, locate, ensureNext };
}

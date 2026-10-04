export type FeedItem = { id: string };
export type CommentQuery = { videoId: string; q?: string; root?: string };
export type CommentPage<T> = { items: T[]; total: number; page: number; page_size: number };
export type CommentFeed<T> = {
  items: T[];
  total: number;
  page: number;
  hasMore: boolean;
  loading: boolean;
  error: string;
};
export function emptyCommentFeed<T>(): CommentFeed<T> {
  return { items: [], total: 0, page: 0, hasMore: true, loading: false, error: '' };
}
export function createCommentFeed<T extends FeedItem>(
  state: CommentFeed<T>,
  fetchPage: (
    query: CommentQuery & { page: number; page_size: number; sort: 'likes' },
    signal: AbortSignal,
  ) => Promise<CommentPage<T>>,
) {
  let generation = 0;
  let disposed = false;
  let current: CommentQuery | undefined;
  let controller: AbortController | undefined;
  async function more() {
    if (disposed || !current || state.loading || !state.hasMore) return;
    const ticket = generation;
    const page = state.page + 1;
    controller = new AbortController();
    state.loading = true;
    state.error = '';
    try {
      const result = await fetchPage(
        { ...current, page, page_size: 20, sort: 'likes' },
        controller.signal,
      );
      if (disposed || ticket !== generation) return;
      const seen = new Set(state.items.map((item) => item.id));
      for (const item of result.items) {
        if (!seen.has(item.id)) {
          seen.add(item.id);
          state.items.push(item);
        }
      }
      state.total = result.total;
      state.page = page;
      state.hasMore = result.items.length > 0 && page * result.page_size < result.total;
    } catch (error) {
      if (!disposed && ticket === generation)
        state.error = error instanceof Error ? error.message : '评论读取失败，请重试';
    } finally {
      if (!disposed && ticket === generation) state.loading = false;
    }
  }
  return {
    more,
    reset(query: CommentQuery, eager = true) {
      generation++;
      controller?.abort();
      current = { ...query };
      Object.assign(state, emptyCommentFeed<T>());
      return eager ? more() : Promise.resolve();
    },
    dispose() {
      disposed = true;
      generation++;
      controller?.abort();
    },
  };
}

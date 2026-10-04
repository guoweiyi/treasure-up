export type SourceCategory = 'created' | 'collected' | 'following';
export type SourceChoice = {
  kind: 'favorite' | 'creator';
  source_id: string;
  title: string;
  owner_name?: string;
  media_count?: number;
};
export type SourcePage = { items: SourceChoice[]; next_page: number | null; cached: boolean };
export type SourcePickerState = {
  rows: SourceChoice[];
  nextPage: number | null;
  cached: boolean;
  loading: boolean;
  error: string;
  retryAt?: number;
  retryPending?: boolean;
};
export type SourceRequest = {
  account_id: string;
  category: SourceCategory;
  page: number;
  refresh: boolean;
};

export function createSourceLoader(
  state: SourcePickerState,
  fetchPage: (request: SourceRequest) => Promise<SourcePage>,
) {
  let generation = 0;
  let disposed = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  function cancelRetry() {
    clearTimeout(timer);
    state.retryPending = false;
  }
  async function load(
    accountId: string,
    category: SourceCategory,
    more = false,
    refresh = false,
    attempt = 0,
  ) {
    if (disposed || (more && !state.nextPage)) return;
    cancelRetry();
    state.retryAt = undefined;
    const ticket = ++generation;
    const page = more ? state.nextPage! : 1;
    state.error = '';
    if (!more || !accountId) {
      state.rows = [];
      state.nextPage = null;
      state.cached = false;
    }
    if (!accountId) {
      state.loading = false;
      return;
    }
    state.loading = true;
    try {
      const data = await fetchPage({ account_id: accountId, category, page, refresh });
      if (ticket !== generation) return;
      const seen = new Set(state.rows.map((row) => `${row.kind}:${row.source_id}`));
      for (const row of data.items) {
        const key = `${row.kind}:${row.source_id}`;
        if (!seen.has(key)) {
          seen.add(key);
          state.rows.push(row);
        }
      }
      state.nextPage = data.next_page;
      state.cached = data.cached;
    } catch (e) {
      if (ticket === generation) {
        state.error = e instanceof Error ? e.message : '读取来源失败，请重试';
        const failure = e as { status?: number; retryAfterSeconds?: number };
        if (failure?.status === 429 && Number.isFinite(failure.retryAfterSeconds)) {
          const seconds = Math.max(1, Math.min(86400, failure.retryAfterSeconds!));
          state.retryAt = Date.now() + seconds * 1000;
          if (attempt < 2 && seconds <= 300) {
            state.retryPending = true;
            timer = setTimeout(() => {
              if (ticket === generation && !disposed)
                void load(accountId, category, more, refresh, attempt + 1);
            }, seconds * 1000);
          }
        }
      }
    } finally {
      if (ticket === generation) state.loading = false;
    }
  }
  return {
    load,
    cancelRetry,
    dispose() {
      disposed = true;
      generation++;
      cancelRetry();
    },
  };
}

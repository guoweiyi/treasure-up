export type TransferRequestId = string | number | symbol | object;
export type TransferState = 'unknown' | 'idle' | 'waiting' | 'receiving' | 'stalled';
export interface TransferSnapshot {
  supported: boolean;
  state: TransferState;
  /** Actual media body bytes per second, never HLS bandwidth estimates or probe traffic. */
  bytesPerSecond: number | null;
  activeRequests: number;
  transferredBytes: number;
  sampledAt: number | null;
}
export const emptyTransferSnapshot = (): TransferSnapshot => ({
  supported: false,
  state: 'unknown',
  bytesPerSecond: null,
  activeRequests: 0,
  transferredBytes: 0,
  sampledAt: null,
});

type Request = { loaded: number; sampledAt: number; startedAt: number };
type Interval = { start: number; end: number; bytes: number };

/**
 * Feed cumulative `frag.stats.loaded` while real media requests are active.
 * The caller owns its 250 ms timer and discards callbacks from older player generations.
 * No URLs, signed tokens, test downloads, or HLS's estimated bandwidth are retained.
 */
export function createTransferMeter(now: () => number = () => performance.now()) {
  const windowMs = 1500;
  const requests = new Map<TransferRequestId, Request>();
  let intervals: Interval[] = [];
  let supported = false;
  let transferredBytes = 0;
  let lastProgressAt: number | null = null;
  let originAt: number | null = null;

  function prune(at: number) {
    intervals = intervals.filter((interval) => interval.end > at - windowMs).slice(-256);
  }
  function start(id: TransferRequestId) {
    const at = now();
    prune(at);
    supported = true;
    if (!requests.size && !intervals.length) originAt = at;
    requests.set(id, { loaded: 0, sampledAt: at, startedAt: at });
  }
  function sample(id: TransferRequestId, cumulativeLoaded: number) {
    const request = requests.get(id);
    if (!request || !Number.isFinite(cumulativeLoaded) || cumulativeLoaded < 0) return;
    const loaded = Math.floor(cumulativeLoaded);
    const at = now();
    if (at < request.sampledAt) return;
    // A lower or duplicate cumulative counter is not new traffic. A retry must start again.
    if (loaded > request.loaded) {
      const bytes = loaded - request.loaded;
      intervals.push({ start: request.sampledAt, end: Math.max(at, request.sampledAt + 1), bytes });
      transferredBytes += bytes;
      request.loaded = loaded;
      lastProgressAt = at;
    }
    request.sampledAt = at;
    prune(at);
  }
  function finish(id: TransferRequestId, cumulativeLoaded?: number) {
    if (cumulativeLoaded != null) sample(id, cumulativeLoaded);
    requests.delete(id);
  }
  function abort(id: TransferRequestId) {
    requests.delete(id);
  }
  function reset(observable = false) {
    requests.clear();
    intervals = [];
    supported = observable;
    transferredBytes = 0;
    lastProgressAt = null;
    originAt = null;
  }
  function snapshot(): TransferSnapshot {
    if (!supported) return emptyTransferSnapshot();
    const at = now();
    prune(at);
    let state: TransferState = requests.size ? 'waiting' : 'idle';
    let bytesPerSecond = 0;
    if (requests.size) {
      const oldestStart = Math.min(
        ...Array.from(requests.values(), (request) => request.startedAt),
      );
      if (lastProgressAt != null && at - lastProgressAt < windowMs) {
        const since = Math.max(originAt ?? at, at - windowMs);
        let bytes = 0;
        for (const interval of intervals) {
          const overlap = Math.max(0, Math.min(at, interval.end) - Math.max(since, interval.start));
          bytes += (interval.bytes * overlap) / Math.max(1, interval.end - interval.start);
        }
        bytesPerSecond = (bytes * 1000) / Math.max(250, at - since);
        state = bytesPerSecond > 0 ? 'receiving' : 'waiting';
      } else if (at - Math.max(lastProgressAt ?? oldestStart, oldestStart) >= windowMs)
        state = 'stalled';
    }
    return {
      supported: true,
      state,
      bytesPerSecond,
      activeRequests: requests.size,
      transferredBytes,
      sampledAt: lastProgressAt,
    };
  }
  return { start, sample, finish, abort, snapshot, reset };
}

/** Decimal network units: MB/s is bytes, distinct from a video's Mbps bitrate. */
export function formatTransferRate(bytesPerSecond: number | null | undefined) {
  if (bytesPerSecond == null || !Number.isFinite(bytesPerSecond) || bytesPerSecond < 0) return '—';
  const units = ['B/s', 'KB/s', 'MB/s', 'GB/s', 'TB/s'];
  let value = bytesPerSecond,
    unit = 0;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit++;
  }
  const digits = unit === 0 ? 0 : value < 10 ? 2 : 1;
  return `${Number(value.toFixed(digits))} ${units[unit]}`;
}

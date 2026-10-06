import { api, session, write } from '../api.ts';
import type { Playback, PlaybackRoute } from '../types';

const observations = new Map<string, number>();
const ttl = 5 * 60 * 1000;
type PlaybackInput = {
  part_id: string;
  variant_id?: string;
  route_id?: string;
  protocol?: 'auto' | 'hls' | 'file';
};

async function probe(data: Playback, route: PlaybackRoute, signal: AbortSignal) {
  const id = data.session_id || data.id;
  if (!id || !route.probe_url) return false;
  const url = new URL(route.probe_url, location.origin);
  if (url.origin !== location.origin) return false;
  const budget = Math.max(16384, Math.min(131072, route.probe_bytes || 65536));
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener('abort', abort, { once: true });
  const timeout = setTimeout(abort, 1200);
  const start = performance.now();
  let latency = 0,
    read = 0,
    succeeded = false;
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  try {
    const response = await fetch(url, {
      headers: { Range: `bytes=0-${budget - 1}` },
      credentials: 'same-origin',
      cache: 'no-store',
      signal: controller.signal,
    });
    latency = performance.now() - start;
    if (!response.ok || !response.body) throw new Error('probe unavailable');
    reader = response.body.getReader();
    while (read < budget) {
      const chunk = await reader.read();
      if (chunk.done) break;
      read += Math.min(chunk.value.byteLength, budget - read);
    }
    succeeded = read > 0;
  } catch {
    succeeded = false;
  } finally {
    await reader?.cancel().catch(() => {});
    clearTimeout(timeout);
    signal.removeEventListener('abort', abort);
  }
  if (signal.aborted) return false;
  const elapsed = Math.max(1, performance.now() - start);
  try {
    await write(`/playback-sessions/${id}/observations`, {
      route_id: route.id,
      latency_ms: Math.min(latency || elapsed, elapsed),
      elapsed_ms: elapsed,
      bytes_read: read,
      succeeded,
    });
    observations.set(`${session.user?.id}:${route.id}`, Date.now());
    return true;
  } catch {
    return false;
  }
}

export async function createPlayback(
  input: PlaybackInput,
  signal: AbortSignal,
  onProbe: (active: boolean) => void,
  measure = true,
) {
  const create = () =>
    api<Playback>('/playback-sessions', { method: 'POST', body: JSON.stringify(input), signal });
  const data = await create();
  const routes = data.routes?.filter((route) => route.status !== 'unavailable') || [];
  if (!measure || input.route_id || routes.length < 2 || signal.aborted) return data;
  const pending = routes
    .filter(
      (route) =>
        route.probe_url &&
        Date.now() - (observations.get(`${session.user?.id}:${route.id}`) || 0) > ttl,
    )
    .slice(0, 3);
  if (!pending.length) return data;
  onProbe(true);
  // Measurements improve subsequent automatic selections without delaying playback
  // or replacing a stream that has already begun playing.
  void Promise.allSettled(pending.map((route) => probe(data, route, signal))).finally(() => {
    if (!signal.aborted) onProbe(false);
  });
  return data;
}

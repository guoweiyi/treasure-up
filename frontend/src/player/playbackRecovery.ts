import type { Playback } from '../types';

export type PlaybackInput = {
  part_id: string;
  variant_id?: string;
  route_id?: string;
  protocol?: 'auto' | 'hls' | 'file';
};
export class MediaLoadError extends Error {
  kind: 'network' | 'media';
  constructor(kind: 'network' | 'media', message: string) {
    super(message);
    this.kind = kind;
  }
}
export function mediaFailureKind(code?: number): 'network' | 'media' {
  return code === 3 || code === 4 ? 'media' : 'network';
}
export function recoveryStartState(
  position: number,
  paused: boolean,
  startupPending: boolean,
  playRequested: boolean,
) {
  return {
    position: Number.isFinite(position) ? Math.max(0, position) : 0,
    paused: startupPending ? !playRequested : paused,
  };
}

export function createProtocolFallback() {
  const files = new Set<string>();
  const key = (partId: string, variantId: string) => `${partId}:${variantId}`;
  return {
    reset: () => files.clear(),
    input(input: PlaybackInput): PlaybackInput {
      return input.variant_id && files.has(key(input.part_id, input.variant_id))
        ? { ...input, protocol: 'file' }
        : input;
    },
    claim(partId: string, data: Playback | null, routeId?: string): PlaybackInput | null {
      // A package ID is not the source ID. Never choose a different codec or
      // compatible copy when the server omitted the original's identity.
      const original = data?.source_variant_id;
      if (data?.protocol !== 'hls' || !original || files.has(key(partId, original))) return null;
      files.add(key(partId, original));
      return { part_id: partId, variant_id: original, route_id: routeId, protocol: 'file' };
    },
  };
}

export async function loadWithProtocolFallback(
  input: PlaybackInput,
  fallback: ReturnType<typeof createProtocolFallback>,
  callbacks: {
    current: () => boolean;
    create: (input: PlaybackInput) => Promise<Playback>;
    attach: (data: Playback) => Promise<void>;
    restore: () => Promise<void>;
  },
): Promise<Playback | null> {
  let next = fallback.input(input);
  for (let attempt = 0; attempt < 2; attempt++) {
    const data = await callbacks.create(next);
    if (!callbacks.current()) return null;
    if (
      next.protocol === 'file' &&
      (data.protocol !== 'file' || data.variant_id !== next.variant_id)
    )
      throw new Error('文件直读返回的版本与指定原档不符');
    try {
      await callbacks.attach(data);
      if (!callbacks.current()) return null;
      await callbacks.restore();
      return callbacks.current() ? data : null;
    } catch (error) {
      if (!callbacks.current()) return null;
      const retry =
        error instanceof MediaLoadError && error.kind === 'media'
          ? fallback.claim(input.part_id, data, input.route_id)
          : null;
      if (!retry || attempt !== 0) throw error;
      next = retry;
    }
  }
  return null;
}

import type { Playback } from '../types';
import type { PlaybackInput } from './playbackRecovery';

/** One network recovery per selection. An explicit node is never silently replaced. */
export function createRouteRecovery() {
  let attempted = false;
  return {
    reset() {
      attempted = false;
    },
    claim(
      partId: string,
      variantId: string,
      selectedRoute: string,
      playback: Playback | null,
    ): PlaybackInput | null {
      if (attempted || !partId || !playback) return null;
      attempted = true;
      const alternative = !selectedRoute
        ? playback.routes?.find(
            (route) => route.id !== playback.selected_route_id && route.status !== 'unavailable',
          )
        : undefined;
      return {
        part_id: partId,
        variant_id: variantId || playback.source_variant_id || playback.variant_id,
        route_id: selectedRoute || alternative?.id || playback.selected_route_id,
        ...(playback.protocol ? { protocol: playback.protocol } : {}),
      };
    },
  };
}

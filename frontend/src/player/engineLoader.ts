// Kept outside the route's static graph: video details can render before these
// player libraries finish downloading. Concurrent mounts share module loading.
export function loadPlayerEngines() {
  return Promise.all([import('artplayer'), import('artplayer-plugin-danmuku')]);
}

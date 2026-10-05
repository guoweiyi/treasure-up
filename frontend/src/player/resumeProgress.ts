export function validResumePosition(position: number, duration: number) {
  return Number.isFinite(position) &&
    position > 0 &&
    Number.isFinite(duration) &&
    position < duration - 3
    ? position
    : 0;
}

// One optional read per setup, shared by native-ready and HLS/file recovery.
// The budget starts now, not when playback is ready. A stalled progress API
// cannot hold video startup indefinitely or seek a later player backwards.
export function preloadProgress(
  load: (signal: AbortSignal) => Promise<{ position: number }>,
  parent: AbortSignal,
  { enabled = true, timeoutMs = 400 } = {},
) {
  const controller = new AbortController();
  let settled = false,
    position = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let resolve!: (position: number) => void;
  const ready = new Promise<number>((done) => {
    resolve = done;
  });
  const finish = (value = 0) => {
    if (settled) return;
    settled = true;
    clearTimeout(timer);
    parent.removeEventListener('abort', cancel);
    position = Number.isFinite(value) && value > 0 ? value : 0;
    resolve(position);
  };
  const cancel = () => {
    finish();
    controller.abort();
  };
  if (!enabled || parent.aborted) cancel();
  else {
    parent.addEventListener('abort', cancel, { once: true });
    timer = setTimeout(cancel, Math.max(0, timeoutMs));
    void Promise.resolve()
      .then(() => {
        if (controller.signal.aborted) return { position: 0 };
        return load(controller.signal);
      })
      .then(
        (value) => finish(value?.position),
        () => finish(),
      );
  }
  return { ready, peek: () => position, cancel };
}

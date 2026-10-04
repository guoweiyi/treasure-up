export function createScopedInterval(onCleanup: (cleanup: () => void) => void) {
  let timer: ReturnType<typeof setInterval> | undefined;
  let disposed = false;
  onCleanup(() => {
    disposed = true;
    clearInterval(timer);
  });
  return (callback: () => void, interval: number) => {
    // An async setup may finish after its component or watcher was disposed.
    if (disposed) return;
    clearInterval(timer);
    timer = setInterval(callback, interval);
  };
}

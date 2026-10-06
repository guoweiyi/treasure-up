/** Renew signed media links only while the viewer is using the player. */
export function createUrlRenewal(
  active: () => boolean,
  renew: () => void,
  clock = {
    now: () => Date.now(),
    schedule: (run: () => void, delay: number) => setTimeout(run, delay),
    cancel: (timer: ReturnType<typeof setTimeout>) => clearTimeout(timer),
  },
) {
  let expires = 0,
    attempted = false,
    timer: ReturnType<typeof setTimeout> | undefined;
  function check() {
    if (!expires || attempted || !active() || clock.now() < expires - 60000) return;
    attempted = true;
    renew();
  }
  function set(value?: string | null) {
    if (timer !== undefined) clock.cancel(timer);
    timer = undefined;
    expires = value ? Date.parse(value) : 0;
    attempted = false;
    if (!Number.isFinite(expires) || !expires) {
      expires = 0;
      return;
    }
    timer = clock.schedule(check, Math.max(0, Math.min(2147483647, expires - clock.now() - 60000)));
  }
  return { set, check, stop: () => set(null) };
}

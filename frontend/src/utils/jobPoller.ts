export function createJobPoller(
  fetchJob: (id: string) => Promise<Record<string, any>>,
  update: (job: Record<string, any>) => void,
  failure: (error: unknown) => void,
  terminal: (job: Record<string, any>) => boolean,
) {
  let generation = 0,
    timer: ReturnType<typeof setTimeout> | undefined;
  function stop() {
    generation++;
    clearTimeout(timer);
  }
  return {
    stop,
    start(id: string) {
      stop();
      const ticket = generation;
      let failures = 0;
      async function poll() {
        try {
          const job = await fetchJob(id);
          if (ticket !== generation) return;
          update(job);
          failures = 0;
          if (terminal(job)) return;
        } catch (error) {
          if (ticket !== generation) return;
          failure(error);
          failures++;
          if (failures >= 3) return;
        }
        timer = setTimeout(poll, failures ? 10000 : 2500);
      }
      void poll();
    },
  };
}

export type CaptureCounts = { queued: number; reused: number; skipped: number };
export type CaptureProgress = CaptureCounts & {
  completed: string[];
  remaining: string[];
  total: number;
};
export type CaptureOutcome = CaptureProgress & { failure?: unknown; cancelled?: boolean };

// Source validation is deliberately paced. Small sequential batches stay under
// the proxy deadline and commit progress before another batch can be rejected.
export async function submitCreatorCapture(
  bvids: string[],
  submit: (batch: string[]) => Promise<CaptureCounts>,
  onProgress: (progress: CaptureProgress) => void,
  isActive: () => boolean = () => true,
  shouldContinue: () => boolean = () => true,
): Promise<CaptureOutcome> {
  const pending = [...new Set(bvids)];
  let progress: CaptureProgress = {
    queued: 0,
    reused: 0,
    skipped: 0,
    completed: [],
    remaining: pending,
    total: pending.length,
  };
  while (progress.remaining.length) {
    if (!isActive() || !shouldContinue()) return { ...progress, cancelled: true };
    const batch = progress.remaining.slice(0, 3);
    let result: CaptureCounts;
    try {
      result = await submit(batch);
    } catch (failure) {
      // A rejected/uncertain batch is never retried automatically, especially
      // after a source challenge. Previously accepted videos stay accepted.
      return { ...progress, failure };
    }
    if (!isActive()) return { ...progress, cancelled: true };
    progress = {
      queued: progress.queued + result.queued,
      reused: progress.reused + result.reused,
      skipped: progress.skipped + result.skipped,
      completed: [...progress.completed, ...batch],
      remaining: progress.remaining.slice(batch.length),
      total: progress.total,
    };
    onProgress(progress);
  }
  return progress;
}

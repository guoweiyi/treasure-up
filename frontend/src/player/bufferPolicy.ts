import type { TransferState } from './transferMeter.ts';

// Adapt look-ahead rather than changing the archived video/audio rendition.
const MiB = 1024 * 1024;
export type ConnectionHint = { saveData?: boolean; effectiveType?: string };
export function bufferPolicy(
  input: {
    bitrateBps?: number | null;
    throughputBps?: number | null;
    playbackRate?: number;
    stalled?: boolean;
    connection?: ConnectionHint;
  } = {},
) {
  const finite = (value: number | null | undefined) =>
    typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : 0;
  const bitrate = finite(input.bitrateBps);
  const rate = Math.max(0.25, Math.min(4, finite(input.playbackRate) || 1));
  const throughput = finite(input.throughputBps);
  let seconds = throughput ? 24 : 12;
  if (input.stalled || ['slow-2g', '2g', '3g'].includes(input.connection?.effectiveType || ''))
    seconds = 48;
  if (throughput && bitrate && throughput < bitrate * rate * 1.4) seconds = 48;
  // Respect a device's data-saving preference without dropping picture/audio quality.
  if (input.connection?.saveData) seconds = 8;
  const bytes = input.connection?.saveData ? 16 * MiB : 48 * MiB;
  // A single source GOP may exceed this budget. Never pretend it is a hard byte cap.
  const target = Math.max(
    4,
    Math.floor(Math.min(seconds * rate, bitrate ? (bytes * 8) / bitrate : 24)),
  );
  return {
    maxBufferLength: target,
    maxMaxBufferLength: target,
    maxBufferSize: bytes,
    backBufferLength: Math.min(10, Math.floor(target / 2)),
  };
}

export function bufferedAhead(video: Pick<HTMLVideoElement, 'buffered' | 'currentTime'>) {
  try {
    for (let index = 0; index < video.buffered.length; index++)
      if (
        video.buffered.start(index) <= video.currentTime + 0.05 &&
        video.buffered.end(index) > video.currentTime
      )
        return video.buffered.end(index) - video.currentTime;
  } catch {
    /* Detached ranges during a source change. */
  }
  return 0;
}

/** A real buffering stall may use the existing bounded same-rendition route recovery. */
export function shouldRecoverStall(input: {
  elapsedMs: number;
  paused: boolean;
  readyState: number;
  bufferedSeconds: number;
  bytesPerSecond: number | null;
  activeRequests: number;
  transferState: TransferState;
  bitrateBps?: number | null;
  playbackRate: number;
}) {
  // A completed download can be awaiting demux/append/decode. Its idle zero
  // is not network failure, and switching would discard already received data.
  if (
    input.activeRequests <= 0 ||
    !['waiting', 'receiving', 'stalled'].includes(input.transferState)
  )
    return false;
  if (input.paused || input.readyState >= 3 || input.bufferedSeconds >= 1 || input.elapsedMs < 8000)
    return false;
  if (input.bytesPerSecond === 0) return true;
  // Slow but progressing requests get more time; a buffer already present is a decoder issue.
  return (
    input.elapsedMs >= 15000 &&
    input.bytesPerSecond !== null &&
    !!input.bitrateBps &&
    input.bitrateBps > 0 &&
    input.bytesPerSecond * 8 < input.bitrateBps * input.playbackRate * 0.85
  );
}

import { unknownAudioSupport, type AudioCapability } from './audioCapabilities.ts';
import { emptyTransferSnapshot, type TransferSnapshot } from './transferMeter.ts';
export interface RuntimeStats {
  engine: string;
  mime: string | null;
  codecs: string | null;
  width: number | null;
  height: number | null;
  droppedFrames: number | null;
  totalFrames: number | null;
  loadedFragments: number | null;
  host: string | null;
  hostIsFinal: boolean;
  networkBps: number | null;
  networkSampleAt: number | null;
  transfer: TransferSnapshot;
  audioSupport: { ec3: AudioCapability; spatial: AudioCapability };
}
export const emptyRuntimeStats = (): RuntimeStats => ({
  engine: '—',
  mime: null,
  codecs: null,
  width: null,
  height: null,
  droppedFrames: null,
  totalFrames: null,
  loadedFragments: null,
  host: null,
  hostIsFinal: false,
  networkBps: null,
  networkSampleAt: null,
  transfer: emptyTransferSnapshot(),
  audioSupport: unknownAudioSupport(),
});
export function deliveryHost(url: string, base = 'http://localhost') {
  try {
    const parsed = new URL(url, base);
    return ['http:', 'https:'].includes(parsed.protocol) ? parsed.host : null;
  } catch {
    return null;
  }
}
// Cached responses and cross-origin responses without Timing-Allow-Origin cannot prove network throughput.
export function networkSample(
  entry: Pick<
    PerformanceResourceTiming,
    'transferSize' | 'encodedBodySize' | 'responseStart' | 'responseEnd'
  > & { responseStatus?: number; deliveryType?: string },
) {
  const elapsed = entry.responseEnd - entry.responseStart;
  return entry.responseStatus !== 304 &&
    entry.deliveryType !== 'cache' &&
    entry.transferSize !== 300 &&
    entry.transferSize >= entry.encodedBodySize &&
    entry.transferSize > 0 &&
    entry.encodedBodySize > 0 &&
    entry.responseStart > 0 &&
    elapsed > 0
    ? (entry.encodedBodySize * 8000) / elapsed
    : null;
}

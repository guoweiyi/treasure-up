import type Hls from 'hls.js';
import type { Fragment, Part, LoaderStats } from 'hls.js';
import { probeAudioSupport, type AudioFormat } from './audioCapabilities.ts';
import { bufferPolicy, type ConnectionHint } from './bufferPolicy.ts';
import { createTransferMeter } from './transferMeter.ts';
import { prefersNativeHls, selectHlsEngine } from './hlsEngine.ts';
import {
  deliveryHost,
  emptyRuntimeStats,
  networkSample,
  type RuntimeStats,
} from './runtimeStats.ts';

type PlaybackMedia = AudioFormat & {
  total_bitrate_bps?: number | null;
  video_bitrate_bps?: number | null;
};
type Connection = ConnectionHint &
  Partial<Pick<EventTarget, 'addEventListener' | 'removeEventListener'>>;
type ActiveTransfer = { id: symbol; frag: Fragment; started: boolean; staleStats?: LoaderStats };
const finitePositive = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : 0;
const validLoaded = (value: number | undefined) =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0;

async function loadBrowserHls() {
  const { default: HlsPlayer } = await import('hls.js');
  // The ESM build does not contain the UMD inline-worker bundle. Ship the
  // matching, patched official worker explicitly so parsing stays off-thread.
  const workerPath =
    typeof document === 'undefined'
      ? undefined
      : (await import('hls.js/dist/hls.worker.js?url')).default;
  return { default: HlsPlayer, workerPath };
}

export function createMediaAdapter(
  onError: (kind: 'network' | 'media', message: string) => void,
  onStats: (stats: RuntimeStats) => void = () => {},
  loadHls: () => Promise<{ default: typeof Hls; workerPath?: string }> = loadBrowserHls,
) {
  let hls: Hls | null = null;
  let generation = 0;
  let observer: PerformanceObserver | null = null;
  let timer: ReturnType<typeof setInterval> | undefined;
  let stats = emptyRuntimeStats();
  let cleanupAttachment: (() => void) | undefined;
  let onWaiting = () => {};
  const meter = createTransferMeter();
  function destroy() {
    generation++;
    observer?.disconnect();
    observer = null;
    clearInterval(timer);
    timer = undefined;
    cleanupAttachment?.();
    cleanupAttachment = undefined;
    onWaiting = () => {};
    meter.reset();
    stats = emptyRuntimeStats();
    hls?.destroy();
    hls = null;
  }
  async function attach(
    video: HTMLVideoElement,
    url: string,
    protocol?: 'file' | 'hls',
    media?: PlaybackMedia,
    startPosition = 0,
  ) {
    destroy();
    const key = generation;
    const urls = new Set<string>();
    const fragments = new Set<string>();
    const active = new Map<Fragment | Part, ActiveTransfer>();
    const connection =
      typeof navigator === 'undefined'
        ? undefined
        : (navigator as Navigator & { connection?: Connection }).connection;
    const bitrateBps =
      finitePositive(media?.total_bitrate_bps) ||
      finitePositive(media?.audio_bitrate_bps) + finitePositive(media?.video_bitrate_bps);
    let throughputBps: number | null = null;
    let stalled = false;
    const applyBufferPolicy = () => {
      if (key !== generation || !hls) return;
      Object.assign(
        hls.config,
        bufferPolicy({
          bitrateBps,
          throughputBps,
          playbackRate: video.playbackRate,
          stalled,
          connection,
        }),
      );
    };
    const onConnectionChange = () => {
      // A link type is only a buffer hint, never a download speed measurement.
      throughputBps = null;
      applyBufferPolicy();
    };
    onWaiting = () => {
      stalled = true;
      applyBufferPolicy();
    };
    video.addEventListener?.('ratechange', applyBufferPolicy);
    connection?.addEventListener?.('change', onConnectionChange);
    const abortTransfer = (segment: Fragment | Part) => {
      const request = active.get(segment);
      if (request) meter.abort(request.id);
      active.delete(segment);
    };
    const abortAll = () => {
      for (const segment of active.keys()) abortTransfer(segment);
    };
    cleanupAttachment = () => {
      abortAll();
      video.removeEventListener?.('ratechange', applyBufferPolicy);
      connection?.removeEventListener?.('change', onConnectionChange);
    };
    const sampleTransfers = () => {
      for (const [segment, request] of active) {
        const load = segment.stats;
        if (load === request.staleStats) continue;
        if (load?.aborted) {
          abortTransfer(segment);
          continue;
        }
        if (!validLoaded(load?.loaded)) continue;
        if (!request.started) {
          meter.start(request.id);
          request.started = true;
        }
        meter.sample(request.id, load.loaded);
        // hls.js does not emit FRAG_LOADED for initSegment. Its loader timing is
        // the completion signal, otherwise that request would remain active forever.
        if (finitePositive(load.loading?.end)) {
          meter.finish(request.id);
          active.delete(segment);
        }
      }
      stats.transfer = meter.snapshot();
    };
    const base = typeof location === 'undefined' ? 'http://localhost' : location.href;
    const track = (value: string) => {
      try {
        urls.add(new URL(value, base).href);
        if (urls.size > 256) urls.delete(urls.values().next().value!);
      } catch {
        /* Unknown URI. */
      }
    };
    track(url);
    stats = { ...emptyRuntimeStats(), host: deliveryHost(url, base) };
    const publish = () => {
      if (key !== generation) return;
      sampleTransfers();
      const quality = video.getVideoPlaybackQuality?.();
      stats.width = video.videoWidth || null;
      stats.height = video.videoHeight || null;
      stats.droppedFrames = quality?.droppedVideoFrames ?? null;
      stats.totalFrames = quality?.totalVideoFrames ?? null;
      if (stats.networkSampleAt && Date.now() - stats.networkSampleAt > 15000)
        stats.networkBps = null;
      onStats({ ...stats });
    };
    if (typeof PerformanceObserver !== 'undefined') {
      observer = new PerformanceObserver((list) => {
        if (key !== generation) return;
        for (const entry of list.getEntries() as PerformanceResourceTiming[]) {
          if (!urls.has(entry.name)) continue;
          const bps = networkSample(entry);
          if (bps !== null) {
            stats.networkBps = bps;
            stats.networkSampleAt = Date.now();
          }
        }
        publish();
      });
      try {
        observer.observe({ type: 'resource' });
      } catch {
        observer.disconnect();
        observer = null;
      }
    }
    timer = setInterval(publish, 250);
    const native = protocol === 'hls' && !!video.canPlayType('application/vnd.apple.mpegurl');
    const environment =
      typeof navigator === 'undefined'
        ? {}
        : {
            userAgent: navigator.userAgent,
            platform: navigator.platform,
            maxTouchPoints: navigator.maxTouchPoints,
          };
    let audioProbe = 0;
    const probeEngineAudio = (engine: 'file' | 'media-source') => {
      const probeKey = ++audioProbe;
      void probeAudioSupport(
        media,
        typeof navigator === 'undefined' ? undefined : navigator.mediaCapabilities,
        engine,
      ).then((value) => {
        if (key !== generation || probeKey !== audioProbe) return;
        stats.audioSupport = value;
        publish();
      });
    };
    const attachNative = () => {
      stats.engine = '浏览器原生 HLS';
      probeEngineAudio('file');
      publish();
      video.src = url;
    };
    // Inline Safari playback retains our controls and danmaku in the page.
    video.controls = false;
    video.playsInline = true;
    video.setAttribute('playsinline', '');
    video.setAttribute('webkit-playsinline', '');
    if (protocol !== 'hls') {
      stats.engine = 'HTMLVideoElement · 文件直读';
      probeEngineAudio('file');
      publish();
      video.src = url;
      return;
    }
    // Native HLS can retain the system EC-3/JOC path in every capable WebView,
    // including iOS browsers whose user agent does not identify them as Safari.
    if (native && prefersNativeHls(environment, media)) {
      attachNative();
      return;
    }
    try {
      const { default: HlsPlayer, workerPath } = await loadHls();
      if (key !== generation) return;
      const engine = selectHlsEngine({
        ...environment,
        media,
        nativeAvailable: native,
        mseAvailable: HlsPlayer.isSupported(),
      });
      if (engine === 'mse') {
        stats.engine = `hls.js ${HlsPlayer.version} · MSE`;
        probeEngineAudio('media-source');
        stats.loadedFragments = 0;
        hls = new HlsPlayer({
          enableWorker: true,
          ...(workerPath ? { workerPath } : {}),
          // Let hls.js select its official streaming FetchLoader; an fLoader override
          // would silently turn progressive decoding back into whole-fragment loading.
          progressive: true,
          ...bufferPolicy({ bitrateBps, playbackRate: video.playbackRate, connection }),
          ...(Number.isFinite(startPosition) && startPosition > 0 ? { startPosition } : {}),
        });
        hls.on(HlsPlayer.Events.ERROR, (_, data) => {
          if (key !== generation) return;
          const network = data.type === HlsPlayer.ErrorTypes.NETWORK_ERROR;
          if (data.frag && network) {
            for (const [segment, request] of active)
              if (request.frag === data.frag) abortTransfer(segment);
          }
          if (!data.fatal) {
            publish();
            return;
          }
          abortAll();
          hls?.stopLoad();
          publish();
          onError(
            network ? 'network' : 'media',
            network
              ? '分片播放地址暂时不可用'
              : '当前浏览器无法解码此归档版本。可手动选择已保存的兼容版本，原档不会被更改。',
          );
        });
        hls.on(HlsPlayer.Events.BUFFER_CREATED, (_, data) => {
          if (key !== generation) return;
          const tracks = Object.values(data.tracks);
          stats.mime =
            [...new Set(tracks.map((track) => track.container).filter(Boolean))].join(' / ') ||
            null;
          stats.codecs =
            tracks
              .map((track) => track.codec)
              .filter(Boolean)
              .join(', ') || null;
          publish();
        });
        hls.on(HlsPlayer.Events.FRAG_LOADING, (_, data) => {
          if (key !== generation) return;
          track(data.frag.url);
          if (!['main', 'audio'].includes(data.frag.type)) return;
          const segment = data.part || data.frag;
          abortTransfer(segment);
          // Each retry owns a fresh cumulative counter. Bound abandoned request refs.
          while (active.size >= 64) abortTransfer(active.keys().next().value!);
          const previous = segment.stats;
          const staleStats =
            previous?.aborted || finitePositive(previous?.loading?.end) ? previous : undefined;
          const request: ActiveTransfer = {
            id: Symbol('media-request'),
            frag: data.frag,
            started: false,
            staleStats,
          };
          active.set(segment, request);
          if (!staleStats && validLoaded(segment.stats?.loaded)) {
            meter.start(request.id);
            request.started = true;
          }
        });
        hls.on(HlsPlayer.Events.FRAG_LOAD_EMERGENCY_ABORTED, (_, data) => {
          if (key !== generation) return;
          abortTransfer(data.part || data.frag);
          publish();
        });
        hls.on(HlsPlayer.Events.FRAG_LOADED, (_, data) => {
          if (key !== generation) return;
          const segment = data.part || data.frag;
          const request = active.get(segment);
          const load: LoaderStats | undefined = segment.stats;
          if (request && load !== request.staleStats) {
            if (!request.started && validLoaded(load?.loaded)) meter.start(request.id);
            meter.finish(request.id, validLoaded(load?.loaded) ? load.loaded : undefined);
            active.delete(segment);
          }
          if (
            request &&
            load !== request.staleStats &&
            finitePositive(load?.loaded) &&
            finitePositive(load?.loading?.start) &&
            finitePositive(load?.loading?.end) > load.loading.start
          ) {
            const sample = (load.loaded * 8000) / (load.loading.end - load.loading.start);
            throughputBps = throughputBps === null ? sample : throughputBps * 0.6 + sample * 0.4;
            stalled = false;
            applyBufferPolicy();
          }
          if (typeof data.frag.sn === 'number')
            fragments.add(`${data.frag.type}:${data.frag.level}:${data.frag.cc}:${data.frag.sn}`);
          stats.loadedFragments = fragments.size;
          const network = data.networkDetails;
          const responseUrl =
            network &&
            ('responseURL' in network
              ? network.responseURL
              : 'url' in network
                ? network.url
                : undefined);
          if (typeof responseUrl === 'string') {
            stats.host = deliveryHost(responseUrl, base);
            stats.hostIsFinal = !!stats.host;
          }
          publish();
        });
        publish();
        hls.loadSource(url);
        hls.attachMedia(video);
      } else if (engine === 'native') attachNative();
      else onError('media', '当前浏览器不支持此分片播放格式，请使用支持 HLS 与原档编码的浏览器。');
    } catch {
      if (key !== generation) return;
      if (native) {
        hls?.destroy();
        hls = null;
        abortAll();
        meter.reset();
        attachNative();
      } else onError('media', '分片播放器加载失败，请刷新后重试。');
    }
  }
  return { attach, destroy, onWaiting: () => onWaiting() };
}

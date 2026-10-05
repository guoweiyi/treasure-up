import type Hls from 'hls.js';
import { probeAudioSupport, type AudioFormat } from './audioCapabilities.ts';
import {
  deliveryHost,
  emptyRuntimeStats,
  networkSample,
  type RuntimeStats,
} from './runtimeStats.ts';

export function createMediaAdapter(
  onError: (kind: 'network' | 'media', message: string) => void,
  onStats: (stats: RuntimeStats) => void = () => {},
  loadHls: () => Promise<{ default: typeof Hls }> = () => import('hls.js'),
) {
  let hls: Hls | null = null;
  let generation = 0;
  let observer: PerformanceObserver | null = null;
  let timer: ReturnType<typeof setInterval> | undefined;
  let stats = emptyRuntimeStats();
  function destroy() {
    generation++;
    observer?.disconnect();
    observer = null;
    clearInterval(timer);
    hls?.destroy();
    hls = null;
  }
  async function attach(
    video: HTMLVideoElement,
    url: string,
    protocol?: 'file' | 'hls',
    media?: AudioFormat,
    startPosition = 0,
  ) {
    destroy();
    const key = generation;
    const urls = new Set<string>();
    const fragments = new Set<string>();
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
    timer = setInterval(publish, 1000);
    const native = protocol === 'hls' && !!video.canPlayType('application/vnd.apple.mpegurl');
    void probeAudioSupport(
      media,
      typeof navigator === 'undefined' ? undefined : navigator.mediaCapabilities,
      protocol === 'hls' && !native ? 'media-source' : 'file',
    ).then((value) => {
      if (key !== generation) return;
      stats.audioSupport = value;
      publish();
    });
    // Inline Safari playback retains our controls and danmaku in the page.
    video.controls = false;
    video.playsInline = true;
    video.setAttribute('playsinline', '');
    video.setAttribute('webkit-playsinline', '');
    if (protocol !== 'hls') {
      stats.engine = 'HTMLVideoElement · 文件直读';
      publish();
      video.src = url;
      return;
    }
    // Native HLS can retain the system EC-3/JOC path in every capable WebView,
    // including iOS browsers whose user agent does not identify them as Safari.
    if (native) {
      stats.engine = '浏览器原生 HLS';
      publish();
      video.src = url;
      return;
    }
    try {
      const { default: HlsPlayer } = await loadHls();
      if (key !== generation) return;
      if (HlsPlayer.isSupported()) {
        stats.engine = `hls.js ${HlsPlayer.version} · MSE`;
        stats.loadedFragments = 0;
        hls = new HlsPlayer({
          enableWorker: true,
          maxBufferLength: 30,
          maxMaxBufferLength: 60,
          backBufferLength: 30,
          ...(Number.isFinite(startPosition) && startPosition > 0 ? { startPosition } : {}),
        });
        hls.on(HlsPlayer.Events.ERROR, (_, data) => {
          if (key !== generation || !data.fatal) return;
          hls?.stopLoad();
          const network = data.type === HlsPlayer.ErrorTypes.NETWORK_ERROR;
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
          if (key === generation) track(data.frag.url);
        });
        hls.on(HlsPlayer.Events.FRAG_LOADED, (_, data) => {
          if (key !== generation) return;
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
      } else if (native) {
        stats.engine = '浏览器原生 HLS';
        publish();
        video.src = url;
      } else
        onError('media', '当前浏览器不支持此分片播放格式，请使用支持 HLS 与原档编码的浏览器。');
    } catch {
      if (key === generation) onError('media', '分片播放器加载失败，请刷新后重试。');
    }
  }
  return { attach, destroy };
}

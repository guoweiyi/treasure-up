import type Hls from 'hls.js';

export function createMediaAdapter(onError: (kind: 'network' | 'media', message: string) => void) {
  let hls: Hls | null = null;
  let generation = 0;
  function destroy() {
    generation++;
    hls?.destroy();
    hls = null;
  }
  async function attach(video: HTMLVideoElement, url: string, protocol?: 'file' | 'hls') {
    destroy();
    const key = generation;
    // Inline Safari playback retains our controls and danmaku in the page.
    video.controls = false;
    video.playsInline = true;
    video.setAttribute('playsinline', '');
    video.setAttribute('webkit-playsinline', '');
    if (protocol !== 'hls') {
      video.src = url;
      return;
    }
    const native = !!video.canPlayType('application/vnd.apple.mpegurl');
    const safari =
      /Safari/.test(navigator.userAgent) &&
      !/Chrome|Chromium|CriOS|Edg|OPR/.test(navigator.userAgent);
    if (safari && native) {
      video.src = url;
      return;
    }
    try {
      const { default: HlsPlayer } = await import('hls.js');
      if (key !== generation) return;
      if (HlsPlayer.isSupported()) {
        hls = new HlsPlayer({
          enableWorker: true,
          maxBufferLength: 30,
          maxMaxBufferLength: 60,
          backBufferLength: 30,
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
        hls.loadSource(url);
        hls.attachMedia(video);
      } else if (native) video.src = url;
      else onError('media', '当前浏览器不支持此分片播放格式，请使用支持 HLS 与原档编码的浏览器。');
    } catch {
      if (key === generation) onError('media', '分片播放器加载失败，请刷新后重试。');
    }
  }
  return { attach, destroy };
}

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue';
import type Artplayer from 'artplayer';
import type { Result as DanmakuPlugin, Mode } from 'artplayer-plugin-danmuku';
import { api, write, errorText, display, session } from '../api';
import type { Part, Playback, Danmaku, MediaProperties } from '../types';
import { createMediaAdapter } from '../player/mediaAdapter';
import { loadPlayerEngines } from '../player/engineLoader';
import { loadDanmaku } from '../player/danmakuLoader';
import { preloadProgress, validResumePosition } from '../player/resumeProgress';
import { emptyRuntimeStats } from '../player/runtimeStats';
import LoadingTransfer from '../player/LoadingTransfer.vue';
import { bufferedAhead, shouldRecoverStall } from '../player/bufferPolicy';
import type { MeasuredMedia } from '../player/mediaInfo';
import { sameOriginalAudioAlternative } from '../player/audioAlternatives';
import AudioCompatibilityNotice from './AudioCompatibilityNotice.vue';
import { createEndGuard } from '../player/queue';
import { createPlayback } from '../player/routing';
import { createUrlRenewal } from '../player/urlRenewal';
import { createRouteRecovery } from '../player/routeRecovery';
import {
  createProtocolFallback,
  loadWithProtocolFallback,
  MediaLoadError,
  mediaFailureKind,
  recoveryStartState,
  withProtocolPreference,
  type PlaybackInput,
} from '../player/playbackRecovery';
import { canUseElementFullscreen } from '../player/nativePlayback';
import PlaybackOptions from './PlaybackOptions.vue';
import DanmakuSettings from './DanmakuSettings.vue';
import PlayerSettingsPanel from './PlayerSettingsPanel.vue';
import { installPlayerControls } from '../player/controls';
import PlayerChoice from '../player/PlayerChoice.vue';
import { danmakuMargins, rateChoices } from '../player/layout';
import {
  defaultPreferences,
  readPreferences,
  preferenceKey,
  clamp,
  fontFamily,
  textShadow,
  includeAtDensity,
} from '../player/preferences';
const props = defineProps<{
  part: Part;
  poster?: string | null;
  mediaProperties?: Record<string, MediaProperties>;
  autoStart?: boolean;
  resume?: boolean;
}>();
const emit = defineEmits<{
  variant: [id: string];
  ended: [partId: string];
  playing: [value: boolean];
  routing: [value: { playback: Playback | null; routeId: string; busy: boolean }];
}>();
const runtimeStats = ref(emptyRuntimeStats());
const endGuard = createEndGuard();
const container = ref<HTMLDivElement>(),
  error = ref(''),
  note = ref(''),
  progressError = ref(''),
  mediaLoading = ref(true),
  busy = ref(false),
  variantId = ref(''),
  subtitle = ref(''),
  playback = ref<Playback | null>(null),
  dmCount = ref<number | null>(null);
const routeId = ref(''),
  playbackProtocol = ref<'auto' | 'file'>('auto'),
  volumeBalance = ref(false),
  probing = ref(false),
  switching = ref(false);
watch([playback, routeId, busy, switching], () => {
  emit('routing', {
    playback: playback.value,
    routeId: routeId.value,
    busy: busy.value || switching.value,
  });
});
const playableVariants = computed(() =>
  props.part.variants.filter((variant) => variant.kind !== 'hls'),
);
const currentMedia = computed(
  () =>
    ({
      audio_codec: playableVariants.value.find((variant) => variant.id === variantId.value)
        ?.audio_codec,
      ...props.mediaProperties?.[variantId.value],
      ...playback.value?.media,
    }) as MeasuredMedia,
);
const audioAlternative = computed(() =>
  sameOriginalAudioAlternative(
    variantId.value,
    playableVariants.value,
    props.mediaProperties,
    currentMedia.value,
  ),
);
watch(variantId, (id) => emit('variant', id));
let playbackController = new AbortController(),
  expectedVolume = 0.7;
const userVolume = ref(0.7),
  playbackRate = ref(1),
  fit = ref<'contain' | 'cover'>('contain');
let controlButtons: ReturnType<typeof installPlayerControls> | null = null;
let rawDanmaku: { text: string; time: number; mode: Mode; color: string }[] = [];
let danmakuReload: ReturnType<typeof setTimeout> | undefined;
let danmakuLoading: DanmakuPlugin | null = null;
let danmakuReloadRequested = false;
let rejectMediaLoad: ((error: Error) => void) | null = null;
const protocolFallback = createProtocolFallback();
const routeRecovery = createRouteRecovery();
let renewal = 0;
let waitingSince = 0;
let stallTimer: ReturnType<typeof setInterval> | undefined;
let renewalController: AbortController | null = null;
type PlaybackSnapshot = {
  position: number;
  paused: boolean;
  rate: number;
  volume: number;
  muted: boolean;
};
let renewalSnapshot: PlaybackSnapshot | null = null;
let startupPending = true,
  startupPlayRequested = false;
let initialProgress: ReturnType<typeof preloadProgress> | null = null;
const mediaAdapter = createMediaAdapter(
  handleMediaFailure,
  (value) => (runtimeStats.value = value),
);
const playerHost = ref<HTMLElement | null>(null),
  settingsOpen = ref(false),
  panelMode = ref<'settings' | 'rate'>('settings'),
  smallScreen = ref(false),
  fullscreenActive = ref(false);
const sheet = computed(() => smallScreen.value && !fullscreenActive.value);
const returnFocus = ref<HTMLElement | null>(null);
let screenQuery: MediaQueryList | null = null;
let initialPreferences = defaultPreferences(display.default_danmaku);
try {
  initialPreferences = readPreferences(
    localStorage.getItem(preferenceKey),
    display.default_danmaku,
  );
} catch {
  /* Storage is optional. */
}
const prefs = reactive(initialPreferences);
let art: Artplayer | null = null,
  resize: ResizeObserver | null = null,
  request = 0,
  lastSaved = 0,
  renewing = false,
  activePartId = '';
const urlRenewal = createUrlRenewal(
  () =>
    !!art && !art.video.paused && !art.video.ended && !renewing && !busy.value && !document.hidden,
  () => {
    void renew();
  },
);
watch(
  () => playback.value?.url_expires_at,
  (value) => urlRenewal.set(value),
);
function refreshVisiblePlayback() {
  if (!document.hidden) urlRenewal.check();
}
const family = computed(() => fontFamily(prefs));
const shadow = computed(() => textShadow(prefs));
const style = computed(() => ({
  '--dm-family': `${family.value}, sans-serif`,
  '--dm-weight': prefs.weight,
  '--dm-spacing': `${clamp(prefs.spacing, 0, 6)}px`,
  '--dm-shadow': shadow.value,
  '--dm-color': prefs.color,
}));
function plugin() {
  return art?.plugins.artplayerPluginDanmuku as DanmakuPlugin | undefined;
}
function syncPlayerStyles() {
  const player = art?.template.$player;
  if (!player) return;
  player.classList.add('treasure-archive-player');
  player.classList.toggle('dm-uniform', prefs.uniform);
  controlButtons?.danmaku.setAttribute('aria-pressed', String(prefs.visible));
  for (const [property, value] of Object.entries(style.value)) {
    player.style.setProperty(property, String(value));
  }
}
function screenScale() {
  return prefs.scaleWithScreen
    ? Math.max(0.6, (art?.template.$player.clientHeight || 450) / 450)
    : 1;
}
function applyPreferences() {
  syncPlayerStyles();
  const size = clamp(prefs.fontSize, 12, 120) * screenScale();
  const modes: Mode[] = [];
  if (prefs.rolling) modes.push(0);
  if (prefs.top) modes.push(1);
  if (prefs.bottom) modes.push(2);
  plugin()?.config({
    danmuku: plugin()?.option.danmuku || [],
    visible: prefs.visible,
    opacity: clamp(prefs.opacity, 0, 100) / 100,
    fontSize: size,
    speed: clamp(prefs.speed, 1, 10),
    margin: danmakuMargins(
      art?.template.$player.clientHeight || 450,
      prefs.area,
      prefs.subtitleSafe,
      art ? parseFloat(getComputedStyle(art.template.$bottom).paddingBottom) || 0 : 0,
    ),
    modes,
    antiOverlap: prefs.antiOverlap,
    synchronousPlayback: prefs.synchronousPlayback,
    beforeVisible: (dm) =>
      prefs.colored || ['#ffffff', '#fff', 'white'].includes(String(dm.color).toLowerCase()),
  });
  plugin()?.reset();
}
watch(
  prefs,
  () => {
    try {
      localStorage.setItem(preferenceKey, JSON.stringify(prefs));
    } catch {
      /* Playback remains available without storage. */
    }
    applyPreferences();
  },
  { deep: true },
);
function filteredDanmaku() {
  return rawDanmaku
    .filter((_, index) => includeAtDensity(index, prefs.density))
    .map((item) => ({ ...item, time: item.time + clamp(prefs.offset, -60, 60) }))
    .filter((item) => item.time >= 0)
    .map((item) => ({ ...item, time: Math.max(0.001, item.time) }));
}
async function reloadDanmaku() {
  const target = plugin();
  if (!target) return;
  if (danmakuLoading === target) {
    danmakuReloadRequested = true;
    return;
  }
  danmakuLoading = target;
  try {
    target.config({ danmuku: filteredDanmaku() });
    await target.load();
  } catch (e) {
    if (target === plugin()) note.value = `弹幕暂时不可用：${errorText(e)}`;
  } finally {
    if (danmakuLoading === target) {
      danmakuLoading = null;
      if (target === plugin()) applyPreferences();
      if (danmakuReloadRequested) {
        danmakuReloadRequested = false;
        void reloadDanmaku();
      }
    }
  }
}
watch(
  () => [prefs.density, prefs.offset],
  () => {
    clearTimeout(danmakuReload);
    danmakuReload = setTimeout(() => void reloadDanmaku(), 150);
  },
);
function openPanel(mode: 'settings' | 'rate') {
  returnFocus.value =
    document.activeElement instanceof HTMLElement
      ? document.activeElement
      : controlButtons?.settings || null;
  panelMode.value = mode;
  settingsOpen.value = true;
}
function openSettings() {
  openPanel('settings');
}
function closeSettings() {
  settingsOpen.value = false;
}
function fullscreenChanged() {
  fullscreenActive.value = !!(art?.fullscreen || art?.fullscreenWeb);
  syncPlayerStyles();
  applyPreferences();
}
function screenChanged() {
  smallScreen.value = !!screenQuery?.matches;
}
watch([settingsOpen, panelMode], ([open, mode]) => {
  controlButtons?.settings.setAttribute('aria-expanded', String(open && mode === 'settings'));
  controlButtons?.rate.setAttribute('aria-expanded', String(open && mode === 'rate'));
  if (art) art.controls.show = true;
});
function setRate(value: number) {
  playbackRate.value = value;
  if (art) art.playbackRate = value;
}
watch(playbackRate, (value) => {
  if (!controlButtons) return;
  controlButtons.rate.textContent = `${value}×`;
  controlButtons.rate.setAttribute('aria-label', `播放速度 ${value} 倍`);
});
function setVolume(value: number) {
  userVolume.value = clamp(value, 0, 1);
  if (art) art.muted = false;
  applyVolume();
}
function setFit(value: 'contain' | 'cover') {
  fit.value = value;
  if (art) art.video.style.objectFit = value;
}
function resetSettings(tab: 'playback' | 'danmaku') {
  if (tab === 'danmaku') {
    Object.assign(prefs, defaultPreferences(display.default_danmaku));
    return;
  }
  volumeBalance.value = false;
  setVolume(0.7);
  setRate(1);
  setFit('contain');
  subtitle.value = '';
  selectSubtitle();
  routeId.value = '';
  variantId.value = '';
  routeRecovery.reset();
  playbackProtocol.value = 'auto';
  protocolFallback.reset();
  void renew();
}
async function saveProgress() {
  if (!session.user || !art || !activePartId || !Number.isFinite(art.duration) || art.duration <= 0)
    return;
  const partId = activePartId;
  const player = art;
  const key = request;
  try {
    await write(
      `/progress/${partId}`,
      { position: art.currentTime, duration: art.duration },
      'PUT',
    );
    if (key === request && player === art) progressError.value = '';
  } catch (e) {
    if (key === request && player === art) progressError.value = `观看进度未保存：${errorText(e)}`;
  }
}
function applyVolume() {
  if (!art) return;
  const loudness = playback.value?.loudness;
  const gain =
    volumeBalance.value && loudness?.status === 'ready' && !loudness.atmos_bypass
      ? Math.max(0, Math.min(1, loudness.gain_linear))
      : 1;
  expectedVolume = Math.max(0, Math.min(1, userVolume.value * gain));
  art.volume = expectedVolume;
}
watch(volumeBalance, applyVolume);
function handleMediaFailure(kind: 'network' | 'media', message: string) {
  if (rejectMediaLoad) {
    rejectMediaLoad(new MediaLoadError(kind, message));
    return;
  }
  if (renewing) return;
  if (kind === 'media') {
    const fallback = protocolFallback.claim(
      activePartId,
      playback.value,
      routeId.value || undefined,
    );
    if (fallback) void renew(fallback);
    else error.value = message;
    return;
  }
  const recovery = routeRecovery.claim(
    activePartId,
    variantId.value,
    routeId.value,
    playback.value,
  );
  if (recovery) void renew(recovery);
  else error.value = '当前播放连接暂时不可用，已自动尝试恢复。可手动重试或切换节点。';
}
function clearBuffering() {
  mediaLoading.value = false;
  waitingSince = 0;
}
function checkBufferingRoute() {
  if (!art || !waitingSince || busy.value || renewing || error.value || routeId.value) return;
  const video = art.video;
  if (video.paused && !video.seeking && !startupPlayRequested) {
    clearBuffering();
    return;
  }
  if (
    !playback.value?.routes?.some(
      (route) => route.id !== playback.value?.selected_route_id && route.status !== 'unavailable',
    )
  )
    return;
  if (
    !shouldRecoverStall({
      elapsedMs: performance.now() - waitingSince,
      paused: video.paused,
      readyState: video.readyState,
      bufferedSeconds: bufferedAhead(video),
      bytesPerSecond: runtimeStats.value.transfer.bytesPerSecond,
      activeRequests: runtimeStats.value.transfer.activeRequests,
      transferState: runtimeStats.value.transfer.state,
      bitrateBps:
        currentMedia.value.total_bitrate_bps ||
        (currentMedia.value.video_bitrate_bps || 0) + (currentMedia.value.audio_bitrate_bps || 0),
      playbackRate: video.playbackRate,
    })
  )
    return;
  const recovery = routeRecovery.claim(activePartId, variantId.value, '', playback.value);
  if (recovery) void renew(recovery);
}
function switchMedia(player: Artplayer, url: string) {
  return new Promise<void>((resolve, reject) => {
    const video = player.video;
    let settled = false;
    const ready = () => finish();
    const failed = () =>
      finish(
        new MediaLoadError(mediaFailureKind(video.error?.code), '当前浏览器无法播放此归档版本'),
      );
    const cancel = (reason: Error) => finish(reason);
    const began = performance.now();
    let progressed = began,
      previousBytes = -1,
      previousBufferedEnd = 0,
      previousReady = 0;
    const timeout = setInterval(() => {
      const at = performance.now(),
        bytes = runtimeStats.value.transfer.transferredBytes;
      let end = 0;
      try {
        if (video.buffered.length) end = video.buffered.end(video.buffered.length - 1);
      } catch {
        /* Source replacement can invalidate a TimeRanges snapshot. */
      }
      if (
        bytes !== previousBytes ||
        end > previousBufferedEnd + 0.01 ||
        video.readyState > previousReady
      )
        progressed = at;
      previousBytes = bytes;
      previousBufferedEnd = end;
      previousReady = video.readyState;
      if (at - progressed >= 25000 || at - began >= 120000)
        finish(new MediaLoadError('network', '播放加载超时，请重试或选择其他节点'));
    }, 1000);
    function finish(reason?: Error) {
      if (settled) return;
      settled = true;
      clearInterval(timeout);
      video.removeEventListener('canplay', ready);
      video.removeEventListener('error', failed);
      if (rejectMediaLoad === cancel) rejectMediaLoad = null;
      if (reason) reject(reason);
      else resolve();
    }
    rejectMediaLoad = cancel;
    video.addEventListener('canplay', ready, { once: true });
    video.addEventListener('error', failed, { once: true });
    player.pause();
    player.url = url;
  });
}
function selectRoute(value: string) {
  routeId.value = value;
  routeRecovery.reset();
  void renew();
}
function selectVariant(value: string) {
  variantId.value = value;
  routeRecovery.reset();
  void renew();
}
function selectProtocol(value: 'auto' | 'file') {
  playbackProtocol.value = value;
  protocolFallback.reset();
  routeRecovery.reset();
  void renew();
}
async function renew(input?: PlaybackInput) {
  if (!art) return;
  const operation = ++renewal;
  renewalController?.abort();
  rejectMediaLoad?.(new Error('播放选择已切换'));
  const controller = new AbortController();
  renewalController = controller;
  const snapshot = renewalSnapshot || {
    ...recoveryStartState(art.currentTime, art.video.paused, startupPending, startupPlayRequested),
    rate: art.playbackRate,
    volume: userVolume.value,
    muted: art.muted,
  };
  renewalSnapshot = snapshot;
  renewing = true;
  switching.value = true;
  mediaLoading.value = true;
  waitingSince = 0;
  const currentPlayer = art,
    key = request,
    id = activePartId;
  const resumeAtStart = initialProgress;
  const current = () => key === request && operation === renewal && currentPlayer === art;
  error.value = '';
  try {
    const data = await loadWithProtocolFallback(
      withProtocolPreference(
        input || {
          part_id: id,
          variant_id: variantId.value || undefined,
          route_id: routeId.value || undefined,
        },
        playbackProtocol.value,
      ),
      protocolFallback,
      {
        current,
        create: (selection) =>
          createPlayback(
            selection,
            controller.signal,
            (value) => {
              if (current()) probing.value = value;
            },
            false,
          ),
        attach: async (value) => {
          if (typeof value.url !== 'string' || !value.url)
            throw new Error('服务端未返回有效播放地址');
          playback.value = value;
          variantId.value = value.source_variant_id || value.variant_id;
          await switchMedia(currentPlayer, value.url);
        },
        restore: async () => {
          if (startupPending && snapshot.position < 1 && resumeAtStart) {
            const saved = await resumeAtStart.ready;
            if (current()) snapshot.position = validResumePosition(saved, currentPlayer.duration);
          }
          if (!current()) return;
          startupPending = false;
          currentPlayer.currentTime = snapshot.position;
          currentPlayer.playbackRate = snapshot.rate;
          userVolume.value = snapshot.volume;
          applyVolume();
          currentPlayer.muted = snapshot.muted;
          currentPlayer.template.$player.classList.remove('art-error');
          currentPlayer.notice.show = '';
          if (snapshot.paused) currentPlayer.pause();
          else {
            try {
              await currentPlayer.play();
            } catch {
              if (current()) note.value = '浏览器暂未允许自动播放，请点击播放继续。';
            }
          }
        },
      },
    );
    if (!data || !current()) return;
  } catch (e) {
    if (current()) {
      const recovery =
        e instanceof MediaLoadError && e.kind === 'network'
          ? routeRecovery.claim(id, variantId.value, routeId.value, playback.value)
          : null;
      if (recovery) {
        void renew(recovery);
        return;
      }
      error.value = `无法恢复播放：${errorText(e)}`;
    }
  } finally {
    if (current()) {
      renewing = false;
      switching.value = false;
      clearBuffering();
      renewalSnapshot = null;
    }
  }
}
async function setup() {
  const key = ++request;
  renewal++;
  renewalController?.abort();
  renewalSnapshot = null;
  protocolFallback.reset();
  const endKey = endGuard.reset();
  const partAtStart = props.part.id;
  const shouldStart = props.autoStart;
  const shouldResume = props.resume !== false;
  startupPending = true;
  startupPlayRequested = !!shouldStart;
  emit('playing', false);
  playbackController.abort();
  playbackController = new AbortController();
  initialProgress?.cancel();
  const resumeAtStart = preloadProgress(
    (signal) => api<{ position: number }>(`/progress/${partAtStart}`, { signal }),
    playbackController.signal,
    { enabled: !!session.user && shouldResume },
  );
  initialProgress = resumeAtStart;
  rejectMediaLoad?.(new Error('播放内容已切换'));
  busy.value = true;
  mediaLoading.value = true;
  waitingSince = 0;
  error.value = '';
  note.value = '';
  progressError.value = '';
  dmCount.value = null;
  subtitle.value = '';
  playback.value = null;
  routeRecovery.reset();
  renewing = false;
  switching.value = false;
  runtimeStats.value = emptyRuntimeStats();
  void saveProgress();
  if (key !== request) return;
  closeSettings();
  fullscreenActive.value = false;
  playerHost.value = null;
  await nextTick();
  if (key !== request) return;
  resize?.disconnect();
  mediaAdapter.destroy();
  art?.destroy(false);
  art = null;
  controlButtons = null;
  rawDanmaku = [];
  danmakuReloadRequested = false;
  clearTimeout(danmakuReload);
  try {
    const [data, [{ default: Player }, { default: artplayerPluginDanmuku }]] = await Promise.all([
      createPlayback(
        {
          part_id: partAtStart,
          variant_id: variantId.value || undefined,
          route_id: routeId.value || undefined,
          protocol: playbackProtocol.value,
        },
        playbackController.signal,
        (value) => {
          if (key === request) probing.value = value;
        },
      ),
      loadPlayerEngines(),
    ]);
    if (key !== request) return;
    if (typeof data.url !== 'string' || !data.url) throw new Error('此分 P 尚无有效的播放地址');
    playback.value = data;
    variantId.value = data.source_variant_id || data.variant_id || '';
    await nextTick();
    if (key !== request) return;
    if (!container.value) return;
    activePartId = partAtStart;
    // Renewal is bounded here, so disable the player's separate reconnect loop.
    Player.RECONNECT_TIME_MAX = 0;
    art = new Player({
      container: container.value,
      url: data.url,
      type: 'treasure',
      customType: {
        treasure: (video, url) => {
          void mediaAdapter.attach(
            video,
            url,
            playback.value?.protocol,
            playback.value?.media as MeasuredMedia | undefined,
            validResumePosition(
              renewalSnapshot?.position ?? (startupPending ? resumeAtStart.peek() : 0),
              (playback.value?.media as MeasuredMedia | undefined)?.duration_seconds ||
                props.part.duration,
            ),
          );
        },
      },
      poster: props.poster || '',
      theme: '#00a1d6',
      lang: 'zh-cn',
      volume: userVolume.value,
      autoplay: false,
      autoSize: false,
      fullscreen: canUseElementFullscreen(container.value, window),
      fullscreenWeb: true,
      pip: true,
      playbackRate: false,
      setting: false,
      hotkey: false,
      mutex: true,
      playsInline: true,
      miniProgressBar: true,
      plugins: [
        artplayerPluginDanmuku({
          danmuku: filteredDanmaku(),
          mount: document.createElement('div'),
          emitter: false,
          visible: prefs.visible,
          fontSize: prefs.fontSize,
          antiOverlap: prefs.antiOverlap,
        }),
      ],
    });
    playerHost.value = art.template.$player;
    setFit(fit.value);
    controlButtons = installPlayerControls(art, {
      openSettings,
      openRates: () => openPanel('rate'),
      toggleDanmaku: () => (prefs.visible = !prefs.visible),
      setVolume,
      getVolume: () => userVolume.value,
    });
    controlButtons.rate.textContent = `${playbackRate.value}×`;
    controlButtons.rate.setAttribute('aria-label', `播放速度 ${playbackRate.value} 倍`);
    applyVolume();
    art.on('video:volumechange', () => {
      if (!art || Math.abs(art.video.volume - expectedVolume) < 0.0001) return;
      userVolume.value = art.video.volume;
      applyVolume();
    });
    syncPlayerStyles();
    resize?.observe(art.template.$player);
    art.on('fullscreen', fullscreenChanged);
    art.on('fullscreenWeb', fullscreenChanged);
    art.on('video:ratechange', () => {
      if (art) playbackRate.value = art.playbackRate;
    });
    art.on('ready', async () => {
      if (key !== request || !art || renewing) return;
      const readyRenewal = renewal;
      const player = art;
      applyPreferences();
      setRate(playbackRate.value);
      if (session.user && shouldResume) {
        const saved = await resumeAtStart.ready;
        const position = validResumePosition(saved, player.duration);
        if (
          key === request &&
          readyRenewal === renewal &&
          !renewing &&
          player === art &&
          player.video.paused &&
          player.currentTime < 1 &&
          position > 0
        )
          player.currentTime = position;
      }
      if (key !== request || readyRenewal !== renewal || player !== art || renewing) return;
      startupPending = false;
      if (startupPlayRequested) {
        try {
          await player.play();
        } catch {
          if (key === request) note.value = '浏览器暂未允许自动播放，请点击播放继续。';
        }
      }
    });
    art.on('video:ended', () => {
      if (key === request && art && endGuard.consume(endKey, art.video.ended)) {
        emit('playing', false);
        void saveProgress();
        emit('ended', partAtStart);
      }
    });
    art.on('video:playing', () => {
      if (key === request) {
        clearBuffering();
        emit('playing', true);
      }
    });
    art.on('video:canplay', () => {
      if (key === request) clearBuffering();
    });
    art.on('video:loadeddata', () => {
      if (key === request && art?.video.paused && !startupPlayRequested && !renewing)
        clearBuffering();
    });
    art.on('video:loadstart', () => {
      if (key === request) mediaLoading.value = true;
    });
    art.on('video:seeking', () => {
      if (key === request) {
        mediaLoading.value = true;
        waitingSince = performance.now();
      }
    });
    art.on('video:seeked', () => {
      if (key === request && art && art.video.readyState >= 3) clearBuffering();
    });
    art.on('video:play', () => {
      if (key === request && !renewing) {
        startupPlayRequested = true;
        if (art && art.video.readyState < 3) {
          mediaLoading.value = true;
          waitingSince ||= performance.now();
        }
        urlRenewal.check();
      }
    });
    art.on('video:waiting', () => {
      if (key === request) {
        if (art?.video.paused && !renewing && !startupPlayRequested) return;
        mediaLoading.value = true;
        waitingSince ||= performance.now();
        mediaAdapter.onWaiting();
        emit('playing', false);
      }
    });
    art.on('video:pause', () => {
      if (key !== request) return;
      if (!renewing) startupPlayRequested = false;
      if (!renewing) clearBuffering();
      emit('playing', false);
      void saveProgress();
    });
    art.on('video:timeupdate', () => {
      if (key !== request || !art) return;
      urlRenewal.check();
      endGuard.rearm(endKey, art.currentTime, art.duration, art.video.ended);
      if (Date.now() - lastSaved > 15000) {
        lastSaved = Date.now();
        void saveProgress();
      }
    });
    art.on('video:error', () => {
      if (key !== request) return;
      clearBuffering();
      emit('playing', false);
      handleMediaFailure(
        mediaFailureKind(art?.video.error?.code),
        '当前浏览器无法解码此归档版本，可手动选择已保存的兼容版本。',
      );
    });
    const danmakuPlayer = art;
    const signal = playbackController.signal;
    void loadDanmaku(
      () => api<Danmaku[]>(`/parts/${partAtStart}/danmaku`, { signal }),
      () => key === request && danmakuPlayer === art && !signal.aborted,
      async (rows) => {
        rawDanmaku = rows;
        dmCount.value = rows.length;
        await reloadDanmaku();
      },
      (reason) => (note.value = `弹幕暂时不可用：${errorText(reason)}`),
    );
  } catch (e) {
    if (key === request) error.value = errorText(e);
  } finally {
    if (key === request) {
      busy.value = false;
      if (error.value) clearBuffering();
    }
  }
}
async function replay() {
  const player = art;
  if (!player) return;
  player.currentTime = 0;
  try {
    await player.play();
  } catch {
    if (art === player) note.value = '请点击播放继续。';
  }
}
defineExpose({
  replay,
  selectRoute,
  isEnded: (partId: string) => activePartId === partId && !!art?.video.ended,
});
function selectSubtitle() {
  if (!art) return;
  if (!subtitle.value) {
    art.subtitle.show = false;
    return;
  }
  art.subtitle.switch(subtitle.value, { type: 'vtt', escape: true });
  art.subtitle.show = true;
}
watch(
  () => props.part.id,
  () => {
    variantId.value = '';
    routeId.value = '';
    void setup();
  },
);
onMounted(() => {
  stallTimer = setInterval(checkBufferingRoute, 1000);
  document.addEventListener('visibilitychange', refreshVisiblePlayback);
  screenQuery = window.matchMedia('(max-width: 700px)');
  screenQuery.addEventListener('change', screenChanged);
  screenChanged();
  void setup();
  resize = new ResizeObserver(() => {
    applyPreferences();
  });
});
onBeforeUnmount(() => {
  clearInterval(stallTimer);
  urlRenewal.stop();
  document.removeEventListener('visibilitychange', refreshVisiblePlayback);
  emit('playing', false);
  screenQuery?.removeEventListener('change', screenChanged);
  closeSettings();
  request++;
  clearTimeout(danmakuReload);
  playbackController.abort();
  initialProgress?.cancel();
  renewalController?.abort();
  rejectMediaLoad?.(new Error('播放器已关闭'));
  mediaAdapter.destroy();
  void saveProgress();
  resize?.disconnect();
  art?.destroy(false);
  art = null;
});
</script>
<template>
  <section class="archive-player" aria-label="视频播放">
    <div ref="container" class="player-stage"></div>
    <Teleport :to="playerHost || 'body'" :disabled="!playerHost">
      <LoadingTransfer
        v-if="!error && (busy || switching || mediaLoading)"
        :phase="switching ? 'switching' : busy ? 'preparing' : 'buffering'"
        :transfer="busy ? null : runtimeStats.transfer"
      />
      <div v-if="error" class="player-error-overlay" role="alert" @click.stop @keydown.stop>
        <span>{{ error }}</span>
        <div>
          <button @click="art ? renew() : setup()">重试播放</button
          ><button
            v-if="audioAlternative"
            :disabled="busy || switching"
            @click="selectVariant(audioAlternative.id)"
          >
            使用 AAC 声音兼容版</button
          ><button v-if="playback" @click="openSettings">播放设置</button>
        </div>
      </div>
      <div
        v-if="(note || progressError) && !settingsOpen"
        class="player-warning"
        role="status"
        @click.stop
      >
        <span>{{ note || progressError }}</span
        ><button
          aria-label="关闭提示"
          @click="
            note = '';
            progressError = '';
          "
        >
          ×
        </button>
      </div>
      <Teleport to="body" :disabled="!sheet">
        <PlayerSettingsPanel
          v-if="playerHost && settingsOpen"
          :sheet="sheet"
          :rate-only="panelMode === 'rate'"
          :return-focus="returnFocus"
          :busy="switching"
          @close="closeSettings"
          @reset="resetSettings"
        >
          <template #rate>
            <PlayerChoice
              label="播放速度"
              :model-value="playbackRate"
              :choices="rateChoices"
              @update:model-value="
                setRate(Number($event));
                closeSettings();
              "
            />
          </template>
          <template #playback
            ><PlaybackOptions
              :playback="playback"
              :variants="playableVariants"
              :variant-id="variantId"
              :subtitle="subtitle"
              :route-id="routeId"
              :protocol="playbackProtocol"
              :balance="volumeBalance"
              :busy="busy || switching"
              :rate="playbackRate"
              :volume="userVolume"
              :fit="fit"
              :media-properties="mediaProperties"
              :runtime-stats="runtimeStats"
              @variant="selectVariant"
              @route="selectRoute"
              @protocol="selectProtocol"
              @balance="volumeBalance = $event"
              @subtitle="
                subtitle = $event;
                selectSubtitle();
              "
              @rate="setRate"
              @volume="setVolume"
              @fit="setFit"
          /></template>
          <template #danmaku><DanmakuSettings :model-value="prefs" :count="dmCount" /></template>
        </PlayerSettingsPanel>
      </Teleport>
    </Teleport>
    <AudioCompatibilityNotice
      v-if="!settingsOpen"
      :media="currentMedia"
      :support="runtimeStats.audioSupport.ec3"
      :alternative-id="audioAlternative?.id"
      :busy="busy || switching"
      @variant="selectVariant"
    />
  </section>
</template>

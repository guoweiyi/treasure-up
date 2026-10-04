<script setup lang="ts">
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue';
import Artplayer from 'artplayer';
import artplayerPluginDanmuku, {
  type Result as DanmakuPlugin,
  type Mode,
} from 'artplayer-plugin-danmuku';
import { api, write, errorText, display, session } from '../api';
import type { Part, Playback, Danmaku, MediaProperties } from '../types';
import { createMediaAdapter } from '../player/mediaAdapter';
import { emptyRuntimeStats } from '../player/runtimeStats';
import { createEndGuard } from '../player/queue';
import { createPlayback } from '../player/routing';
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
}>();
const runtimeStats = ref(emptyRuntimeStats());
const endGuard = createEndGuard();
const container = ref<HTMLDivElement>(),
  error = ref(''),
  note = ref(''),
  progressError = ref(''),
  busy = ref(false),
  variantId = ref(''),
  subtitle = ref(''),
  playback = ref<Playback | null>(null),
  dmCount = ref<number | null>(null);
const routeId = ref(''),
  volumeBalance = ref(false),
  probing = ref(false),
  switching = ref(false);
const playableVariants = computed(() =>
  props.part.variants.filter((variant) => variant.kind !== 'hls'),
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
let danmakuLoading = false,
  danmakuReloadRequested = false;
let rejectMediaLoad: ((error: Error) => void) | null = null;
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
  recoveries = 0,
  renewing = false,
  activePartId = '';
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
  if (danmakuLoading) {
    danmakuReloadRequested = true;
    return;
  }
  const target = plugin();
  if (!target) return;
  danmakuLoading = true;
  try {
    target.config({ danmuku: filteredDanmaku() });
    await target.load();
  } catch (e) {
    note.value = `弹幕暂时不可用：${errorText(e)}`;
  } finally {
    danmakuLoading = false;
    if (target === plugin()) applyPreferences();
    if (danmakuReloadRequested) {
      danmakuReloadRequested = false;
      void reloadDanmaku();
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
  void renew();
}
async function saveProgress() {
  if (!session.user || !art || !activePartId || !Number.isFinite(art.duration) || art.duration <= 0)
    return;
  const partId = activePartId;
  try {
    await write(
      `/progress/${partId}`,
      { position: art.currentTime, duration: art.duration },
      'PUT',
    );
    progressError.value = '';
  } catch (e) {
    progressError.value = `观看进度未保存：${errorText(e)}`;
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
    rejectMediaLoad(new Error(message));
    return;
  }
  if (kind === 'media') {
    error.value = message;
    return;
  }
  if (renewing) return;
  if (recoveries === 0) {
    recoveries = 1;
    void renew();
  } else error.value = '当前节点暂时无法播放，已更新过一次地址。可手动重试或切换节点。';
}
function switchMedia(player: Artplayer, url: string) {
  return new Promise<void>((resolve, reject) => {
    const video = player.video;
    const ready = () => finish();
    const failed = () => finish(new Error('当前浏览器无法播放此归档版本'));
    const cancel = (reason: Error) => finish(reason);
    const timeout = setTimeout(
      () => finish(new Error('播放加载超时，请重试或选择其他节点')),
      20000,
    );
    function finish(reason?: Error) {
      clearTimeout(timeout);
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
  recoveries = 0;
  void renew();
}
function selectVariant(value: string) {
  variantId.value = value;
  recoveries = 0;
  void renew();
}
async function renew() {
  if (renewing || !art) return;
  renewing = true;
  switching.value = true;
  const currentPlayer = art,
    key = request,
    id = activePartId;
  const snapshot = {
    position: art.currentTime,
    paused: art.video.paused,
    rate: art.playbackRate,
    volume: userVolume.value,
    muted: art.muted,
  };
  error.value = '';
  try {
    const data = await createPlayback(
      {
        part_id: id,
        variant_id: variantId.value || undefined,
        route_id: routeId.value || undefined,
      },
      playbackController.signal,
      (value) => (probing.value = value),
      false,
    );
    if (key !== request || currentPlayer !== art) return;
    if (typeof data.url !== 'string' || !data.url) throw new Error('服务端未返回有效播放地址');
    playback.value = data;
    variantId.value = data.source_variant_id || data.variant_id;
    await switchMedia(currentPlayer, data.url);
    if (key !== request || currentPlayer !== art) return;
    art.currentTime = snapshot.position;
    art.playbackRate = snapshot.rate;
    userVolume.value = snapshot.volume;
    applyVolume();
    art.muted = snapshot.muted;
    art.template.$player.classList.remove('art-error');
    art.notice.show = '';
    if (snapshot.paused) art.pause();
    else await art.play();
  } catch (e) {
    if (key === request) error.value = `无法恢复播放：${errorText(e)}`;
  } finally {
    if (key === request && currentPlayer === art) {
      renewing = false;
      switching.value = false;
    }
  }
}
async function setup() {
  const key = ++request;
  const endKey = endGuard.reset();
  const partAtStart = props.part.id;
  const shouldStart = props.autoStart;
  const shouldResume = props.resume !== false;
  emit('playing', false);
  playbackController.abort();
  playbackController = new AbortController();
  rejectMediaLoad?.(new Error('播放内容已切换'));
  busy.value = true;
  error.value = '';
  note.value = '';
  progressError.value = '';
  dmCount.value = null;
  subtitle.value = '';
  playback.value = null;
  recoveries = 0;
  renewing = false;
  switching.value = false;
  runtimeStats.value = emptyRuntimeStats();
  await saveProgress();
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
  try {
    const data = await createPlayback(
      {
        part_id: partAtStart,
        variant_id: variantId.value || undefined,
        route_id: routeId.value || undefined,
      },
      playbackController.signal,
      (value) => {
        if (key === request) probing.value = value;
      },
    );
    if (key !== request) return;
    if (typeof data.url !== 'string' || !data.url) throw new Error('此分 P 尚无有效的播放地址');
    playback.value = data;
    variantId.value = data.source_variant_id || data.variant_id || '';
    let danmaku: Danmaku[] = [];
    try {
      danmaku = await api<Danmaku[]>(`/parts/${partAtStart}/danmaku`, {
        signal: playbackController.signal,
      });
      if (key !== request) return;
      dmCount.value = danmaku.length;
    } catch (e) {
      note.value = `弹幕暂时不可用：${errorText(e)}`;
    }
    if (key !== request) return;
    await nextTick();
    if (!container.value) return;
    rawDanmaku = danmaku
      .filter((d) => Number.isFinite(d.time) && [0, 1, 2].includes(d.mode))
      .map((d) => ({
        text: String(d.text ?? ''),
        time: d.time,
        mode: d.mode as Mode,
        color:
          typeof d.color === 'number' && Number.isFinite(d.color)
            ? `#${Math.max(0, Math.min(0xffffff, Math.round(d.color)))
                .toString(16)
                .padStart(6, '0')}`
            : typeof d.color === 'string' && /^#(?:[\da-f]{3}|[\da-f]{6})$/i.test(d.color)
              ? d.color
              : '#ffffff',
      }));
    activePartId = partAtStart;
    // Renewal is bounded here, so disable the player's separate reconnect loop.
    Artplayer.RECONNECT_TIME_MAX = 0;
    art = new Artplayer({
      container: container.value,
      url: data.url,
      type: 'treasure',
      customType: {
        treasure: (video, url) => {
          void mediaAdapter.attach(video, url, playback.value?.protocol);
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
      if (key !== request || !art) return;
      const player = art;
      applyPreferences();
      setRate(playbackRate.value);
      if (session.user && shouldResume) {
        try {
          const progress = await api<{ position: number }>(`/progress/${partAtStart}`, {
            signal: playbackController.signal,
          });
          if (
            key === request &&
            player === art &&
            player.video.paused &&
            player.currentTime < 1 &&
            progress.position > 0 &&
            progress.position < player.duration - 3
          )
            player.currentTime = progress.position;
        } catch {
          /* Missing progress does not block playback. */
        }
      }
      if (shouldStart && key === request && player === art) {
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
      if (key === request) emit('playing', true);
    });
    art.on('video:waiting', () => {
      if (key === request) emit('playing', false);
    });
    art.on('video:pause', () => {
      if (key !== request) return;
      emit('playing', false);
      void saveProgress();
    });
    art.on('video:timeupdate', () => {
      if (key !== request || !art) return;
      endGuard.rearm(endKey, art.currentTime, art.duration, art.video.ended);
      if (Date.now() - lastSaved > 15000) {
        lastSaved = Date.now();
        void saveProgress();
      }
    });
    art.on('video:error', () => {
      if (key !== request) return;
      emit('playing', false);
      handleMediaFailure(
        art?.video.error?.code === 3 ? 'media' : 'network',
        '当前浏览器无法解码此归档版本，可手动选择已保存的兼容版本。',
      );
    });
  } catch (e) {
    if (key === request) error.value = errorText(e);
  } finally {
    if (key === request) busy.value = false;
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
  screenQuery = window.matchMedia('(max-width: 700px)');
  screenQuery.addEventListener('change', screenChanged);
  screenChanged();
  void setup();
  resize = new ResizeObserver(() => {
    applyPreferences();
  });
});
onBeforeUnmount(() => {
  emit('playing', false);
  screenQuery?.removeEventListener('change', screenChanged);
  closeSettings();
  request++;
  clearTimeout(danmakuReload);
  playbackController.abort();
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
      <div v-if="busy || switching" class="player-loading" role="status">
        {{ switching ? '正在切换播放…' : '正在准备播放…' }}
      </div>
      <div v-if="error" class="player-error-overlay" role="alert" @click.stop @keydown.stop>
        <span>{{ error }}</span>
        <div>
          <button @click="art ? renew() : setup()">重试播放</button
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
              :balance="volumeBalance"
              :busy="busy || switching"
              :rate="playbackRate"
              :volume="userVolume"
              :fit="fit"
              :media-properties="mediaProperties"
              :runtime-stats="runtimeStats"
              @variant="selectVariant"
              @route="selectRoute"
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
  </section>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue';
import Artplayer from 'artplayer';
import artplayerPluginDanmuku, {
  type Result as DanmakuPlugin,
  type Option as DanmakuOption,
  type Mode,
} from 'artplayer-plugin-danmuku';
import { api, write, errorText, display } from '../api';
import type { Part, Playback, Danmaku } from '../types';
const props = defineProps<{ part: Part; poster?: string | null }>();
const container = ref<HTMLDivElement>(),
  error = ref(''),
  note = ref(''),
  progressError = ref(''),
  busy = ref(false),
  variantId = ref(''),
  subtitle = ref(''),
  playback = ref<Playback | null>(null),
  dmCount = ref<number | null>(null);
const fullscreenHost = ref<HTMLElement | null>(null),
  fullscreenActive = ref(false),
  settingsOpen = ref(false),
  settingsElement = ref<HTMLDetailsElement>();
const defaults = {
  visible: display.default_danmaku,
  opacity: 100,
  fontSize: 25,
  area: 75,
  speed: 5,
  fontFamily: 'Microsoft YaHei',
  customFont: '',
  weight: 400,
  spacing: 0,
  outline: 'stroke',
  strokeWidth: 1,
  strokeColor: '#000000',
  uniform: false,
  color: '#ffffff',
  rolling: true,
  top: true,
  bottom: true,
  colored: true,
  antiOverlap: true,
  synchronousPlayback: false,
  scaleWithScreen: false,
  subtitleSafe: true,
};
const prefs = reactive({ ...defaults });
try {
  const saved = JSON.parse(localStorage.getItem('treasure-up:danmaku:v1') || '{}');
  for (const k of Object.keys(defaults) as (keyof typeof defaults)[])
    if (typeof saved[k] === typeof defaults[k]) (prefs as Record<string, unknown>)[k] = saved[k];
} catch {
  /* Browser storage is optional. */
}
const clamp = (n: number, min: number, max: number) =>
  Math.min(max, Math.max(min, Number(n) || min));
let art: Artplayer | null = null,
  resize: ResizeObserver | null = null,
  request = 0,
  lastSaved = 0,
  recoveries = 0,
  renewing = false,
  activePartId = '',
  applyingPreferences = false;
const family = computed(() =>
  prefs.fontFamily === 'custom' ? prefs.customFont || 'sans-serif' : prefs.fontFamily,
);
const shadow = computed(() => {
  const s = clamp(prefs.strokeWidth, 0, 3),
    c = /^#[\da-f]{6}$/i.test(prefs.strokeColor) ? prefs.strokeColor : '#000000';
  return prefs.outline === 'none'
    ? 'none'
    : prefs.outline === 'shadow'
      ? `${s + 1}px ${s + 1}px 2px ${c}`
      : `${s}px 0 ${prefs.outline === 'heavy' ? 2 : 0}px ${c}, -${s}px 0 0 ${c}, 0 ${s}px 0 ${c}, 0 -${s}px 0 ${c}`;
});
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
  applyingPreferences = true;
  plugin()?.config({
    danmuku: plugin()?.option.danmuku || [],
    visible: prefs.visible,
    opacity: clamp(prefs.opacity, 0, 100) / 100,
    fontSize: size,
    speed: clamp(prefs.speed, 1, 10),
    margin: [10, `${Math.max(100 - clamp(prefs.area, 25, 100), prefs.subtitleSafe ? 12 : 0)}%`],
    modes,
    antiOverlap: prefs.antiOverlap,
    synchronousPlayback: prefs.synchronousPlayback,
    beforeVisible: (dm) =>
      prefs.colored || ['#ffffff', '#fff', 'white'].includes(String(dm.color).toLowerCase()),
  });
  plugin()?.reset();
  applyingPreferences = false;
}
watch(
  prefs,
  () => {
    try {
      localStorage.setItem('treasure-up:danmaku:v1', JSON.stringify(prefs));
    } catch {
      /* Playback remains available without storage. */
    }
    applyPreferences();
  },
  { deep: true },
);
async function saveProgress() {
  if (!art || !activePartId || !Number.isFinite(art.duration) || art.duration <= 0) return;
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
async function renew() {
  if (renewing || !art) return;
  renewing = true;
  const currentPlayer = art,
    key = request,
    id = activePartId;
  const snapshot = {
    position: art.currentTime,
    paused: art.video.paused,
    rate: art.playbackRate,
    volume: art.volume,
    muted: art.muted,
  };
  error.value = '';
  try {
    const data = await write<Playback>('/playback-sessions', {
      part_id: id,
      variant_id: variantId.value || undefined,
    });
    if (key !== request || currentPlayer !== art) return;
    if (typeof data.url !== 'string' || !data.url) throw new Error('服务端未返回有效播放地址');
    playback.value = data;
    await art.switchQuality(data.url);
    if (key !== request || currentPlayer !== art) return;
    art.currentTime = snapshot.position;
    art.playbackRate = snapshot.rate;
    art.volume = snapshot.volume;
    art.muted = snapshot.muted;
    art.template.$player.classList.remove('art-error');
    art.notice.show = '';
    if (snapshot.paused) art.pause();
    else await art.play();
  } catch (e) {
    if (key === request) error.value = `无法恢复播放：${errorText(e)}`;
  } finally {
    renewing = false;
  }
}
function syncFullscreen() {
  fullscreenActive.value = art?.state === 'fullscreen' || art?.state === 'fullscreenWeb';
  // Web fullscreen restores an earlier cssText on exit; reapply current preferences.
  syncPlayerStyles();
}
function settingsToggled(event: Event) {
  settingsOpen.value = (event.target as HTMLDetailsElement).open;
}
async function setup() {
  const key = ++request;
  busy.value = true;
  error.value = '';
  note.value = '';
  progressError.value = '';
  dmCount.value = null;
  subtitle.value = '';
  playback.value = null;
  recoveries = 0;
  await saveProgress();
  if (key !== request) return;
  fullscreenActive.value = false;
  await nextTick();
  if (key !== request) return;
  resize?.disconnect();
  art?.destroy(false);
  art = null;
  fullscreenHost.value = null;
  try {
    const data = await write<Playback>('/playback-sessions', {
      part_id: props.part.id,
      variant_id: variantId.value || undefined,
    });
    if (key !== request) return;
    if (typeof data.url !== 'string' || !data.url) throw new Error('此分 P 尚无有效的播放地址');
    playback.value = data;
    variantId.value = data.variant_id || '';
    let danmaku: Danmaku[] = [];
    try {
      danmaku = await api<Danmaku[]>(`/parts/${props.part.id}/danmaku`);
      if (key !== request) return;
      dmCount.value = danmaku.length;
    } catch (e) {
      note.value = `弹幕暂时不可用：${errorText(e)}`;
    }
    if (key !== request) return;
    await nextTick();
    if (!container.value) return;
    const list = danmaku
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
    activePartId = props.part.id;
    // Renewal is bounded here, so disable the player's separate reconnect loop.
    Artplayer.RECONNECT_TIME_MAX = 0;
    art = new Artplayer({
      container: container.value,
      url: data.url,
      poster: props.poster || '',
      theme: '#3e665a',
      lang: 'zh-cn',
      volume: 0.7,
      autoplay: false,
      autoSize: false,
      fullscreen: true,
      fullscreenWeb: true,
      pip: true,
      playbackRate: true,
      setting: true,
      hotkey: true,
      mutex: true,
      playsInline: true,
      miniProgressBar: true,
      plugins: [
        artplayerPluginDanmuku({
          danmuku: list,
          emitter: false,
          visible: prefs.visible,
          fontSize: prefs.fontSize,
          antiOverlap: prefs.antiOverlap,
        }),
      ],
    });
    fullscreenHost.value = art.template.$player;
    syncPlayerStyles();
    resize?.observe(art.template.$player);
    art.controls.add({
      name: 'treasure-danmaku-settings',
      position: 'right',
      index: 9,
      html: '<button type="button" class="dm-player-settings-trigger" aria-label="打开高级弹幕设置">弹幕设置</button>',
      tooltip: '字体、描边与弹幕显示',
      click: () => {
        settingsOpen.value = !settingsOpen.value;
        if (settingsOpen.value)
          void nextTick(() => {
            settingsElement.value?.focus();
            if (!fullscreenActive.value)
              settingsElement.value?.scrollIntoView({ block: 'nearest' });
          });
      },
    });
    art.on('fullscreen', syncFullscreen);
    art.on('fullscreenWeb', syncFullscreen);
    art.on('artplayerPluginDanmuku:config', (value: unknown) => {
      if (applyingPreferences) return;
      const option = value as DanmakuOption;
      prefs.visible = !!option.visible;
      prefs.opacity = Math.round((option.opacity ?? 1) * 100);
      prefs.speed = option.speed ?? 5;
      prefs.rolling = !!option.modes?.includes(0);
      prefs.top = !!option.modes?.includes(1);
      prefs.bottom = !!option.modes?.includes(2);
      prefs.antiOverlap = !!option.antiOverlap;
      prefs.synchronousPlayback = !!option.synchronousPlayback;
      const scale = screenScale();
      if (typeof option.fontSize === 'number')
        prefs.fontSize = clamp(Math.round(option.fontSize / scale), 12, 120);
      const bottom = option.margin?.[1];
      if (typeof bottom === 'string') {
        const area = 100 - Number.parseFloat(bottom);
        if ([25, 50, 75, 100].includes(area)) prefs.area = area;
      } else if (typeof bottom === 'number') prefs.area = 100;
    });
    art.on('artplayerPluginDanmuku:hide', () => {
      if (!applyingPreferences) prefs.visible = false;
    });
    art.on('artplayerPluginDanmuku:show', () => {
      if (!applyingPreferences) prefs.visible = true;
    });
    art.on('ready', async () => {
      applyPreferences();
      try {
        const progress = await api<{ position: number }>(`/progress/${props.part.id}`);
        if (key === request && art && progress.position > 0 && progress.position < art.duration - 3)
          art.currentTime = progress.position;
      } catch {
        /* Missing progress does not block playback. */
      }
    });
    art.on('video:pause', saveProgress);
    art.on('video:timeupdate', () => {
      if (Date.now() - lastSaved > 15000) {
        lastSaved = Date.now();
        void saveProgress();
      }
    });
    art.on('video:error', () => {
      if (renewing) return;
      if (recoveries === 0) {
        recoveries = 1;
        void renew();
      } else
        error.value = '当前媒体无法播放。已尝试更新播放地址，可以手动重试或选择其他已归档版本。';
    });
  } catch (e) {
    if (key === request) error.value = errorText(e);
  } finally {
    if (key === request) busy.value = false;
  }
}
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
    void setup();
  },
);
onMounted(() => {
  void setup();
  resize = new ResizeObserver(() => {
    if (prefs.scaleWithScreen) applyPreferences();
  });
});
onBeforeUnmount(() => {
  request++;
  void saveProgress();
  resize?.disconnect();
  art?.destroy(false);
  art = null;
});
</script>
<template>
  <section class="archive-player">
    <div ref="container" class="player-stage"></div>
    <div v-if="busy" class="player-loading" role="status">正在准备播放…</div>
    <div v-if="error" class="player-error" role="alert">
      <span>{{ error }}</span
      ><button @click="art ? renew() : setup()">重试播放</button>
    </div>
    <div class="player-toolbar">
      <label class="check-label"
        ><input v-model="prefs.visible" type="checkbox" />弹幕
        <span v-if="dmCount !== null" class="muted">{{ dmCount }} 条</span></label
      >
      <div class="player-selects">
        <label v-if="part.variants.length > 1"
          >画质
          <select v-model="variantId" @change="setup">
            <option v-for="variant in part.variants" :key="variant.id" :value="variant.id">
              {{ variant.height ? `${variant.height}P` : variant.quality || variant.kind }} ·
              {{ variant.video_codec || '视频' }}
            </option>
          </select></label
        ><label v-if="playback?.subtitles?.length"
          >字幕
          <select v-model="subtitle" @change="selectSubtitle">
            <option value="">关闭</option>
            <option v-for="track in playback.subtitles" :key="track.url" :value="track.url">
              {{ track.label }}{{ track.is_auto ? '（自动）' : '' }}
            </option>
          </select></label
        >
      </div>
    </div>
    <p v-if="note" class="inline-notice">{{ note }}</p>
    <p v-if="progressError" class="inline-notice" role="status">{{ progressError }}</p>
    <Teleport :to="fullscreenHost || 'body'" :disabled="!fullscreenActive"
      ><details
        ref="settingsElement"
        class="danmaku-settings"
        :class="{ 'dm-fullscreen-panel': fullscreenActive }"
        :open="settingsOpen"
        v-show="!fullscreenActive || settingsOpen"
        tabindex="-1"
        :role="fullscreenActive ? 'dialog' : undefined"
        aria-label="高级弹幕显示设置"
        @toggle="settingsToggled"
        @click.stop
        @keydown.stop
      >
        <summary>弹幕显示设置<span class="muted">字体、大小与显示区域</span></summary>
        <div v-if="fullscreenActive" class="dm-fullscreen-actions">
          <button @click="settingsOpen = false">关闭设置</button>
        </div>
        <div class="dm-settings-grid">
          <label
            >不透明度 <output>{{ prefs.opacity }}%</output
            ><input v-model.number="prefs.opacity" type="range" min="0" max="100" /></label
          ><label
            >字号 <output>{{ prefs.fontSize }} px</output
            ><input v-model.number="prefs.fontSize" type="range" min="12" max="120" /></label
          ><label
            >显示区域<select v-model.number="prefs.area">
              <option :value="25">顶部四分之一</option>
              <option :value="50">半屏</option>
              <option :value="75">四分之三</option>
              <option :value="100">全屏</option>
            </select></label
          ><label
            >移动速度<select v-model.number="prefs.speed">
              <option :value="10">很慢</option>
              <option :value="7.5">较慢</option>
              <option :value="5">适中</option>
              <option :value="2.5">较快</option>
              <option :value="1">很快</option>
            </select></label
          ><label
            >字体<select v-model="prefs.fontFamily">
              <option value="Microsoft YaHei">微软雅黑</option>
              <option value="SimHei">黑体</option>
              <option value="SimSun">宋体</option>
              <option value="KaiTi">楷体</option>
              <option value="sans-serif">系统无衬线</option>
              <option value="monospace">等宽字体</option>
              <option value="custom">自定义本机字体</option>
            </select></label
          ><label v-if="prefs.fontFamily === 'custom'"
            >本机字体名称<input v-model="prefs.customFont" placeholder="如 Noto Sans SC" /></label
          ><label
            >字重<select v-model.number="prefs.weight">
              <option :value="400">常规</option>
              <option :value="500">中等</option>
              <option :value="600">半粗</option>
              <option :value="700">粗体</option>
            </select></label
          ><label
            >文字效果<select v-model="prefs.outline">
              <option value="stroke">描边</option>
              <option value="heavy">重墨</option>
              <option value="shadow">45° 投影</option>
              <option value="none">无</option>
            </select></label
          ><label
            >效果宽度 <output>{{ prefs.strokeWidth }} px</output
            ><input
              v-model.number="prefs.strokeWidth"
              type="range"
              min="0"
              max="3"
              step="0.5" /></label
          ><label
            >字符间距 <output>{{ prefs.spacing }} px</output
            ><input v-model.number="prefs.spacing" type="range" min="0" max="6" step="0.5" /></label
          ><label>描边颜色<input v-model="prefs.strokeColor" type="color" /></label
          ><label class="check-label"
            ><input v-model="prefs.uniform" type="checkbox" />统一文字颜色<input
              v-model="prefs.color"
              aria-label="统一弹幕颜色"
              type="color"
              :disabled="!prefs.uniform"
          /></label>
        </div>
        <div class="dm-checks">
          <label class="check-label"><input v-model="prefs.rolling" type="checkbox" />滚动</label
          ><label class="check-label"><input v-model="prefs.top" type="checkbox" />顶部</label
          ><label class="check-label"><input v-model="prefs.bottom" type="checkbox" />底部</label
          ><label class="check-label"
            ><input v-model="prefs.colored" type="checkbox" />彩色弹幕</label
          ><label class="check-label"
            ><input v-model="prefs.antiOverlap" type="checkbox" />防重叠</label
          ><label class="check-label"
            ><input v-model="prefs.subtitleSafe" type="checkbox" />防挡字幕</label
          ><label class="check-label"
            ><input v-model="prefs.scaleWithScreen" type="checkbox" />字号随屏幕缩放</label
          ><label class="check-label"
            ><input v-model="prefs.synchronousPlayback" type="checkbox" />速度同步倍速</label
          >
        </div>
        <div class="dm-preview-row">
          <span
            class="dm-preview"
            :style="{
              fontFamily: family,
              fontSize: `${Math.min(prefs.fontSize, 40)}px`,
              fontWeight: prefs.weight,
              letterSpacing: `${prefs.spacing}px`,
              textShadow: shadow,
              color: prefs.uniform ? prefs.color : '#fff',
              opacity: prefs.opacity / 100,
            }"
            >珍藏每一个值得重看的瞬间</span
          ><button @click="Object.assign(prefs, defaults)">恢复默认</button>
        </div>
        <p class="muted small">设置保存在当前浏览器。未安装的字体会使用系统替代字体。</p>
      </details></Teleport
    >
  </section>
</template>

<script setup lang="ts">
import { ref, useId, watch, onMounted, onBeforeUnmount } from 'vue';
const props = defineProps<{
  initialTab?: 'playback' | 'danmaku';
  returnFocus?: HTMLElement | null;
  busy?: boolean;
  sheet?: boolean;
  rateOnly?: boolean;
}>();
const emit = defineEmits<{ close: []; reset: [tab: 'playback' | 'danmaku'] }>();
const dialog = ref<HTMLElement>(),
  tab = ref(props.initialTab || 'playback');
const tabs = [
  ['playback', '播放'],
  ['danmaku', '弹幕'],
] as const;
const id = useId();
const body = ref<HTMLElement>();
let savedOverflow = '';
watch(
  () => props.sheet,
  (sheet, previous) => {
    if (sheet) {
      savedOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
    } else if (previous) document.body.style.overflow = savedOverflow;
  },
  { immediate: true },
);
watch(
  tab,
  () => {
    if (body.value) body.value.scrollTop = 0;
  },
  { flush: 'post' },
);
function focusable() {
  return [
    ...(dialog.value?.querySelectorAll<HTMLElement>(
      'button:not(:disabled):not([tabindex="-1"]), input:not(:disabled), select:not(:disabled), summary, [tabindex="0"]',
    ) || []),
  ].filter((el) => el.getClientRects().length > 0);
}
function keydown(event: KeyboardEvent) {
  event.stopPropagation();
  if (event.key === 'Escape') {
    event.preventDefault();
    emit('close');
  }
  if (event.key !== 'Tab') return;
  const list = focusable(),
    first = list[0],
    last = list.at(-1);
  if (
    event.shiftKey &&
    (document.activeElement === first || document.activeElement === dialog.value)
  ) {
    event.preventDefault();
    last?.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first?.focus();
  }
}
function switchTab(event: KeyboardEvent) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  tab.value =
    event.key === 'Home'
      ? 'playback'
      : event.key === 'End'
        ? 'danmaku'
        : tab.value === 'playback'
          ? 'danmaku'
          : 'playback';
  dialog.value?.querySelector<HTMLElement>(`[data-tab="${tab.value}"]`)?.focus();
}
onMounted(() => dialog.value?.querySelector<HTMLElement>('.player-settings-close')?.focus());
onBeforeUnmount(() => {
  if (props.sheet) document.body.style.overflow = savedOverflow;
  if (props.returnFocus?.isConnected) props.returnFocus.focus();
});
</script>
<template>
  <div
    class="player-settings-scrim"
    :class="{ 'player-sheet': sheet, 'player-rate-panel': rateOnly }"
    @click.self="$emit('close')"
    @pointerdown.stop
    @click.stop
    @dblclick.stop
    @keydown="keydown"
  >
    <section
      ref="dialog"
      class="player-settings-dialog"
      role="dialog"
      aria-modal="true"
      :aria-label="rateOnly ? '播放速度' : '播放器设置'"
      tabindex="-1"
    >
      <header class="player-settings-header">
        <strong>{{ rateOnly ? '播放速度' : '播放器设置' }}</strong
        ><button class="player-settings-close" aria-label="关闭播放器设置" @click="$emit('close')">
          关闭 <span aria-hidden="true">×</span>
        </button>
      </header>
      <div
        v-if="!rateOnly"
        class="player-settings-tabs"
        role="tablist"
        aria-label="设置分类"
        @keydown="switchTab"
      >
        <button
          v-for="[value, label] in tabs"
          :key="value"
          :id="`${id}-${value}`"
          :data-tab="value"
          role="tab"
          :aria-controls="`${id}-panel`"
          :aria-selected="tab === value"
          :tabindex="tab === value ? 0 : -1"
          @click="tab = value"
        >
          {{ label }}
        </button>
      </div>
      <div
        ref="body"
        :id="`${id}-panel`"
        class="player-settings-body"
        :role="rateOnly ? undefined : 'tabpanel'"
        :aria-labelledby="rateOnly ? undefined : `${id}-${tab}`"
      >
        <slot :name="rateOnly ? 'rate' : tab"></slot>
        <details
          v-if="!rateOnly && tab === 'playback'"
          class="player-settings-details player-keyboard-help"
        >
          <summary>键盘快捷键</summary>
          <p class="player-setting-help">
            焦点在播放器时：空格 / K 暂停或播放，← → 跳转 5 秒，↑ ↓ 调整音量，M 静音，D 开关弹幕，S
            打开设置，F 网页全屏。设置内使用 Tab 导航、方向键调整选项，Esc 关闭。
          </p>
        </details>
      </div>
      <footer v-if="!rateOnly" class="player-settings-footer">
        <span>{{ tab === 'danmaku' ? '弹幕偏好保存在此浏览器' : '设置当前播放器' }}</span
        ><button :disabled="busy" @click="$emit('reset', tab)">恢复默认</button>
      </footer>
    </section>
  </div>
</template>

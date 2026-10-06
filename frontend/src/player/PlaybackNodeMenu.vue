<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import type { Playback } from '../types';
import { bitrate } from './mediaInfo';
import UiIcon from '../components/UiIcon.vue';
const props = defineProps<{ playback: Playback | null; routeId: string; busy: boolean }>();
const emit = defineEmits<{ select: [id: string] }>();
const open = ref(false),
  root = ref<HTMLElement>(),
  popover = ref<HTMLElement>(),
  menuOffset = ref(0),
  trigger = ref<HTMLButtonElement>();
const current = computed(() =>
  props.playback?.routes?.find((route) => route.id === props.playback?.selected_route_id),
);
function close() {
  open.value = false;
}
function outside(event: PointerEvent) {
  if (!root.value?.contains(event.target as Node)) close();
}
function select(id: string) {
  if (props.busy) return;
  close();
  if (id !== props.routeId) emit('select', id);
  trigger.value?.focus();
}
function keydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    close();
    trigger.value?.focus();
  }
}
function positionMenu() {
  if (!open.value || !root.value || !popover.value) return;
  const anchor = root.value.getBoundingClientRect();
  const menu = popover.value.getBoundingClientRect();
  const rightEdge = document.documentElement.clientWidth - 16;
  const left = Math.max(16, Math.min(anchor.left, rightEdge - menu.width));
  menuOffset.value = left - anchor.left;
}
watch(open, async (value, _, onCleanup) => {
  menuOffset.value = 0;
  if (!value) return;
  document.addEventListener('pointerdown', outside);
  window.addEventListener('resize', positionMenu);
  onCleanup(() => {
    document.removeEventListener('pointerdown', outside);
    window.removeEventListener('resize', positionMenu);
  });
  await nextTick();
  positionMenu();
});
watch(() => props.playback?.variant_id, close);
</script>
<template>
  <div ref="root" class="playback-node-menu" @keydown="keydown">
    <button
      ref="trigger"
      class="node-trigger"
      :aria-expanded="open"
      aria-haspopup="true"
      :disabled="!playback"
      @click="open = !open"
    >
      {{ busy ? '正在切换…' : current?.name || '播放节点' }}<UiIcon name="arrow" />
    </button>
    <div
      v-if="open"
      ref="popover"
      class="node-popover"
      :style="{ left: `${menuOffset}px` }"
      aria-label="切换播放节点"
    >
      <header>播放节点<span v-if="busy" role="status">切换中</span></header>
      <button
        class="node-option"
        :class="{ selected: !routeId }"
        :disabled="busy"
        @click="select('')"
      >
        <span>自动选择<small>优先使用可用且连接较好的副本</small></span
        ><span v-if="!routeId" aria-label="已选择">✓</span>
      </button>
      <button
        v-for="node in playback?.routes || []"
        :key="node.id"
        class="node-option"
        :class="{ selected: routeId === node.id }"
        :disabled="busy || node.status === 'unavailable'"
        @click="select(node.id)"
      >
        <span
          >{{ node.name }}<small v-if="node.status === 'unavailable'">暂不可用</small
          ><small
            v-else-if="node.measurement_scope === 'browser_delivery' && node.latency_ms != null"
            >{{ Math.round(node.latency_ms) }} ms<template v-if="node.throughput_bps">
              · {{ bitrate(node.throughput_bps) }}</template
            ></small
          ><small v-else>{{
            node.id === playback?.selected_route_id ? '当前播放' : '可用副本'
          }}</small></span
        ><span v-if="routeId === node.id" aria-label="已选择">✓</span>
      </button>
      <p>切换时保留当前进度和播放状态。</p>
    </div>
  </div>
</template>
<style scoped>
.playback-node-menu {
  position: relative;
  display: inline-flex;
}
.node-trigger {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 0;
  border: 0;
  border-radius: 0;
  background: none;
  font: inherit;
  color: var(--accent, #356d8e);
  cursor: pointer;
}
.node-trigger:disabled {
  opacity: 0.55;
  cursor: wait;
}
.node-trigger :deep(svg) {
  width: 12px;
  height: 12px;
  transform: rotate(90deg);
}
.node-popover {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  width: min(282px, calc(100vw - 32px));
  box-sizing: border-box;
  z-index: 25;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface, #fff);
  box-shadow: 0 8px 28px #0002;
  color: var(--text);
  font-size: 13px;
}
header {
  display: flex;
  justify-content: space-between;
  padding: 5px 8px 9px;
  font-weight: 600;
}
header span {
  font-size: 11px;
  font-weight: 400;
  color: var(--muted);
}
.node-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  border: 0;
  border-radius: 3px;
  padding: 9px 8px;
  background: transparent;
  text-align: left;
  color: inherit;
  font: inherit;
  cursor: pointer;
}
.node-option:hover,
.node-option.selected {
  background: var(--surface-soft, #f4f6f8);
}
.node-option.selected {
  color: var(--accent, #356d8e);
}
.node-option:disabled {
  opacity: 0.5;
  cursor: default;
}
.node-option small {
  display: block;
  margin-top: 2px;
  font-size: 11px;
  color: var(--muted);
  font-weight: 400;
}
p {
  margin: 7px 8px 3px;
  color: var(--muted);
  font-size: 11px;
}
@media (max-width: 600px) {
  .node-popover {
    right: auto;
    left: 0;
  }
}
</style>

<script setup lang="ts">
import { computed, ref, watch, nextTick, onMounted, onBeforeUnmount, useId } from 'vue';
import { duration } from '../api';
import type { VideoDetail } from './mediaInfo';
import type { QueuePreferences, QueueMode } from './queue';
import { queueModes, queuePosition, queueMenuFocus } from './queuePresentation';
import QueueIcon from './QueueIcon.vue';
const props = defineProps<{
  items: VideoDetail[];
  currentId: string;
  currentTitle: string;
  partTitle?: string;
  partPosition?: number;
  partCount?: number;
  playing?: boolean;
  title: string;
  total: number;
  busy: boolean;
  error: string;
  preferences: QueuePreferences;
  previous: boolean;
  next: boolean;
  transitioning: boolean;
}>();
const emit = defineEmits<{
  select: [id: string];
  more: [];
  previous: [];
  next: [];
  preferences: [value: Partial<QueuePreferences>];
}>();
const modeWrap = ref<HTMLElement>(),
  menu = ref<HTMLElement>(),
  modeButton = ref<HTMLButtonElement>(),
  list = ref<HTMLOListElement>();
const expanded = ref(true),
  menuOpen = ref(false),
  menuAbove = ref(false),
  id = useId();
const hasList = computed(() => !!(props.title || props.busy || props.error));
const currentIndex = computed(() => props.items.findIndex((item) => item.id === props.currentId));
const position = computed(() =>
  queuePosition(currentIndex.value, props.total, props.partPosition, props.partCount),
);
const currentMode = computed(
  () => queueModes.find((mode) => mode.value === props.preferences.mode)!,
);
const currentLabel = computed(() =>
  (props.partCount || 0) > 1 && props.partTitle ? props.partTitle : props.currentTitle,
);
function closeMenu(restoreFocus = false) {
  menuOpen.value = false;
  if (restoreFocus) modeButton.value?.focus({ preventScroll: true });
}
async function toggleMenu() {
  if (menuOpen.value) {
    closeMenu();
    return;
  }
  menuOpen.value = true;
  await nextTick();
  const trigger = modeButton.value?.getBoundingClientRect();
  const menuHeight = (menu.value?.offsetHeight || 0) + 8;
  menuAbove.value =
    !!trigger && trigger.bottom + menuHeight > window.innerHeight && trigger.top >= menuHeight;
  await nextTick();
  menu.value
    ?.querySelector<HTMLButtonElement>('[role="menuitemradio"][aria-checked="true"]')
    ?.focus({ preventScroll: true });
}
function chooseMode(mode: QueueMode) {
  emit('preferences', { mode });
  closeMenu(true);
}
function menuKey(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault();
    event.stopPropagation();
    closeMenu(true);
    return;
  }
  const buttons = [...(menu.value?.querySelectorAll<HTMLButtonElement>('button') || [])];
  const next = queueMenuFocus(
    event.key,
    buttons.indexOf(event.target as HTMLButtonElement),
    buttons.length,
  );
  if (next !== null) {
    event.preventDefault();
    buttons[next]?.focus();
  }
}
function outside(event: PointerEvent) {
  if (!modeWrap.value?.contains(event.target as Node)) closeMenu();
}
function focusOut(event: FocusEvent) {
  if (event.relatedTarget && !modeWrap.value?.contains(event.relatedTarget as Node)) closeMenu();
}
async function revealCurrent() {
  await nextTick();
  const rows = list.value;
  const selected = rows?.querySelector<HTMLElement>('[aria-current="true"]');
  if (rows && selected)
    rows.scrollTop = Math.max(
      0,
      selected.offsetTop - (rows.clientHeight - selected.offsetHeight) / 2,
    );
}
watch([() => props.currentId, currentIndex, expanded], () => {
  if (expanded.value) void revealCurrent();
});
onMounted(() => {
  document.addEventListener('pointerdown', outside);
  void revealCurrent();
});
onBeforeUnmount(() => document.removeEventListener('pointerdown', outside));
</script>
<template>
  <section
    class="playlist-player"
    :class="{
      'playlist-player--single': !hasList && (partCount || 0) < 2,
      'playlist-player--menu-open': menuOpen,
    }"
    aria-label="播放队列"
  >
    <div class="playlist-toolbar">
      <button
        v-if="hasList"
        class="playlist-now"
        :aria-expanded="expanded"
        :aria-controls="`${id}-list`"
        :title="title"
        @click="expanded = !expanded"
      >
        <QueueIcon name="list" class="playlist-list-icon" />
        <span class="playlist-now-text"
          ><span class="playlist-source"
            ><span class="playlist-scope-title">{{ title || '播放列表' }}</span
            ><span v-if="position.video" class="playlist-position">{{ position.video }}</span
            ><span v-if="position.part" class="playlist-position">{{ position.part }}</span></span
          ><strong :title="currentTitle">{{ currentTitle }}</strong></span
        >
        <QueueIcon name="chevron" class="playlist-chevron" :class="{ 'is-expanded': expanded }" />
      </button>
      <div v-else-if="(partCount || 0) > 1" class="playlist-now-text playlist-part-heading">
        <span class="playlist-source"
          >视频选集<span class="playlist-position">{{ position.part }}</span></span
        ><strong :title="currentLabel">{{ currentLabel }}</strong>
      </div>
      <div v-else class="playlist-now-text playlist-part-heading">
        <span class="playlist-source">播放方式</span><strong>{{ currentMode.label }}</strong>
      </div>
      <div class="playlist-actions">
        <button
          v-if="hasList || (partCount || 0) > 1"
          class="playlist-icon-button"
          aria-label="上一集"
          title="上一集"
          :disabled="!previous || transitioning"
          @click="emit('previous')"
        >
          <QueueIcon name="previous" />
        </button>
        <button
          v-if="hasList || (partCount || 0) > 1"
          class="playlist-icon-button"
          aria-label="下一集"
          title="下一集"
          :disabled="!next || transitioning"
          @click="emit('next')"
        >
          <QueueIcon name="next" />
        </button>
        <div ref="modeWrap" class="playlist-mode-wrap" @focusout="focusOut">
          <button
            ref="modeButton"
            class="playlist-icon-button playlist-mode-button"
            :aria-label="`播放方式：${currentMode.label}`"
            :title="`播放方式：${currentMode.label}`"
            aria-haspopup="menu"
            :aria-expanded="menuOpen"
            :aria-controls="`${id}-mode`"
            @click="toggleMenu"
          >
            <QueueIcon :name="preferences.mode" /><QueueIcon name="chevron" class="mode-chevron" />
          </button>
          <div
            v-if="menuOpen"
            :id="`${id}-mode`"
            ref="menu"
            role="menu"
            aria-label="播放方式"
            class="playlist-mode-menu"
            :class="{ 'playlist-mode-menu--above': menuAbove }"
            @keydown="menuKey"
          >
            <div class="playlist-menu-heading">播放方式</div>
            <button
              v-for="mode in queueModes"
              :key="mode.value"
              role="menuitemradio"
              :aria-checked="preferences.mode === mode.value"
              :tabindex="preferences.mode === mode.value ? 0 : -1"
              @click="chooseMode(mode.value)"
            >
              <QueueIcon :name="mode.value" /><span>{{ mode.label }}</span
              ><QueueIcon v-if="preferences.mode === mode.value" name="check" class="mode-check" />
            </button>
            <div role="separator" class="playlist-menu-divider"></div>
            <button
              role="menuitemcheckbox"
              :aria-checked="preferences.autoStart"
              tabindex="-1"
              title="打开视频或手动切换时自动播放"
              @click="emit('preferences', { autoStart: !preferences.autoStart })"
            >
              <span>自动开始播放</span
              ><span
                class="playlist-toggle"
                :class="{ 'is-on': preferences.autoStart }"
                aria-hidden="true"
                ><i></i
              ></span>
            </button>
          </div>
        </div>
      </div>
    </div>
    <div v-if="hasList && expanded" :id="`${id}-list`" class="playlist-content">
      <ol ref="list" class="playlist-items" aria-label="列表中的视频">
        <li v-for="(item, index) in items" :key="item.id">
          <button
            :aria-current="item.id === currentId ? 'true' : undefined"
            :disabled="transitioning"
            :aria-label="`${index + 1} ${item.title}${item.id === currentId ? '，当前视频' : ''}`"
            @click="emit('select', item.id)"
          >
            <span class="playlist-item-index"
              ><QueueIcon
                v-if="item.id === currentId"
                name="playing"
                :class="{ 'is-playing': playing }"
              /><span v-else>{{ index + 1 }}</span></span
            >
            <span class="playlist-cover"
              ><img v-if="item.cover_url" :src="item.cover_url" alt="" loading="lazy" /><QueueIcon
                v-else
                name="list"
              /><span>{{ duration(item.duration) }}</span></span
            >
            <span class="playlist-item-info"
              ><strong>{{ item.title }}</strong
              ><small
                >{{
                  [...new Set(item.creators?.map((creator) => creator.name).filter(Boolean))].join(
                    ' / ',
                  ) || item.bvid
                }}<template v-if="item.parts_count > 1">
                  · {{ item.parts_count }} P</template
                ></small
              ></span
            >
          </button>
        </li>
      </ol>
      <p v-if="items.length && currentIndex < 0" class="playlist-status">
        尚未定位当前视频 · 已读取 {{ items.length }} / {{ total }}
      </p>
      <p v-if="error" class="playlist-status playlist-error" role="alert">
        {{ error }} <button @click="emit('more')">重试</button>
      </p>
      <button
        v-if="items.length < total || busy"
        :disabled="busy"
        class="playlist-load-more"
        @click="emit('more')"
      >
        {{ busy ? '正在读取列表…' : currentIndex < 0 ? '继续定位当前视频' : '加载更多' }}
      </button>
    </div>
  </section>
</template>
<style scoped>
.playlist-player {
  position: relative;
  container-type: inline-size;
  margin: 12px 0;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface, #fff);
}
.playlist-player--menu-open {
  z-index: 170;
}
.playlist-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 62px;
  padding: 8px 12px;
}
.playlist-now {
  display: flex;
  flex: 1;
  min-width: 0;
  align-items: center;
  gap: 10px;
  background: none;
  color: var(--text, #18191c);
  border: 0;
  padding: 0;
  text-align: left;
  cursor: pointer;
}
.playlist-now-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.playlist-now-text strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 500;
}
.playlist-source {
  color: var(--muted);
  font-size: 11px;
  display: flex;
  gap: 10px;
  align-items: center;
}
.playlist-scope-title {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.playlist-position {
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}
.playlist-list-icon {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  color: var(--muted);
}
.playlist-chevron {
  width: 14px;
  height: 14px;
  color: var(--muted);
  flex-shrink: 0;
  transition: transform 0.15s;
}
.playlist-chevron.is-expanded {
  transform: rotate(180deg);
}
.playlist-actions {
  display: flex;
  align-items: center;
  gap: 3px;
  flex-shrink: 0;
}
.playlist-icon-button {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 2px;
  width: 44px;
  height: 44px;
  padding: 7px;
  border: 0;
  border-radius: 5px;
  color: var(--text, #18191c);
  background: none;
  cursor: pointer;
}
.playlist-icon-button:hover:not(:disabled),
.playlist-icon-button[aria-expanded='true'] {
  color: var(--accent);
  background: var(--accent-soft, #edf6f4);
}
.playlist-icon-button:disabled {
  opacity: 0.3;
  cursor: default;
}
.playlist-icon-button svg {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
}
.playlist-mode-button {
  width: 46px;
}
.playlist-icon-button .mode-chevron {
  width: 10px;
  height: 10px;
}
.playlist-mode-wrap {
  position: relative;
}
.playlist-mode-menu {
  position: absolute;
  z-index: 130;
  top: calc(100% + 8px);
  right: 0;
  width: 218px;
  padding: 7px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface, #fff);
  box-shadow: 0 8px 28px #00000016;
}
.playlist-mode-menu--above {
  top: auto;
  bottom: calc(100% + 8px);
}
.playlist-menu-heading {
  font-size: 11px;
  color: var(--muted);
  padding: 5px 9px 8px;
}
.playlist-mode-menu button {
  display: flex;
  align-items: center;
  gap: 11px;
  width: 100%;
  min-height: 44px;
  padding: 8px 9px;
  border: 0;
  border-radius: 5px;
  background: none;
  color: var(--text, #18191c);
  text-align: left;
  font-size: 12px;
  cursor: pointer;
}
.playlist-mode-menu button:hover,
.playlist-mode-menu button:focus-visible {
  background: var(--accent-soft, #edf6f4);
}
.playlist-mode-menu button[aria-checked='true'] {
  color: var(--accent);
}
.playlist-mode-menu svg {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}
.playlist-mode-menu .mode-check {
  width: 15px;
  height: 15px;
  margin-left: auto;
}
.playlist-menu-divider {
  height: 1px;
  background: var(--border);
  margin: 6px 2px;
}
.playlist-toggle {
  margin-left: auto;
  width: 28px;
  height: 16px;
  border-radius: 12px;
  background: #b7bdc2;
  padding: 2px;
}
.playlist-toggle i {
  display: block;
  width: 12px;
  height: 12px;
  background: #fff;
  border-radius: 50%;
  transition: transform 0.15s;
}
.playlist-toggle.is-on {
  background: var(--accent);
}
.playlist-toggle.is-on i {
  transform: translateX(12px);
}
.playlist-content {
  border-top: 1px solid var(--border);
  padding: 5px;
}
.playlist-items {
  position: relative;
  padding: 0;
  margin: 0;
  list-style: none;
  max-height: 260px;
  overflow: auto;
  overscroll-behavior: contain;
  scrollbar-width: thin;
}
.playlist-items li button {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-width: 0;
  padding: 8px 9px;
  border: 0;
  border-radius: 5px;
  color: var(--text, #18191c);
  background: none;
  text-align: left;
  cursor: pointer;
}
.playlist-items li button:hover {
  background: var(--accent-soft, #edf6f4);
}
.playlist-items li button[aria-current='true'] {
  color: var(--accent);
  background: var(--accent-soft, #edf6f4);
}
.playlist-item-index {
  display: flex;
  width: 20px;
  flex-shrink: 0;
  justify-content: center;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  color: var(--muted);
}
.playlist-item-index svg {
  width: 18px;
  height: 18px;
  color: var(--accent);
}
.playlist-cover {
  position: relative;
  display: grid;
  place-items: center;
  width: 74px;
  aspect-ratio: 16/9;
  flex-shrink: 0;
  border-radius: 4px;
  overflow: hidden;
  background: var(--bg, #f1f3f4);
  color: var(--muted);
}
.playlist-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.playlist-cover > svg {
  width: 22px;
  height: 22px;
}
.playlist-cover > span {
  position: absolute;
  right: 3px;
  bottom: 2px;
  padding: 0 2px;
  border-radius: 2px;
  background: #0008;
  color: #fff;
  font-size: 9px;
  line-height: 1.6;
}
.playlist-item-info {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}
.playlist-item-info strong {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.55;
}
.playlist-item-info small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--muted);
  font-size: 10px;
}
.playlist-load-more {
  min-height: 44px;
  width: 100%;
  padding: 9px;
  border: 0;
  color: var(--accent);
  background: none;
  font-size: 11px;
  cursor: pointer;
}
.playlist-status {
  margin: 8px;
  color: var(--muted);
  font-size: 11px;
}
.playlist-error {
  color: var(--danger, #b64b4b);
}
.playlist-error button {
  margin-left: 6px;
  border: 0;
  background: none;
  color: inherit;
  text-decoration: underline;
}
.playlist-player--single {
  border: 0;
  margin: 0;
  background: none;
}
.playlist-player--single .playlist-toolbar {
  min-height: 58px;
  padding: 6px 0;
}
.playlist-player button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
@media (max-width: 600px) {
  .playlist-toolbar {
    padding: 8px;
    gap: 4px;
  }
  .playlist-list-icon {
    display: none;
  }
  .playlist-now {
    gap: 5px;
  }
  .playlist-now-text strong {
    font-size: 12px;
  }
  .playlist-source {
    font-size: 10px;
    gap: 7px;
  }
  .playlist-actions {
    gap: 0;
  }
  .playlist-items {
    max-height: 236px;
  }
  .playlist-items li button {
    padding: 8px 5px;
    gap: 8px;
  }
  .playlist-cover {
    width: 68px;
  }
}
@container (max-width: 460px) {
  .playlist-toolbar {
    flex-wrap: wrap;
    gap: 4px 8px;
    padding: 8px;
  }
  .playlist-now,
  .playlist-part-heading {
    flex-basis: 100%;
    min-height: 44px;
  }
  .playlist-actions {
    margin-left: auto;
  }
  .playlist-player--single .playlist-part-heading {
    flex-basis: auto;
  }
  .playlist-now-text strong {
    font-size: 12px;
  }
  .playlist-cover {
    width: 60px;
  }
  .playlist-items li button {
    gap: 7px;
    padding: 8px 5px;
    min-height: 54px;
  }
  .playlist-items {
    max-height: 350px;
  }
}
@media (prefers-reduced-motion: no-preference) {
  .playlist-item-index .is-playing {
    animation: playlist-pulse 1s ease-in-out infinite alternate;
    transform-origin: center;
  }
  @keyframes playlist-pulse {
    from {
      transform: scaleY(0.6);
    }
    to {
      transform: scaleY(1);
    }
  }
}
</style>

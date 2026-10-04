<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { galleryIndex, galleryKeyIndex } from '../utils/commentGallery';

const props = defineProps<{ images: string[]; initialIndex: number }>();
const emit = defineEmits<{ close: [] }>();
const dialog = ref<HTMLDialogElement>();
const active = ref(galleryIndex(props.initialIndex, props.images.length));
const loading = ref(true),
  failed = ref(false);
const current = computed(() => props.images[active.value]);
let restoreFocus: HTMLElement | null = null;
let previousOverflow = '';

watch(
  () => props.images,
  () => {
    if (!props.images.length) emit('close');
    active.value = galleryIndex(active.value, props.images.length);
  },
);
watch(current, () => {
  loading.value = true;
  failed.value = false;
});
function move(offset: number) {
  active.value = galleryIndex(active.value + offset, props.images.length);
}
function keydown(event: KeyboardEvent) {
  if (event.altKey || event.ctrlKey || event.metaKey) return;
  if (event.key === 'Tab') {
    const buttons = Array.from(
      dialog.value?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') || [],
    );
    const first = buttons[0],
      last = buttons.at(-1);
    const target =
      event.shiftKey && document.activeElement === first
        ? last
        : !event.shiftKey && document.activeElement === last
          ? first
          : undefined;
    if (target) {
      event.preventDefault();
      target.focus();
    }
    return;
  }
  const next = galleryKeyIndex(active.value, props.images.length, event.key);
  if (next === undefined) return;
  event.preventDefault();
  event.stopPropagation();
  active.value = next;
}
onMounted(() => {
  restoreFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  previousOverflow = document.documentElement.style.overflow;
  document.documentElement.style.overflow = 'hidden';
  dialog.value?.showModal();
});
onBeforeUnmount(() => {
  dialog.value?.close();
  document.documentElement.style.overflow = previousOverflow;
  if (restoreFocus?.isConnected) restoreFocus.focus({ preventScroll: true });
});
</script>
<template>
  <Teleport to="body">
    <dialog
      ref="dialog"
      class="comment-lightbox"
      aria-label="评论图片预览"
      @cancel.prevent="emit('close')"
      @click.self="emit('close')"
      @keydown.stop="keydown"
    >
      <header class="comment-lightbox-heading">
        <div>
          评论图片
          <span v-if="images.length > 1" aria-live="polite"
            >{{ active + 1 }} / {{ images.length }}</span
          >
        </div>
        <button
          type="button"
          class="lightbox-control"
          aria-label="关闭图片预览"
          autofocus
          @click="emit('close')"
        >
          <svg
            width="24"
            height="24"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            aria-hidden="true"
          >
            <path d="m6 6 12 12M18 6 6 18" />
          </svg>
        </button>
      </header>
      <div class="comment-lightbox-stage" @click.self="emit('close')">
        <p v-if="failed" class="lightbox-message" role="status">这张图片暂时无法读取</p>
        <p v-else-if="loading" class="lightbox-message" role="status">正在载入图片…</p>
        <img
          v-if="current && !failed"
          :key="current"
          :src="current"
          :alt="`评论附图 ${active + 1}`"
          :class="{ loading }"
          decoding="async"
          @load="loading = false"
          @error="
            failed = true;
            loading = false;
          "
        />
      </div>
      <template v-if="images.length > 1">
        <button
          type="button"
          class="lightbox-control lightbox-previous"
          aria-label="上一张图片"
          :disabled="active === 0"
          @click="move(-1)"
        >
          <svg
            width="26"
            height="26"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            aria-hidden="true"
          >
            <path d="m15 5-7 7 7 7" />
          </svg>
        </button>
        <button
          type="button"
          class="lightbox-control lightbox-next"
          aria-label="下一张图片"
          :disabled="active === images.length - 1"
          @click="move(1)"
        >
          <svg
            width="26"
            height="26"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            aria-hidden="true"
          >
            <path d="m9 5 7 7-7 7" />
          </svg>
        </button>
      </template>
      <p class="comment-lightbox-help">{{ images.length > 1 ? '方向键切换 · ' : '' }}Esc 关闭</p>
    </dialog>
  </Teleport>
</template>
<style scoped>
.comment-lightbox {
  position: fixed;
  inset: 0;
  width: 100%;
  max-width: none;
  height: 100%;
  max-height: none;
  margin: 0;
  padding: 0;
  border: 0;
  color: #fff;
  background: transparent;
  overflow: hidden;
}
.comment-lightbox::backdrop {
  background: rgb(15 17 20 / 92%);
}
.comment-lightbox-heading {
  position: absolute;
  z-index: 1;
  inset: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 24px;
  font-size: 14px;
}
.comment-lightbox-heading span {
  margin-left: 12px;
  color: #aeb4bc;
  font-variant-numeric: tabular-nums;
}
.comment-lightbox-stage {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 78px 76px 64px;
  box-sizing: border-box;
}
.comment-lightbox-stage img {
  display: block;
  width: auto;
  height: auto;
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  border-radius: 3px;
}
.comment-lightbox-stage img.loading {
  opacity: 0;
}
.lightbox-message {
  position: absolute;
  margin: 0;
  color: #c9cdd4;
  font-size: 14px;
}
.lightbox-control {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  padding: 0;
  border: 1px solid rgb(255 255 255 / 14%);
  border-radius: 50%;
  color: #fff;
  background: rgb(255 255 255 / 7%);
  cursor: pointer;
}
.lightbox-control:hover {
  background: rgb(255 255 255 / 16%);
  border-color: rgb(255 255 255 / 30%);
}
.lightbox-control:focus-visible {
  outline: 2px solid #83decf;
  outline-offset: 4px;
}
.lightbox-control:disabled {
  opacity: 0.25;
  cursor: default;
}
.lightbox-previous,
.lightbox-next {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
}
.lightbox-previous {
  left: 18px;
}
.lightbox-next {
  right: 18px;
}
.comment-lightbox-help {
  position: absolute;
  bottom: 20px;
  left: 0;
  width: 100%;
  text-align: center;
  margin: 0;
  font-size: 12px;
  color: #969da7;
  pointer-events: none;
}
@media (max-width: 600px) {
  .comment-lightbox-heading {
    padding: 12px;
  }
  .comment-lightbox-stage {
    padding: 68px 12px 96px;
  }
  .lightbox-previous,
  .lightbox-next {
    top: auto;
    bottom: 40px;
    transform: none;
  }
  .lightbox-previous {
    left: calc(50% - 64px);
  }
  .lightbox-next {
    right: calc(50% - 64px);
  }
  .comment-lightbox-help {
    bottom: 14px;
  }
}
</style>

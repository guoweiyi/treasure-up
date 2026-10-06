<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { CommentEmote } from '../utils/commentContent';
import {
  collapsedComment,
  commentDisplay,
  type CommentPlayback,
  type CommentSeek,
} from '../utils/commentTimeline';
const props = defineProps<{
  content: string;
  emotes?: CommentEmote[];
  playback?: CommentPlayback;
}>();
const emit = defineEmits<{ seek: [value: CommentSeek] }>();
const failed = ref(new Set<string>());
const expanded = ref(false);
const preview = computed(() => collapsedComment(props.content));
watch(
  () => [props.content, props.emotes],
  () => {
    failed.value = new Set();
    expanded.value = false;
  },
);
const pieces = computed(() =>
  commentDisplay(
    expanded.value ? props.content : preview.value.text,
    props.emotes?.filter((item) => !failed.value.has(item.asset_url)),
    props.playback,
  ),
);
function fallback(url: string) {
  failed.value = new Set([...failed.value, url]);
}
</script>
<template>
  <div class="comment-copy">
    <p class="comment-content">
      <template v-for="(piece, index) in pieces" :key="index"
        ><img
          v-if="piece.kind === 'emote'"
          :src="piece.url"
          :alt="piece.text"
          :title="piece.text"
          class="comment-emote"
          loading="lazy"
          decoding="async"
          width="24"
          height="24"
          @error="fallback(piece.url)"
        /><button
          v-else-if="piece.kind === 'timestamp'"
          type="button"
          class="comment-timestamp"
          :title="`跳转到 P${piece.position} · ${piece.text}`"
          :aria-label="`跳转到 P${piece.position} · ${piece.text}`"
          @click="emit('seek', { partId: piece.partId, seconds: piece.seconds })"
        >
          {{ piece.text }}</button
        ><template v-else>{{ piece.text }}</template></template
      >
    </p>
    <button
      v-if="preview.collapsed"
      type="button"
      class="comment-expand"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      {{ expanded ? '收起' : '展开全文' }}
    </button>
  </div>
</template>
<style scoped>
.comment-content {
  margin: 8px 0 12px;
  color: #252a31;
  font-size: 14px;
  line-height: 1.8;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.comment-timestamp,
.comment-expand {
  padding: 0;
  min-height: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--accent);
  font: inherit;
  cursor: pointer;
}
.comment-timestamp {
  display: inline;
  line-height: inherit;
}
.comment-timestamp:hover,
.comment-expand:hover {
  text-decoration: underline;
  background: transparent;
}
.comment-expand {
  margin: -4px 0 12px;
  font-size: 12px;
}
.comment-timestamp:focus-visible,
.comment-expand:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.comment-emote {
  display: inline-block;
  width: 1.5em;
  height: 1.5em;
  max-width: 48px;
  object-fit: contain;
  vertical-align: -0.35em;
  margin: 0 1px;
}
</style>

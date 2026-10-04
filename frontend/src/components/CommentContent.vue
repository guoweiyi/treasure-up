<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { commentContent, type CommentEmote } from '../utils/commentContent';
const props = defineProps<{ content: string; emotes?: CommentEmote[] }>();
const failed = ref(new Set<string>());
watch(
  () => [props.content, props.emotes],
  () => {
    failed.value = new Set();
  },
);
const pieces = computed(() =>
  commentContent(
    props.content,
    props.emotes?.filter((item) => !failed.value.has(item.asset_url)),
  ),
);
function fallback(url: string) {
  failed.value = new Set([...failed.value, url]);
}
</script>
<template>
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
      /><template v-else>{{ piece.text }}</template></template
    >
  </p>
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

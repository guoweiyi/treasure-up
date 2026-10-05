<script setup lang="ts">
import { computed, ref, watch } from 'vue';
const props = withDefaults(defineProps<{ text?: string; limit?: number; expandable?: boolean }>(), {
  limit: 96,
  expandable: false,
});
const expanded = ref(false);
const characters = computed(() =>
  Array.from((props.text || '暂无简介').trim().replace(/\s+/gu, ' ')),
);
const truncated = computed(() => characters.value.length > props.limit);
const summary = computed(
  () => characters.value.slice(0, props.limit).join('') + (truncated.value ? '…' : ''),
);
watch(
  () => props.text,
  () => {
    expanded.value = false;
  },
);
</script>
<template>
  <div class="creator-bio">
    <p :class="{ collapsed: !expanded && (!expandable || truncated) }">
      {{ expanded ? text : summary }}
    </p>
    <button
      v-if="expandable && truncated"
      class="text-button"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      {{ expanded ? '收起' : '展开简介' }}
    </button>
  </div>
</template>
<style scoped>
.creator-bio {
  min-width: 0;
  max-width: 62ch;
}
.creator-bio p {
  margin: 6px 0;
  color: #707578;
  font-size: 13px;
  line-height: 1.65;
  overflow-wrap: anywhere;
  white-space: pre-line;
}
.creator-bio .collapsed {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.creator-bio button {
  color: var(--accent);
  font-size: 12px;
  min-height: 32px;
  padding: 0;
}
</style>

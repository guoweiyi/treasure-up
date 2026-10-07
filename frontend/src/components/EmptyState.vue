<script setup lang="ts">
import BrandLogo from './BrandLogo.vue';
defineProps<{ title?: string; text?: string; error?: boolean; branded?: boolean }>();
</script>
<template>
  <div class="empty-state" :role="error ? 'alert' : 'status'">
    <BrandLogo v-if="branded && !error" class="empty-brand" :size="112" decorative />
    <span v-else class="empty-symbol" aria-hidden="true">{{ error ? '!' : '▤' }}</span>
    <h2>{{ title || '这里还没有内容' }}</h2>
    <p v-if="text">{{ text }}</p>
    <slot />
  </div>
</template>

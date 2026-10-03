<script setup lang="ts">
defineProps<{ title?: string; text?: string; error?: boolean }>();
</script>
<template>
  <div class="empty-state" :role="error ? 'alert' : 'status'">
    <span class="empty-symbol" aria-hidden="true">{{ error ? '!' : '▤' }}</span>
    <h2>{{ title || '这里还没有内容' }}</h2>
    <p v-if="text">{{ text }}</p>
    <slot />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { contentBadges, type ContentFeatures } from '../utils/contentBadges';
const props = defineProps<{ features?: ContentFeatures; compact?: boolean }>();
const badges = computed(() => contentBadges(props.features));
</script>
<template>
  <div v-if="badges.length" class="content-badges" :class="{ compact }" aria-label="归档内容特性">
    <span
      v-for="badge in badges"
      :key="badge.kind"
      class="content-badge"
      :class="badge.kind"
      :title="badge.title"
      :aria-label="badge.title"
    >
      <svg width="12" height="12" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <path
          v-if="badge.kind === 'charging'"
          d="m11.8 1.8-7 9h4.6l-1.2 7.4 7-10h-4.5z"
          fill="currentColor"
        />
        <template v-else-if="badge.kind === 'vision'">
          <path
            d="M1.8 10s3-5.8 8.2-5.8 8.2 5.8 8.2 5.8-3 5.8-8.2 5.8S1.8 10 1.8 10Z"
            stroke="currentColor"
            stroke-width="1.6"
          />
          <circle cx="10" cy="10" r="2.6" fill="currentColor" />
        </template>
        <template v-else>
          <path
            d="M7 7 4.5 9H2v3h2.5L7 14V7Zm3 .5v6m3-9v12m3-10v8"
            stroke="currentColor"
            stroke-width="1.7"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </template>
      </svg>
      {{ badge.text }}
    </span>
  </div>
</template>
<style scoped>
.content-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin: 7px 0;
}
.content-badges.compact {
  margin: 4px 0;
}
.content-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  max-width: 100%;
  padding: 2px 6px;
  border: 1px solid;
  border-radius: 5px;
  font-size: 10px;
  font-weight: 600;
  line-height: 1.4;
  white-space: nowrap;
}
.content-badge svg {
  flex-shrink: 0;
}
.charging {
  color: #98470b;
  background: #fff3e5;
  border-color: #f0ceaa;
}
.vision {
  color: #7040aa;
  background: #f6efff;
  border-color: #dfc9f1;
}
.atmos {
  color: #146a78;
  background: #eaf7fa;
  border-color: #b9dfe5;
}
</style>

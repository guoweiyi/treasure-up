<script setup lang="ts">
import { ref, watch } from 'vue';
import { api } from '../api';
import type { Creator } from '../types';
const props = defineProps<{ creators: Creator[] }>();
const details = ref<Record<string, Creator>>({});
let request = 0;
watch(
  () => props.creators,
  async (creators) => {
    const key = ++request;
    details.value = {};
    const results = await Promise.allSettled(
      creators.map((creator) => api<Creator>(`/creators/${creator.id}`)),
    );
    if (key !== request) return;
    for (const result of results)
      if (result.status === 'fulfilled') details.value[result.value.id] = result.value;
  },
  { immediate: true },
);
</script>
<template>
  <section class="watch-up-section">
    <div v-for="creator in creators" :key="creator.id" class="watch-up">
      <RouterLink :to="`/creators/${creator.id}`"
        ><img v-if="creator.avatar_url" :src="creator.avatar_url" alt="" class="avatar" /><span
          v-else
          class="avatar fallback"
          >{{ creator.name?.slice(0, 1) }}</span
        ></RouterLink
      >
      <div class="watch-up-info">
        <RouterLink :to="`/creators/${creator.id}`" class="watch-up-name"
          >{{ creator.name }}<span class="up-label">UP</span></RouterLink
        >
        <p class="clamp-two">
          {{
            details[creator.id]?.description ||
            (creator.role === 'staff' ? '合作 UP 主' : '投稿 UP 主')
          }}
        </p>
        <RouterLink :to="`/creators/${creator.id}`" class="up-space-link"
          >查看已收藏作品<span v-if="details[creator.id]?.saved_count != null">
            · {{ details[creator.id]?.saved_count }}</span
          ></RouterLink
        >
      </div>
    </div>
    <p v-if="!creators.length" class="muted small">UP 主资料待补全</p>
  </section>
</template>

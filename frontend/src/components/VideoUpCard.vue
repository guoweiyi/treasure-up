<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { api } from '../api';
import type { Creator } from '../types';
import type { VideoCreator } from '../player/mediaInfo';
const props = defineProps<{ creators: VideoCreator[] }>();
const people = computed(() => {
  const unique = new Map<string, VideoCreator & { roles: string[] }>();
  for (const creator of props.creators) {
    const entry = unique.get(creator.id) || { ...creator, roles: [] };
    const role =
      creator.role_title?.trim() || (creator.role === 'staff' ? '联合投稿' : '投稿 UP 主');
    if (!entry.roles.includes(role)) entry.roles.push(role);
    unique.set(creator.id, entry);
  }
  return [...unique.values()];
});
const details = ref<Record<string, Creator>>({});
let request = 0;
watch(
  people,
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
    <h2 v-if="people.length > 1" class="joint-creators-heading">
      联合投稿 <span>{{ people.length }} 位创作者</span>
    </h2>
    <div v-for="creator in people" :key="creator.id" class="watch-up">
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
        <div class="creator-contribution">{{ creator.roles.join(' · ') }}</div>
        <p v-if="details[creator.id]?.description" class="clamp-two">
          {{ details[creator.id]?.description }}
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

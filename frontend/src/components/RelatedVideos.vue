<script setup lang="ts">
import { ref, watch } from 'vue';
import { api, query, duration, errorText } from '../api';
import type { Video, Page } from '../types';
import { count } from '../utils/format';
import UiIcon from './UiIcon.vue';
const props = defineProps<{ video: Video }>();
const items = ref<Video[]>([]),
  error = ref(''),
  busy = ref(false),
  sameCreator = ref(false);
let request = 0;
async function load() {
  const key = ++request;
  busy.value = true;
  error.value = '';
  items.value = [];
  const creator = props.video.creators[0];
  try {
    let data = creator
      ? await api<Page<Video>>(
          `/videos?${query({ view: 'card', creator_id: creator.id, page_size: 7, sort: 'newest' })}`,
        )
      : null;
    if (key !== request) return;
    let related = data?.items.filter((item) => item.id !== props.video.id) || [];
    sameCreator.value = related.length > 0;
    if (!related.length) {
      data = await api<Page<Video>>('/videos?view=card&page_size=7&sort=newest');
      if (key !== request) return;
      related = data.items.filter((item) => item.id !== props.video.id);
    }
    items.value = related.slice(0, 6);
  } catch (e) {
    if (key === request) error.value = errorText(e);
  } finally {
    if (key === request) busy.value = false;
  }
}
watch(() => props.video.id, load, { immediate: true });
</script>
<template>
  <section class="related-videos">
    <h2>相关推荐</h2>
    <p v-if="items.length" class="muted small">
      {{ sameCreator ? '这位 UP 的其他已保存作品' : '视频库里的最近收藏' }}
    </p>
    <p v-if="busy" class="muted small">正在读取…</p>
    <p v-else-if="error" class="small muted">
      {{ error }} <button class="text-button" @click="load">重试</button>
    </p>
    <p v-else-if="!items.length" class="muted small">还没有其他已保存视频</p>
    <article v-for="item in items" :key="item.id" class="related-card">
      <RouterLink :to="`/videos/${item.id}`" class="cover-link"
        ><img v-if="item.cover_url" :src="item.cover_url" alt="" loading="lazy" />
        <div v-else class="cover-placeholder"><UiIcon name="play" /></div>
        <span class="duration">{{ duration(item.duration) }}</span></RouterLink
      >
      <div>
        <RouterLink :to="`/videos/${item.id}`" class="video-title">{{ item.title }}</RouterLink
        ><RouterLink
          v-if="item.creators[0]"
          :to="`/creators/${item.creators[0].id}`"
          class="related-up"
          ><span class="up-label">UP</span>{{ item.creators[0].name }}</RouterLink
        ><span v-if="item.stats?.view != null" class="related-views"
          ><UiIcon name="play" />{{ count(item.stats.view) }}</span
        >
      </div>
    </article>
  </section>
</template>

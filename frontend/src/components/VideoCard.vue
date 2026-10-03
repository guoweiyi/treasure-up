<script setup lang="ts">
import type { Video } from '../types';
import { duration, statusText } from '../api';
defineProps<{ video: Video }>();
</script>
<template>
  <article class="video-card">
    <RouterLink :to="`/videos/${video.id}`" class="cover-link"
      ><img
        v-if="video.cover_url"
        :src="video.cover_url"
        alt=""
        loading="lazy"
        referrerpolicy="same-origin"
      />
      <div v-else class="cover-placeholder"><span>尚无封面</span></div>
      <span class="duration">{{ duration(video.duration) }}</span
      ><span v-if="!video.playable" class="cover-state">{{ statusText(video.capture_status) }}</span
      ><span v-if="video.starred" class="card-star" aria-label="已星标">★</span></RouterLink
    ><RouterLink :to="`/videos/${video.id}`" class="video-title">{{ video.title }}</RouterLink>
    <div class="video-byline">
      <RouterLink v-if="video.creators?.length" :to="`/creators/${video.creators[0]!.id}`">{{
        video.creators[0]!.name
      }}</RouterLink
      ><span v-else>UP 主资料待补全</span
      ><span v-if="video.parts_count > 1">{{ video.parts_count }} P</span>
    </div>
  </article>
</template>

<script setup lang="ts">
import type { VideoDetail } from '../player/mediaInfo';
import { computed } from 'vue';
import { playlistQuery, type PlaylistScope } from '../player/queue';
import { duration, statusText } from '../api';
import { count, shortDate } from '../utils/format';
import UiIcon from './UiIcon.vue';
const props = defineProps<{ video: VideoDetail; playlist?: PlaylistScope | null }>();
const destination = computed(() => ({
  path: `/videos/${props.video.id}`,
  query: playlistQuery(props.playlist || null),
}));
</script>
<template>
  <article class="video-card">
    <RouterLink :to="destination" class="cover-link"
      ><img
        v-if="video.cover_url"
        :src="video.cover_url"
        alt=""
        loading="lazy"
        referrerpolicy="same-origin"
      />
      <div v-else class="cover-placeholder"><UiIcon name="play" /><span>暂无封面</span></div>
      <div
        v-if="video.stats?.view != null || video.stats?.danmaku != null"
        class="cover-statistics"
      >
        <span v-if="video.stats?.view != null"
          ><UiIcon name="play" />{{ count(video.stats.view) }}</span
        ><span v-if="video.stats?.danmaku != null"
          ><UiIcon name="danmaku" />{{ count(video.stats.danmaku) }}</span
        >
      </div>
      <span class="duration">{{ duration(video.duration) }}</span
      ><span v-if="!video.playable" class="cover-state">{{ statusText(video.capture_status) }}</span
      ><span v-if="video.starred" class="card-star" aria-label="已星标">★</span></RouterLink
    ><RouterLink :to="destination" class="video-title">{{ video.title }}</RouterLink>
    <div v-if="video.content_features" class="media-feature-badges card-features">
      <span v-if="video.content_features.charging_exclusive">充电专属</span
      ><span v-if="video.content_features.dolby_vision">Dolby Vision</span
      ><span v-if="video.content_features.dolby_atmos">Dolby Atmos</span>
    </div>
    <div class="video-byline">
      <RouterLink v-if="video.creators?.length" :to="`/creators/${video.creators[0]!.id}`"
        ><span class="up-label">UP</span>{{ video.creators[0]!.name }}</RouterLink
      ><span v-else>UP 主资料待补全</span
      ><span :title="video.published_at ? '发布时间' : '归档时间'">{{
        video.parts_count > 1
          ? `${video.parts_count} P`
          : shortDate(video.published_at || video.created_at)
      }}</span>
    </div>
  </article>
</template>

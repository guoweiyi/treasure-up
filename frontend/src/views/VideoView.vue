<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { api, write, errorText, duration, statusText, session, date } from '../api';
import type { Video } from '../types';
import ArchivePlayer from '../components/ArchivePlayer.vue';
import CommentsPanel from '../components/CommentsPanel.vue';
import EmptyState from '../components/EmptyState.vue';
import RelatedVideos from '../components/RelatedVideos.vue';
import VideoUpCard from '../components/VideoUpCard.vue';
import UiIcon from '../components/UiIcon.vue';
import { count, shortDate } from '../utils/format';
const route = useRoute(),
  video = ref<Video | null>(null),
  partId = ref(''),
  error = ref(''),
  actionError = ref(''),
  busy = ref(false);
let seq = 0;
const part = computed(() => video.value?.parts?.find((p) => p.id === partId.value));
const canEdit = computed(() => ['admin', 'editor'].includes(session.user?.role || ''));
const description = computed(() => {
  const text = video.value?.description?.trim();
  return text && text !== '-' ? text : '';
});
async function load() {
  const n = ++seq;
  video.value = null;
  error.value = '';
  try {
    const data = await api<Video>(`/videos/${route.params.id}`);
    if (n !== seq) return;
    video.value = data;
    partId.value =
      data.parts?.find((p) => p.id === route.query.part)?.id || data.parts?.[0]?.id || '';
  } catch (e) {
    if (n === seq) error.value = errorText(e);
  }
}
async function star() {
  if (!video.value) return;
  busy.value = true;
  actionError.value = '';
  try {
    await write(`/admin/videos/${video.value.id}`, { starred: !video.value.starred }, 'PATCH');
    video.value.starred = !video.value.starred;
  } catch (e) {
    actionError.value = errorText(e);
  } finally {
    busy.value = false;
  }
}
watch(() => route.params.id, load, { immediate: true });
</script>
<template>
  <main class="content-shell watch-shell">
    <EmptyState v-if="error" title="视频暂时无法读取" :text="error" error
      ><button @click="load">重试</button></EmptyState
    >
    <div v-else-if="!video" class="loading-block">正在加载视频…</div>
    <div v-else class="watch-layout">
      <div class="watch-main">
        <header class="watch-heading">
          <h1>{{ video.title }}</h1>
          <div class="video-meta">
            <span v-if="video.stats?.view != null"
              ><UiIcon name="play" />{{ count(video.stats.view) }} 播放</span
            >
            <span v-if="video.stats?.danmaku != null"
              ><UiIcon name="danmaku" />{{ count(video.stats.danmaku) }} 弹幕</span
            >
            <span>保存于 {{ shortDate(video.created_at) }}</span>
            <span>{{ video.bvid }}</span>
          </div>
        </header>
        <ArchivePlayer
          v-if="part?.variants?.length"
          :key="video.id"
          :part="part"
          :poster="video.cover_url"
          :media-properties="video.media_properties"
        />
        <div v-else class="unavailable-player">
          <UiIcon name="play" /><span>此分 P 还没有可播放的归档</span>
          <p>{{ statusText(video.capture_status) }}</p>
        </div>
        <div class="video-actionbar">
          <div
            class="source-statistics"
            :title="
              video.stats?.observed_at
                ? 'B 站统计快照：' + date(video.stats.observed_at)
                : 'B 站归档统计'
            "
          >
            <span v-if="video.stats?.like != null"
              ><UiIcon name="like" />{{ count(video.stats.like) }}</span
            >
            <span v-if="video.stats?.coin != null"
              ><UiIcon name="coin" />{{ count(video.stats.coin) }}</span
            >
            <span v-if="video.stats?.favorite != null"
              ><UiIcon name="folder" />{{ count(video.stats.favorite) }}</span
            >
            <span v-if="video.stats?.share != null"
              ><UiIcon name="share" />{{ count(video.stats.share) }}</span
            >
            <span v-if="video.stats?.observed_at" class="statistics-note">B 站统计快照</span>
          </div>
          <button
            v-if="canEdit"
            :disabled="busy"
            class="star-button"
            :aria-pressed="video.starred"
            @click="star"
          >
            <UiIcon name="star" />{{ video.starred ? '已星标' : '星标' }}
          </button>
          <RouterLink
            v-if="canEdit"
            :to="{ path: '/admin/videos', query: { edit: video.id } }"
            class="video-edit-link"
            >编辑标注</RouterLink
          >
        </div>
        <p v-if="actionError" class="form-error" role="alert">{{ actionError }}</p>
        <details
          v-if="
            video.source_state &&
            !['available', 'normal', 'active', 'unknown'].includes(video.source_state)
          "
          class="source-state-note archive-information"
        >
          <summary>归档信息</summary>
          <p>{{ statusText(video.source_state) }}，本地归档保留</p>
        </details>
        <details v-if="description" class="video-description" open>
          <summary>视频简介</summary>
          <p class="preserve-lines">{{ description }}</p>
        </details>
        <div v-if="video.tags?.length" class="tags">
          <RouterLink v-for="tag in video.tags" :key="tag" :to="{ path: '/', query: { tag } }">{{
            tag
          }}</RouterLink>
        </div>
        <div v-if="video.notes" class="personal-note">
          <h3>收藏笔记</h3>
          <p class="preserve-lines">{{ video.notes }}</p>
        </div>
        <p
          v-if="['partial', 'blocked', 'failed'].includes(video.capture_status)"
          class="inline-notice"
        >
          这条视频的归档{{ statusText(video.capture_status) }}，部分弹幕、评论或分 P 可能尚未保存。
        </p>
        <CommentsPanel :video-id="video.id" />
      </div>
      <aside class="watch-sidebar">
        <VideoUpCard :creators="video.creators" />
        <section class="sidebar-section parts-section">
          <h2>
            视频选集 <span>{{ part?.position || 1 }} / {{ video.parts?.length || 0 }}</span>
          </h2>
          <div class="part-list">
            <button
              v-for="item in video.parts"
              :key="item.id"
              :class="{ selected: item.id === partId }"
              @click="partId = item.id"
            >
              <span class="part-number">P{{ item.position }}</span
              ><span class="part-name"
                >{{ item.title || 'P' + item.position
                }}<small v-if="!item.variants?.length">待归档</small></span
              ><span class="part-duration">{{ duration(item.duration) }}</span>
            </button>
          </div>
        </section>
        <RelatedVideos :video="video" />
      </aside>
    </div>
  </main>
</template>

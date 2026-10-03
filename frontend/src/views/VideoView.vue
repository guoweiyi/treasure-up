<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { api, write, errorText, duration, statusText, session } from '../api';
import type { Video } from '../types';
import ArchivePlayer from '../components/ArchivePlayer.vue';
import CommentsPanel from '../components/CommentsPanel.vue';
import EmptyState from '../components/EmptyState.vue';
const route = useRoute(),
  video = ref<Video | null>(null),
  partId = ref(''),
  error = ref(''),
  actionError = ref(''),
  busy = ref(false);
let seq = 0;
const part = computed(() => video.value?.parts?.find((p) => p.id === partId.value));
const unsupportedHdr = computed(() =>
  part.value?.variants.some(
    (variant) =>
      variant.kind === 'archive' &&
      video.value?.media_properties?.[variant.id]?.compatibility === 'hdr_conversion_unsupported',
  ),
);
const canEdit = computed(() => ['admin', 'editor'].includes(session.user?.role || ''));
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
    <RouterLink class="back-link" to="/">← 视频库</RouterLink
    ><EmptyState v-if="error" title="视频暂时无法读取" :text="error" error
      ><button @click="load">重试</button></EmptyState
    >
    <div v-else-if="!video" class="loading-block">正在加载视频…</div>
    <template v-else
      ><div class="watch-layout">
        <div class="watch-main">
          <ArchivePlayer
            v-if="part?.variants?.length"
            :key="video.id"
            :part="part"
            :poster="video.cover_url"
          />
          <div v-else class="unavailable-player">
            <span>此分 P 还没有可播放的归档</span>
            <p>{{ statusText(video.capture_status) }}</p>
          </div>
          <p v-if="unsupportedHdr" class="inline-notice" role="status">
            原档已保存，暂不支持生成 HDR 浏览器兼容副本。
          </p>
          <div class="video-heading">
            <h1>{{ video.title }}</h1>
            <button
              v-if="canEdit"
              :disabled="busy"
              class="star-button"
              :aria-pressed="video.starred"
              @click="star"
            >
              {{ video.starred ? '★ 已星标' : '☆ 星标' }}
            </button>
          </div>
          <p v-if="actionError" class="form-error" role="alert">{{ actionError }}</p>
          <div class="video-meta">
            <span>{{ video.bvid }}</span
            ><span>{{ duration(video.duration) }}</span
            ><span
              v-if="
                video.source_state &&
                !['available', 'normal', 'active', 'unknown'].includes(video.source_state)
              "
              >{{ statusText(video.source_state) }}，本地归档保留</span
            ><RouterLink v-if="canEdit" :to="{ path: '/admin/videos', query: { edit: video.id } }"
              >编辑标注</RouterLink
            >
          </div>
          <div v-if="video.tags?.length" class="tags">
            <RouterLink v-for="tag in video.tags" :key="tag" :to="{ path: '/', query: { tag } }">{{
              tag
            }}</RouterLink>
          </div>
          <details class="video-description" open>
            <summary>简介</summary>
            <p class="preserve-lines">{{ video.description || '暂无简介' }}</p>
          </details>
          <div v-if="video.notes" class="personal-note">
            <h3>收藏笔记</h3>
            <p class="preserve-lines">{{ video.notes }}</p>
          </div>
          <p
            v-if="['partial', 'blocked', 'failed'].includes(video.capture_status)"
            class="inline-notice"
          >
            这条视频的归档{{ statusText(video.capture_status) }}，部分弹幕、评论或分 P
            可能尚未保存。
          </p>
          <CommentsPanel :video-id="video.id" />
        </div>
        <aside class="watch-sidebar">
          <section class="sidebar-section">
            <h2>UP 主</h2>
            <RouterLink
              v-for="creator in video.creators"
              :key="creator.id"
              :to="`/creators/${creator.id}`"
              class="creator-mini"
              ><img
                v-if="creator.avatar_url"
                :src="creator.avatar_url"
                alt=""
                class="avatar"
              /><span v-else class="avatar fallback">{{ creator.name?.slice(0, 1) }}</span>
              <div>
                <strong>{{ creator.name }}</strong
                ><span class="muted small">{{
                  creator.role === 'staff' ? '合作 UP 主' : '投稿 UP 主'
                }}</span>
              </div></RouterLink
            >
            <p v-if="!video.creators.length" class="muted">资料待补全</p>
          </section>
          <section class="sidebar-section">
            <h2>
              视频分 P <span class="muted">{{ video.parts?.length || 0 }}</span>
            </h2>
            <div class="part-list">
              <button
                v-for="item in video.parts"
                :key="item.id"
                :class="{ selected: item.id === partId }"
                @click="partId = item.id"
              >
                <span class="part-number">{{ item.position }}</span
                ><span
                  >{{ item.title || `P${item.position}`
                  }}<small
                    >{{ duration(item.duration)
                    }}{{ !item.variants?.length ? ' · 待归档' : '' }}</small
                  ></span
                >
              </button>
            </div>
          </section>
        </aside>
      </div></template
    >
  </main>
</template>

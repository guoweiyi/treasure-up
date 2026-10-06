<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { api, query } from '../api';
import type { Comment, Page } from '../types';
import CommentThread from './CommentThread.vue';
import { createCommentFeed, emptyCommentFeed } from '../utils/commentFeed';
import type { CommentPlayback, CommentSeek } from '../utils/commentTimeline';
const props = defineProps<{ videoId: string; playback?: CommentPlayback }>();
const emit = defineEmits<{ seek: [value: CommentSeek] }>();
const state = reactive(emptyCommentFeed<Comment>());
const q = ref(''),
  appliedQuery = ref(''),
  sentinel = ref<HTMLElement>();
const feed = createCommentFeed<Comment>(state, ({ videoId, ...params }, signal) =>
  api<Page<Comment>>(`/videos/${videoId}/comments?${query(params)}`, { signal }),
);
let observer: IntersectionObserver | undefined;
function search() {
  appliedQuery.value = q.value.trim();
  void feed.reset({ videoId: props.videoId, q: appliedQuery.value });
}
watch(
  () => props.videoId,
  () => {
    q.value = '';
    search();
  },
  { immediate: true },
);
onMounted(() => {
  if (typeof IntersectionObserver === 'undefined') return;
  observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting) && !state.error) void feed.more();
    },
    { rootMargin: '240px 0px' },
  );
  if (sentinel.value) observer.observe(sentinel.value);
});
watch(
  () => state.loading,
  async (loading) => {
    if (loading || !state.hasMore || state.error) return;
    await nextTick();
    if (sentinel.value && observer) {
      observer.unobserve(sentinel.value);
      observer.observe(sentinel.value);
    }
  },
);
onBeforeUnmount(() => {
  observer?.disconnect();
  observer = undefined;
  feed.dispose();
});
</script>
<template>
  <section class="comments-panel" :aria-busy="state.loading">
    <div class="section-heading comments-heading">
      <h2 title="评论和点赞数为归档时快照。展开回复只读取本地保存的内容。">
        {{ appliedQuery ? '评论搜索结果' : '评论' }} <span class="muted">{{ state.total }}</span
        ><small class="comment-sort-label">置顶优先 · 按热度</small>
      </h2>
      <form class="search-form compact" @submit.prevent="search">
        <input
          v-model="q"
          type="search"
          placeholder="搜索此视频的评论"
          aria-label="搜索评论"
        /><button type="submit" aria-label="搜索评论内容" title="搜索评论">
          <svg
            width="17"
            height="17"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            stroke-linecap="round"
            aria-hidden="true"
          >
            <circle cx="10.5" cy="10.5" r="6.5" />
            <path d="m16 16 4 4" />
          </svg>
        </button>
      </form>
    </div>
    <CommentThread
      v-for="comment in state.items"
      :key="comment.id"
      :comment="comment"
      :video-id="videoId"
      :playback="playback"
      @seek="emit('seek', $event)"
      :reply="
        !!comment.root_rpid && comment.root_rpid !== '0' && comment.root_rpid !== comment.rpid
      "
    />
    <p v-if="!state.loading && !state.error && !state.items.length" class="quiet-empty">
      {{ appliedQuery ? '没有匹配的已保存评论。' : '暂无已保存评论。' }}
    </p>
    <div ref="sentinel" class="comment-feed-footer" aria-live="polite">
      <p v-if="state.error" class="form-error" role="alert">{{ state.error }}</p>
      <button
        v-if="state.hasMore || state.error"
        type="button"
        :disabled="state.loading"
        @click="feed.more"
      >
        {{ state.loading ? '正在加载评论…' : state.error ? '重新加载' : '加载更多评论' }}
      </button>
      <span v-if="state.items.length && !state.hasMore" class="muted small">没有更多评论了</span>
    </div>
  </section>
</template>
<style scoped>
.comments-heading {
  align-items: center;
  flex-wrap: wrap;
  gap: 14px;
  margin-bottom: 4px;
}
.comments-heading h2 {
  display: flex;
  align-items: baseline;
  gap: 10px;
  white-space: nowrap;
  font-weight: 600;
}
.comments-heading h2 > span {
  font-size: 15px;
  font-weight: 400;
  font-variant-numeric: tabular-nums;
}
.comment-sort-label {
  margin-left: 8px;
  padding-left: 14px;
  border-left: 1px solid #e7e9ec;
  font-size: 12px;
  font-weight: 400;
  color: #9499a0;
}
.comments-heading .search-form {
  display: flex;
  width: 260px;
  max-width: 100%;
  gap: 0;
  border: 1px solid #edf0f2;
  border-radius: 7px;
  background: #f6f7f8;
  overflow: hidden;
}
.comments-heading .search-form:focus-within {
  border-color: var(--accent);
  background: #fff;
}
.comments-heading .search-form input {
  flex: 1;
  min-width: 0;
  width: 0;
  border: 0;
  background: transparent;
  padding: 8px 10px;
  font-size: 12px;
  box-shadow: none;
  outline: none;
}
.comments-heading .search-form button {
  display: grid;
  place-items: center;
  flex: 0 0 36px;
  padding: 0;
  min-height: 34px;
  border: 0;
  border-radius: 0;
  color: #9499a0;
  background: transparent;
}
.comments-heading .search-form button:hover {
  color: var(--accent);
}
.comment-feed-footer {
  display: flex;
  min-height: 56px;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 20px 0;
}
.comment-feed-footer > button {
  min-width: 144px;
  padding: 8px 18px;
  border: 1px solid #edf0f2;
  border-radius: 7px;
  background: #f7f8fa;
  color: #61666d;
  font-size: 12px;
}
.comment-feed-footer > button:hover {
  color: var(--accent);
  border-color: #c5cecf;
}
.comment-feed-footer .form-error {
  width: 100%;
  text-align: center;
  margin: 0;
}
@media (max-width: 600px) {
  .comments-heading .search-form {
    width: 100%;
  }
}
</style>

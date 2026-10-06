<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue';
import { api, query, date } from '../api';
import type { Comment, Page } from '../types';
import { createCommentFeed, emptyCommentFeed } from '../utils/commentFeed';
import { commentGalleryImages } from '../utils/commentGallery';
import CommentContent from './CommentContent.vue';
import CommentImagePreview from './CommentImagePreview.vue';
import type { CommentPlayback, CommentSeek } from '../utils/commentTimeline';
const props = defineProps<{
  comment: Comment;
  videoId: string;
  reply?: boolean;
  playback?: CommentPlayback;
}>();
const emit = defineEmits<{ seek: [value: CommentSeek] }>();
const open = ref(false);
const previewIndex = ref<number | null>(null);
const state = reactive(emptyCommentFeed<Comment>());
const feed = createCommentFeed<Comment>(state, ({ videoId, ...params }, signal) =>
  api<Page<Comment>>(`/videos/${videoId}/comments?${query(params)}`, { signal }),
);
watch(
  () => [props.videoId, props.comment.rpid],
  () => {
    open.value = false;
    previewIndex.value = null;
    void feed.reset({ videoId: props.videoId, root: props.comment.rpid }, false);
  },
  { immediate: true },
);
onBeforeUnmount(feed.dispose);
async function expand() {
  if (open.value) {
    open.value = false;
    return;
  }
  open.value = true;
  if (!state.page) await feed.more();
}
const images = computed(() => commentGalleryImages(props.comment.images));
const replies = computed(() =>
  open.value && state.page ? state.items : props.comment.preview_replies || [],
);
const savedReplyCount = computed(
  () => props.comment.saved_reply_count ?? props.comment.reply_count,
);
</script>
<template>
  <article class="comment" :class="{ reply }">
    <img
      v-if="comment.author?.avatar_url"
      :src="comment.author.avatar_url"
      class="avatar comment-avatar"
      alt=""
      loading="lazy"
    /><span v-else class="avatar comment-avatar fallback">{{
      comment.author?.name?.slice(0, 1) || '?'
    }}</span>
    <div class="comment-body">
      <div
        class="comment-author"
        :title="comment.author?.uid ? `UID ${comment.author.uid}` : undefined"
      >
        <RouterLink
          v-if="comment.author?.creator_id"
          :to="`/creators/${comment.author.creator_id}`"
          >{{ comment.author.name || '未知作者' }}</RouterLink
        ><span v-else>{{ comment.author?.name || '未知作者' }}</span>
        <span v-if="comment.is_uploader" class="comment-badge uploader" title="视频投稿作者"
          >UP 主</span
        >
        <span v-if="comment.is_pinned" class="comment-badge pinned" title="采集时的置顶评论"
          >置顶</span
        >
      </div>
      <CommentContent
        :content="comment.content"
        :emotes="comment.emotes"
        :playback="playback"
        @seek="emit('seek', $event)"
      />
      <div v-if="images.length" class="comment-images">
        <button
          v-for="(image, index) in images"
          :key="image"
          type="button"
          class="comment-image-button"
          :aria-label="`预览评论附图 ${index + 1}`"
          @click="previewIndex = index"
        >
          <img
            :src="image"
            alt="评论附图"
            loading="lazy"
            decoding="async"
            width="180"
            height="140"
          />
        </button>
      </div>
      <div class="comment-meta">
        <time :datetime="comment.posted_at || undefined">{{ date(comment.posted_at) }}</time>
        <span
          class="comment-likes"
          title="归档时的点赞数"
          :aria-label="`归档点赞 ${comment.like_count}`"
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.6"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path
              d="M7 10v11H3V10zm0 0 5-7c.5-.7 1.5-.5 1.5.5V9H19a2 2 0 0 1 2 2.4l-1.5 7.8A2 2 0 0 1 17.5 21H7"
            />
          </svg>
          {{ comment.like_count.toLocaleString() }}
        </span>
        <button
          v-if="!reply && savedReplyCount > (comment.preview_replies?.length || 0)"
          type="button"
          class="text-button comment-reply-button"
          :aria-expanded="open"
          title="查看已保存的回复"
          @click="expand"
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.6"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path
              d="M20 4H4a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h4l4 3v-3h8a1 1 0 0 0 1-1V5a1 1 0 0 0-1-1Z"
            />
            <path d="M7 9h10M7 13h6" />
          </svg>
          {{ open ? '收起回复' : `查看全部 ${savedReplyCount.toLocaleString()} 条回复` }}
        </button>
      </div>
      <div v-if="!reply && (open || replies.length)" class="replies">
        <CommentThread
          v-for="item in replies"
          :key="item.id"
          :comment="item"
          :video-id="videoId"
          :playback="playback"
          @seek="emit('seek', $event)"
          reply
        />
        <p v-if="open && state.error" class="form-error" role="alert">
          {{ state.error }} <button type="button" @click="feed.more">重试</button>
        </p>
        <p v-if="open && !state.loading && !replies.length && !state.error" class="muted small">
          尚未保存这层回复。
        </p>
        <button
          v-if="open && state.hasMore && !state.error"
          type="button"
          :disabled="state.loading"
          @click="feed.more"
        >
          {{ state.loading ? '正在加载…' : '加载更多回复' }}
        </button>
        <p v-else-if="open && state.loading" class="muted">正在读取回复…</p>
      </div>
    </div>
    <CommentImagePreview
      v-if="previewIndex !== null"
      :images="images"
      :initial-index="previewIndex"
      @close="previewIndex = null"
    />
  </article>
</template>
<style scoped>
.comment {
  gap: 15px;
  padding: 25px 0;
  border-bottom-color: #edf0f2;
}
.comment-author {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 7px;
  min-height: 20px;
  color: #61666d;
  font-weight: 500;
  font-size: 13px;
}
.comment-badge {
  display: inline-flex;
  align-items: center;
  padding: 0 4px;
  border-radius: 3px;
  font-size: 10px;
  line-height: 16px;
  font-weight: 500;
}
.comment-badge.uploader {
  color: #b64a64;
  background: #fceaf0;
}
.comment-badge.pinned {
  color: #647a80;
  border: 1px solid #c7d5d8;
  line-height: 14px;
}
.comment-author a {
  color: inherit;
  text-decoration: none;
}
.comment-author a:hover {
  color: var(--accent);
}
.comment-images {
  gap: 9px;
  margin: 12px 0 14px;
}
.comment-image-button {
  display: block;
  padding: 0;
  border: 1px solid #edf0f2;
  background: #f6f7f8;
  border-radius: 7px;
  overflow: hidden;
  cursor: zoom-in;
}
.comment-image-button:hover {
  border-color: #c5cecf;
  background: #f6f7f8;
}
.comment-image-button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 3px;
}
.comment-image-button img {
  display: block;
  width: 156px;
  height: 124px;
  max-width: 100%;
  max-height: none;
  object-fit: cover;
  border-radius: 0;
}
.comment-meta {
  gap: 20px;
  font-size: 12px;
  line-height: 20px;
  color: #9499a0;
}
.comment-meta time {
  font-variant-numeric: tabular-nums;
}
.comment-likes,
.comment-reply-button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.comment-likes {
  cursor: default;
}
.comment-meta .comment-reply-button {
  padding: 0;
  border: 0;
  color: inherit;
  background: transparent;
  font-weight: 400;
  line-height: 20px;
  min-height: 0;
}
.comment-meta .comment-reply-button:hover {
  color: var(--accent);
}
.replies {
  padding: 0 14px;
  margin-top: 15px;
  background: #f7f8fa;
  border-radius: 8px;
}
.comment.reply {
  padding: 15px 0;
  gap: 10px;
  border-bottom-color: #e9edf0;
}
.comment.reply .comment-avatar {
  width: 30px;
  height: 30px !important;
  font-size: 13px !important;
}
@media (max-width: 600px) {
  .comment {
    gap: 10px;
    padding: 20px 0;
  }
  .comment-meta {
    gap: 12px;
    font-size: 11px;
  }
  .comment-image-button img {
    width: 124px;
    height: 106px;
  }
  .comment-image-button {
    max-width: calc(50% - 5px);
  }
  .replies {
    padding: 0 10px;
  }
}
</style>

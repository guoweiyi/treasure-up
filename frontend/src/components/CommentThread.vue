<script setup lang="ts">
import { ref } from 'vue';
import { api, errorText, date } from '../api';
import type { Comment, Page } from '../types';
const props = defineProps<{ comment: Comment; videoId: string; reply?: boolean }>();
const open = ref(false),
  items = ref<Comment[]>([]),
  page = ref(0),
  total = ref(0),
  error = ref(''),
  busy = ref(false);
async function expand() {
  if (open.value) {
    open.value = false;
    return;
  }
  open.value = true;
  if (!page.value) await load();
}
async function load() {
  if (busy.value) return;
  busy.value = true;
  error.value = '';
  try {
    const data = await api<Page<Comment>>(
      `/videos/${props.videoId}/comments?root=${encodeURIComponent(props.comment.rpid)}&page=${page.value + 1}&page_size=20`,
    );
    items.value.push(
      ...data.items.filter((item) => !items.value.some((existing) => existing.id === item.id)),
    );
    page.value = data.page;
    total.value = data.total;
  } catch (e) {
    error.value = errorText(e);
  } finally {
    busy.value = false;
  }
}
const imageUrl = (image: Comment['images'][number]) =>
  typeof image === 'string' ? image : image.url || image.asset_url;
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
      <div class="comment-author">
        <RouterLink
          v-if="comment.author?.creator_id"
          :to="`/creators/${comment.author.creator_id}`"
          >{{ comment.author.name || '未知作者' }}</RouterLink
        ><span v-else>{{ comment.author?.name || '未知作者' }}</span
        ><span v-if="comment.author?.uid" class="muted small">UID {{ comment.author.uid }}</span>
      </div>
      <p class="preserve-lines">{{ comment.content }}</p>
      <p
        v-if="
          reply &&
          comment.parent_rpid &&
          comment.parent_rpid !== comment.root_rpid &&
          comment.parent_rpid !== '0'
        "
        class="muted small"
      >
        回复评论 #{{ comment.parent_rpid }}
      </p>
      <div v-if="comment.images?.length" class="comment-images">
        <a
          v-for="(image, index) in comment.images"
          :key="index"
          :href="imageUrl(image)"
          target="_blank"
          rel="noopener noreferrer"
          ><img :src="imageUrl(image)" alt="评论附图" loading="lazy"
        /></a>
      </div>
      <div class="comment-meta">
        <time>{{ date(comment.posted_at) }}</time
        ><span>赞 {{ comment.like_count }}（采集时）</span
        ><button v-if="!reply && comment.reply_count" class="text-button" @click="expand">
          {{ open ? '收起回复' : `查看回复（来源记录 ${comment.reply_count} 条）` }}
        </button>
      </div>
      <div v-if="open" class="replies">
        <p v-if="page" class="muted small" style="padding-top: 14px; margin: 0">
          本站已保存 {{ total }} 条回复
        </p>
        <CommentThread
          v-for="item in items"
          :key="item.id"
          :comment="item"
          :video-id="videoId"
          reply
        />
        <p v-if="error" class="form-error" role="alert">
          {{ error }} <button @click="load">重试</button>
        </p>
        <p v-if="!busy && !items.length && !error" class="muted small">尚未保存这层回复。</p>
        <button v-if="page * 20 < total" :disabled="busy" @click="load">
          {{ busy ? '正在加载…' : '加载更多回复' }}
        </button>
        <p v-else-if="busy" class="muted">正在读取回复…</p>
      </div>
    </div>
  </article>
</template>

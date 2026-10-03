<script setup lang="ts">
import { ref, watch } from 'vue';
import { api, query, errorText } from '../api';
import type { Comment, Page } from '../types';
import CommentThread from './CommentThread.vue';
import Pagination from './Pagination.vue';
const props = defineProps<{ videoId: string }>();
const items = ref<Comment[]>([]),
  q = ref(''),
  total = ref(0),
  page = ref(1),
  error = ref(''),
  busy = ref(false);
let sequence = 0;
async function load(p = 1) {
  const n = ++sequence;
  page.value = p;
  busy.value = true;
  error.value = '';
  try {
    const data = await api<Page<Comment>>(
      `/videos/${props.videoId}/comments?${query({ q: q.value, page: p, page_size: 20 })}`,
    );
    if (n === sequence) {
      items.value = data.items;
      total.value = data.total;
    }
  } catch (e) {
    if (n === sequence) error.value = errorText(e);
  } finally {
    if (n === sequence) busy.value = false;
  }
}
watch(
  () => props.videoId,
  () => {
    q.value = '';
    void load();
  },
  { immediate: true },
);
</script>
<template>
  <section class="comments-panel">
    <div class="section-heading">
      <h2>
        {{ q ? '评论搜索结果' : '已保存的顶层评论' }} <span class="muted">{{ total }}</span>
      </h2>
      <form class="search-form compact" @submit.prevent="load()">
        <input v-model="q" placeholder="搜索此视频的评论" aria-label="搜索评论" /><button>
          搜索
        </button>
      </form>
    </div>
    <p class="muted small">评论和点赞数为归档时快照。展开回复只读取本地保存的内容。</p>
    <p v-if="error" class="form-error" role="alert">
      {{ error }} <button @click="load(page)">重试</button>
    </p>
    <div v-else-if="busy" class="loading-block">正在加载评论…</div>
    <template v-else
      ><CommentThread
        v-for="comment in items"
        :key="comment.id"
        :comment="comment"
        :video-id="videoId"
        :reply="
          !!comment.root_rpid && comment.root_rpid !== '0' && comment.root_rpid !== comment.rpid
        "
      />
      <p v-if="!items.length" class="quiet-empty">
        {{ q ? '没有匹配的已保存评论。' : '暂无已保存评论。' }}
      </p></template
    ><Pagination :page="page" :total="total" :page-size="20" @change="load" />
  </section>
</template>

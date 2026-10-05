<script setup lang="ts">
import { ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { api, query, errorText, session } from '../api';
import type { Video, Collection, Page } from '../types';
import VideoCard from '../components/VideoCard.vue';
import EmptyState from '../components/EmptyState.vue';
import Pagination from '../components/Pagination.vue';
const route = useRoute(),
  router = useRouter();
const items = ref<Video[]>([]),
  collections = ref<Collection[]>([]),
  total = ref(0),
  busy = ref(false),
  error = ref('');
const q = ref(''),
  collection = ref(''),
  tag = ref(''),
  starred = ref(false),
  sort = ref('newest'),
  page = ref(1);
let request = 0;
async function load() {
  const key = ++request;
  busy.value = true;
  error.value = '';
  q.value = String(route.query.q || '');
  collection.value = String(route.query.collection_id || '');
  tag.value = String(route.query.tag || '');
  starred.value = route.query.starred === 'true';
  sort.value = String(route.query.sort || 'newest');
  page.value = Math.max(1, Number(route.query.page) || 1);
  try {
    const data = await api<Page<Video>>(
      `/videos?${query({ view: 'card', q: q.value, collection_id: collection.value, tag: tag.value, starred: starred.value ? true : '', sort: sort.value, page: page.value, page_size: 24 })}`,
    );
    if (key === request) {
      items.value = data.items;
      total.value = data.total;
    }
  } catch (e) {
    if (key === request) error.value = errorText(e);
  } finally {
    if (key === request) busy.value = false;
  }
}
function search(next = 1) {
  if (starred.value && !session.user) {
    void router.push({ path: '/login', query: { next: '/?starred=true' } });
    return;
  }
  router.push({
    path: '/',
    query: {
      ...(q.value ? { q: q.value } : {}),
      ...(collection.value ? { collection_id: collection.value } : {}),
      ...(tag.value ? { tag: tag.value } : {}),
      ...(starred.value ? { starred: 'true' } : {}),
      sort: sort.value,
      page: String(next),
    },
  });
}
api<Page<Collection>>('/collections?page_size=100')
  .then((data) => (collections.value = data.items))
  .catch(() => {});
watch(() => route.fullPath, load, { immediate: true });
</script>
<template>
  <main class="content-shell library-shell">
    <div class="library-heading">
      <div class="content-tabs">
        <button
          :class="{ active: !starred }"
          @click="
            starred = false;
            search();
          "
        >
          全部视频</button
        ><button :class="{ active: starred }" @click="router.push('/saved')">我的片单</button>
      </div>
      <span class="muted small">{{ busy ? '正在读取…' : total + ' 个视频' }}</span>
    </div>
    <div v-if="collections.length" class="collection-chips">
      <button
        :class="{ selected: !collection }"
        @click="
          collection = '';
          search();
        "
      >
        全部来源
      </button>
      <button
        v-for="item in collections"
        :key="item.id"
        :class="{ selected: collection === item.id }"
        @click="
          collection = item.id;
          search();
        "
      >
        {{ item.kind === 'creator' ? 'UP · ' : '' }}{{ item.title }}
      </button>
    </div>
    <div class="library-tools">
      <p class="search-result-heading">{{ q ? '“' + q + '” 的搜索结果' : '已保存的视频' }}</p>
      <form class="filter-bar" @submit.prevent="search()">
        <select v-model="sort" aria-label="排序" @change="search()">
          <option value="newest">最近保存</option>
          <option value="published">最新发布</option>
          <option value="oldest">最早保存</option>
          <option value="title">按标题</option>
          <option value="duration">按时长</option>
        </select>
        <input
          v-model="tag"
          class="tag-filter"
          placeholder="筛选标签"
          aria-label="标签筛选"
          @change="search()"
        />
        <button
          v-if="q || collection || tag || starred"
          class="text-button"
          type="button"
          @click="router.push('/')"
        >
          清除筛选
        </button>
      </form>
    </div>
    <EmptyState v-if="error" title="视频库暂时无法读取" :text="error" error
      ><button @click="load">重试</button></EmptyState
    >
    <div v-else-if="busy" class="loading-block" role="status">正在加载…</div>
    <div v-else-if="items.length" class="video-grid">
      <VideoCard
        v-for="video in items"
        :key="video.id"
        :video="video"
        :playlist="collection ? { type: 'collection', id: collection } : undefined"
      />
    </div>
    <EmptyState
      v-else
      :title="q || collection || tag || starred ? '没有匹配的视频' : '视频库还是空的'"
      :text="
        q || collection || tag || starred
          ? '试试其他关键词，或调整筛选条件。'
          : '在管理中心添加收藏来源，归档完成的视频会出现在这里。'
      "
    />
    <Pagination :page="page" :total="total" @change="search" />
  </main>
</template>

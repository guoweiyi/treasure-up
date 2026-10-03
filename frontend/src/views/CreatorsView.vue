<script setup lang="ts">
import { ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { api, query, errorText } from '../api';
import type { Creator, Page } from '../types';
import EmptyState from '../components/EmptyState.vue';
import Pagination from '../components/Pagination.vue';
const route = useRoute(),
  router = useRouter(),
  q = ref(''),
  page = ref(1),
  total = ref(0),
  items = ref<Creator[]>([]),
  error = ref(''),
  busy = ref(false);
let seq = 0;
async function load() {
  const n = ++seq;
  q.value = String(route.query.q || '');
  page.value = Math.max(1, Number(route.query.page) || 1);
  busy.value = true;
  error.value = '';
  try {
    const data = await api<Page<Creator>>(
      `/creators?${query({ q: q.value, page: page.value, page_size: 24 })}`,
    );
    if (n === seq) {
      items.value = data.items;
      total.value = data.total;
    }
  } catch (e) {
    if (n === seq) error.value = errorText(e);
  } finally {
    if (n === seq) busy.value = false;
  }
}
function search(p = 1) {
  router.push({ path: '/creators', query: { q: q.value, page: String(p) } });
}
watch(() => route.fullPath, load, { immediate: true });
</script>
<template>
  <main class="content-shell">
    <div class="page-heading">
      <div>
        <h1>UP 主</h1>
        <p class="muted">从创作者，找到值得重看的内容</p>
      </div>
      <form class="search-form" @submit.prevent="search()">
        <input
          v-model="q"
          placeholder="搜索昵称、曾用名、UID 或简介"
          aria-label="搜索 UP 主"
        /><button class="primary">搜索</button>
      </form>
    </div>
    <EmptyState v-if="error" title="UP 目录暂时无法读取" :text="error" error
      ><button @click="load">重试</button></EmptyState
    >
    <div v-else-if="busy" class="loading-block">正在加载…</div>
    <div v-else-if="items.length" class="creator-grid">
      <RouterLink
        v-for="creator in items"
        :key="creator.id"
        :to="`/creators/${creator.id}`"
        class="creator-card"
        ><img
          v-if="creator.avatar_url"
          :src="creator.avatar_url"
          alt=""
          class="avatar large"
          loading="lazy"
        /><span v-else class="avatar large fallback">{{ creator.name?.slice(0, 1) || '?' }}</span>
        <div>
          <h2>{{ creator.name }}</h2>
          <p class="muted small">
            UID {{ creator.uid }} · 已保存 {{ creator.saved_count || 0 }} 条
          </p>
          <p class="clamp-two">{{ creator.description || '暂无简介' }}</p>
        </div></RouterLink
      >
    </div>
    <EmptyState
      v-else
      :title="q ? '没有找到这位 UP 主' : '还没有已归档的 UP 主'"
      text="UP 主会随视频归档自动收录。"
    /><Pagination :page="page" :total="total" @change="search" />
  </main>
</template>

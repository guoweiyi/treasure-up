<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { api, errorText } from '../api';
import type { Collection, Page } from '../types';
import EmptyState from '../components/EmptyState.vue';
import Pagination from '../components/Pagination.vue';
const items = ref<Collection[]>([]),
  error = ref(''),
  busy = ref(false),
  page = ref(1),
  total = ref(0);
const kinds: Record<string, string> = {
  favorite: '收藏夹',
  favorites: '收藏夹',
  ugc_season: '合集',
  series: '系列',
};
async function load(p = 1) {
  page.value = p;
  busy.value = true;
  error.value = '';
  try {
    const d = await api<Page<Collection>>(`/collections?page=${p}&page_size=24`);
    items.value = d.items;
    total.value = d.total;
  } catch (e) {
    error.value = errorText(e);
  } finally {
    busy.value = false;
  }
}
onMounted(() => load());
</script>
<template>
  <main class="content-shell">
    <div class="page-heading">
      <div>
        <h1>收藏夹</h1>
        <p class="muted">按收藏来源浏览，找回当时想留下的内容</p>
      </div>
    </div>
    <EmptyState v-if="error" title="收藏夹暂时无法读取" :text="error" error
      ><button @click="load()">重试</button></EmptyState
    >
    <div v-else-if="busy" class="loading-block">正在加载…</div>
    <div v-else-if="items.length" class="collection-grid">
      <RouterLink
        v-for="item in items"
        :key="item.id"
        :to="{ path: '/', query: { collection_id: item.id } }"
        class="collection-card"
        ><div class="folder-icon" aria-hidden="true">▤</div>
        <div>
          <p class="muted small">{{ kinds[item.kind] || item.kind }}</p>
          <h2>{{ item.title }}</h2>
          <p class="muted">已保存 {{ item.saved_count }} 条视频</p>
        </div>
        <span class="collection-arrow">→</span></RouterLink
      >
    </div>
    <EmptyState
      v-else
      title="还没有收藏夹"
      text="管理员添加来源并完成扫描后，收藏夹会保存在这里。"
    /><Pagination :page="page" :total="total" @change="load" />
  </main>
</template>

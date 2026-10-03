<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { api, errorText } from '../api';
import type { Collection, Page } from '../types';
import EmptyState from '../components/EmptyState.vue';
import Pagination from '../components/Pagination.vue';
import SourceStatus from '../components/SourceStatus.vue';
import type { SourceMonitorState } from '../components/source-monitor';
type MonitoredCollection = Collection & {
  monitor?: SourceMonitorState;
  last_scan_at?: string | null;
};
const items = ref<MonitoredCollection[]>([]),
  error = ref(''),
  busy = ref(false),
  page = ref(1),
  total = ref(0);
const kinds: Record<string, string> = {
  favorite: '收藏夹',
  favorites: '收藏夹',
  ugc_season: '合集',
  series: '系列',
  creator: 'UP 主订阅',
};
async function load(p = 1) {
  page.value = p;
  busy.value = true;
  error.value = '';
  try {
    const d = await api<Page<MonitoredCollection>>(`/collections?page=${p}&page_size=24`);
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
        <h1>收藏与订阅</h1>
        <p class="muted">{{ total }} 个收藏夹、UP 主订阅与合集</p>
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
          <p class="muted small">{{ kinds[item.kind] || '视频集合' }}</p>
          <h2>{{ item.title }}</h2>
          <p class="muted">已保存 {{ item.saved_count }} 条视频</p>
          <SourceStatus
            :monitor="item.monitor"
            :last-scan-at="item.last_scan_at"
            :creator="item.kind === 'creator'"
          />
        </div>
        <span class="collection-arrow">→</span></RouterLink
      >
    </div>
    <EmptyState
      v-else
      title="还没有收藏或订阅"
      text="添加收藏夹或 UP 主备份来源后，已保存的内容会整理在这里。"
    /><Pagination :page="page" :total="total" @change="load" />
  </main>
</template>

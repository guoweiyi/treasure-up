<script setup lang="ts">
import { ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { api, query, errorText, session } from '../api';
import type { Creator, Video, Page } from '../types';
import VideoCard from '../components/VideoCard.vue';
import EmptyState from '../components/EmptyState.vue';
import Pagination from '../components/Pagination.vue';
const route = useRoute(),
  creator = ref<Creator | null>(null),
  items = ref<Video[]>([]),
  q = ref(''),
  sort = ref('newest'),
  page = ref(1),
  total = ref(0),
  error = ref(''),
  busy = ref(false);
const tab = ref<'videos' | 'about'>('videos');
let seq = 0,
  profileSeq = 0;
async function search(p = 1) {
  const n = ++seq;
  page.value = p;
  busy.value = true;
  error.value = '';
  try {
    const data = await api<Page<Video>>(
      `/videos?${query({ creator_id: route.params.id, q: q.value, sort: sort.value, page: p, page_size: 24 })}`,
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
watch(
  () => route.params.id,
  async () => {
    const n = ++profileSeq;
    seq++;
    creator.value = null;
    q.value = '';
    error.value = '';
    try {
      const data = await api<Creator>(`/creators/${route.params.id}`);
      if (n !== profileSeq) return;
      creator.value = data;
      await search();
    } catch (e) {
      if (n === profileSeq) error.value = errorText(e);
    }
  },
  { immediate: true },
);
</script>
<template>
  <main class="content-shell creator-space">
    <header v-if="creator" class="creator-profile">
      <img v-if="creator.avatar_url" :src="creator.avatar_url" alt="" class="avatar profile" /><span
        v-else
        class="avatar profile fallback"
        >{{ creator.name?.slice(0, 1) }}</span
      >
      <div>
        <h1>{{ creator.name }}</h1>
        <p class="muted small">
          UID {{ creator.uid }}
          <span v-if="creator.source_name && creator.source_name !== creator.name"
            >· 平台昵称 {{ creator.source_name }}</span
          >
        </p>
        <p class="preserve-lines">{{ creator.description || '暂无简介' }}</p>
        <div v-if="creator.tags?.length" class="tags">
          <span v-for="tag in creator.tags" :key="tag">{{ tag }}</span>
        </div>
      </div>
      <div class="profile-actions">
        <span class="profile-saved"
          ><strong>{{ creator.saved_count ?? 0 }}</strong
          >已收藏作品</span
        ><RouterLink
          v-if="['admin', 'editor'].includes(session.user?.role || '')"
          :to="{ path: '/admin/creators', query: { edit: creator.id } }"
          class="outline-link"
          >编辑资料</RouterLink
        >
      </div>
    </header>
    <div class="creator-tabs content-tabs">
      <button :class="{ active: tab === 'videos' }" @click="tab = 'videos'">
        作品 <span>{{ creator?.saved_count ?? 0 }}</span></button
      ><button :class="{ active: tab === 'about' }" @click="tab = 'about'">关于 UP 主</button>
    </div>
    <section v-if="tab === 'about' && creator" class="creator-about">
      <h2>个人简介</h2>
      <p class="preserve-lines">{{ creator.description || '暂无简介' }}</p>
      <dl>
        <dt>UID</dt>
        <dd>{{ creator.uid || '未收录' }}</dd>
        <dt v-if="creator.source_name">平台昵称</dt>
        <dd v-if="creator.source_name">{{ creator.source_name }}</dd>
      </dl>
      <template v-if="creator.notes"
        ><h2>收藏笔记</h2>
        <p class="preserve-lines">{{ creator.notes }}</p></template
      >
    </section>
    <template v-else
      ><div class="section-heading">
        <h2>
          TA 的已收藏作品 <span class="muted">{{ total }}</span>
        </h2>
        <form class="search-form compact" @submit.prevent="search()">
          <input v-model="q" placeholder="在这位 UP 的收藏中搜索" aria-label="UP 视频搜索" /><select
            v-model="sort"
            aria-label="排序"
            @change="search()"
          >
            <option value="newest">最近保存</option>
            <option value="oldest">最早保存</option>
            <option value="title">按标题</option></select
          ><button>搜索</button>
        </form>
      </div>
      <EmptyState v-if="error" title="内容暂时无法读取" :text="error" error />
      <div v-else-if="busy" class="loading-block">正在加载…</div>
      <div v-else-if="items.length" class="video-grid">
        <VideoCard v-for="video in items" :key="video.id" :video="video" />
      </div>
      <EmptyState
        v-else
        title="没有匹配的已保存视频"
        text="这里仅展示你已归档的作品。" /><Pagination
        :page="page"
        :total="total"
        @change="search"
    /></template>
  </main>
</template>

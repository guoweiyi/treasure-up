<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, toRefs, watch } from 'vue';
import { ElButton } from 'element-plus';
import { api, date, query } from '../../api';
import {
  createSourceLoader,
  type SourceCategory as Category,
  type SourceChoice as Choice,
  type SourcePage,
  type SourcePickerState,
} from '../../utils/sourceDiscovery';

const props = defineProps<{ accountId: string; kind: string; sourceId: string }>();
const emit = defineEmits<{ select: [choice: Choice] }>();
const category = ref<Category>(props.kind === 'creator' ? 'following' : 'created');
const categories: { key: Category; label: string }[] = [
  { key: 'created', label: '我创建的收藏夹' },
  { key: 'collected', label: '我收藏的收藏夹' },
  { key: 'following', label: '关注的 UP 主' },
];
const search = ref('');
const state = reactive<SourcePickerState>({
  rows: [],
  nextPage: null,
  cached: false,
  loading: false,
  error: '',
  retryAt: undefined,
  retryPending: false,
});
const { rows, nextPage, cached, loading, error, retryAt, retryPending } = toRefs(state);
const loader = createSourceLoader(state, (params) =>
  api<SourcePage>(`/admin/source-discovery?${query(params)}`),
);
const visible = computed(() => {
  const word = search.value.trim().toLocaleLowerCase();
  return rows.value.filter((row) =>
    `${row.title} ${row.owner_name || ''} ${row.source_id}`.toLocaleLowerCase().includes(word),
  );
});
async function load(more = false, refresh = false) {
  await loader.load(props.accountId, category.value, more, refresh);
}
watch(
  () => props.kind,
  (kind) => {
    if (kind === 'creator') category.value = 'following';
    else if (category.value === 'following') category.value = 'created';
  },
);
watch(
  [() => props.accountId, category],
  () => {
    search.value = '';
    void load();
  },
  { immediate: true },
);
onBeforeUnmount(loader.dispose);
</script>

<template>
  <section class="source-picker" aria-label="从 B 站账号选择备份来源">
    <div class="picker-tabs" aria-label="来源分类">
      <button
        v-for="tab in categories"
        :key="tab.key"
        type="button"
        :aria-pressed="category === tab.key"
        @click="category = tab.key"
      >
        {{ tab.label }}
      </button>
    </div>
    <p v-if="category === 'following'" class="muted">按 B 站最常访问顺序列出关注的 UP 主。</p>
    <div class="picker-search">
      <input
        v-model="search"
        @keydown.enter.prevent
        placeholder="搜索已加载的名称、作者或 ID"
        aria-label="搜索已加载来源"
      />
      <el-button :disabled="loading || !accountId" @click="load(false, true)">刷新</el-button>
    </div>
    <p v-if="!accountId" class="muted">选择采集账号后即可读取来源。</p>
    <p v-else-if="loading && !rows.length" role="status" class="muted">正在读取账号来源…</p>
    <p v-else-if="error" role="alert" class="form-error">{{ error }}</p>
    <p v-else-if="!rows.length" class="muted">该分类暂无可见来源，也可以在下方手动填写链接。</p>
    <p v-else-if="!visible.length" class="muted">已加载的来源中没有匹配项，可继续加载更多。</p>
    <p v-if="retryAt" class="muted" role="status">
      {{ retryPending ? '已安排有限自动重试，时间：' : '可在以下时间后手动重试：'
      }}{{ date(new Date(retryAt).toISOString()) }}
      <el-button v-if="retryPending" size="small" text @click="loader.cancelRetry"
        >取消自动重试</el-button
      >
    </p>
    <div class="picker-results">
      <button
        v-for="row in visible"
        :key="`${row.kind}:${row.source_id}`"
        type="button"
        :aria-pressed="row.kind === kind && row.source_id === sourceId"
        @click="emit('select', row)"
      >
        <strong>{{ row.title }}</strong>
        <span
          >{{ row.owner_name ? `${row.owner_name} · ` : ''
          }}{{ row.kind === 'creator' ? 'UP 主' : '收藏夹'
          }}<template v-if="row.media_count != null"> · {{ row.media_count }} 项</template></span
        >
      </button>
    </div>
    <div v-if="rows.length || nextPage || error" class="picker-footer">
      <span class="muted">已加载 {{ rows.length }} 项{{ cached ? ' · 5 分钟内缓存' : '' }}</span>
      <el-button v-if="nextPage" :loading="loading" @click="load(true)">加载更多</el-button>
      <el-button v-else-if="error" :loading="loading" @click="load()">重试</el-button>
    </div>
  </section>
</template>

<style scoped>
.source-picker {
  border: 1px solid var(--line, #dce1e9);
  padding: 14px;
  border-radius: 12px;
}
.picker-tabs,
.picker-search,
.picker-footer {
  display: flex;
  gap: 8px;
  align-items: center;
}
.picker-tabs {
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.picker-tabs button {
  border: 0;
  border-radius: 7px;
  padding: 7px 10px;
  color: inherit;
  background: var(--surface-muted, #f0f3f7);
  cursor: pointer;
}
.picker-tabs button[aria-pressed='true'] {
  background: #dceaff;
  color: #195bb2;
}
.picker-search input {
  min-width: 0;
  flex: 1;
}
.picker-results {
  display: grid;
  gap: 5px;
  max-height: 260px;
  overflow: auto;
  margin-top: 10px;
}
.picker-results button {
  display: grid;
  gap: 3px;
  text-align: left;
  background: transparent;
  color: inherit;
  border: 1px solid transparent;
  border-radius: 8px;
  padding: 9px;
  cursor: pointer;
}
.picker-results button:hover,
.picker-results button[aria-pressed='true'] {
  border-color: #8eb7f1;
  background: var(--surface-muted, #f0f3f7);
}
.picker-results strong {
  overflow-wrap: anywhere;
}
.picker-results span {
  font-size: 12px;
  color: var(--muted, #637084);
}
.picker-footer {
  justify-content: space-between;
  margin-top: 10px;
}
.muted {
  font-size: 12px;
  color: var(--muted, #637084);
}
</style>

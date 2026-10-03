<script setup lang="ts">
import { onMounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import {
  ElAlert,
  ElButton,
  ElFormItem,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElSelect,
  ElTable,
  ElTableColumn,
  vLoading,
} from 'element-plus';
import { api, write, query, bytes, date, errorText } from '../../api';
import type { Row, Page, Video } from '../../types';
import { qualityLabel } from '../../utils/format';
const route = useRoute(),
  videos = ref<Video[]>([]),
  profiles = ref<Row[]>([]),
  videoId = ref(''),
  targetId = ref(''),
  locations = ref<Row[]>([]),
  selected = ref<Video | null>(null),
  totalAssets = ref(0),
  busy = ref(false),
  searching = ref(false),
  actionBusy = ref(''),
  error = ref(''),
  queued = ref<Row | null>(null),
  profileFilter = ref('');
let request = 0,
  searchRequest = 0;
const states: Record<string, string> = {
  ready: '可用',
  retired: '已停用',
  deleted: '已永久删除',
  missing: '资源缺失',
};
const kinds: Record<string, string> = {
  media: '媒体',
  cover: '封面',
  avatar: '头像',
  danmaku: '弹幕',
  danmaku_raw: '原始弹幕',
  subtitle: '字幕',
  subtitle_raw: '原始字幕',
  image: '图片',
  hls_segment: '分片',
  hls_index: '分片索引',
  hls_init: '分片初始化段',
};
async function searchVideos(q = '') {
  const key = ++searchRequest;
  searching.value = true;
  try {
    const data = await api<Page<Video>>(`/videos?${query({ q, page_size: 100 })}`);
    if (key === searchRequest) videos.value = data.items;
  } catch (e) {
    if (key === searchRequest) error.value = errorText(e);
  } finally {
    if (key === searchRequest) searching.value = false;
  }
}
async function load() {
  const key = ++request;
  error.value = '';
  locations.value = [];
  selected.value = null;
  totalAssets.value = 0;
  if (!videoId.value) return;
  busy.value = true;
  try {
    const [video, storage] = await Promise.all([
      api<Video>(`/videos/${videoId.value}`),
      api<{ items: Row[]; total_assets: number }>(`/admin/videos/${videoId.value}/storage`),
    ]);
    if (key !== request) return;
    selected.value = video;
    locations.value = storage.items;
    totalAssets.value = storage.total_assets;
  } catch (e) {
    if (key === request) error.value = errorText(e);
  } finally {
    if (key === request) busy.value = false;
  }
}
async function run(path: string, body?: unknown, method = 'POST') {
  actionBusy.value = path;
  error.value = '';
  try {
    queued.value = await write(path, body, method);
    ElMessage.success('任务已排队，请在任务中心查看执行结果');
  } catch (e) {
    error.value = errorText(e);
  } finally {
    actionBusy.value = '';
  }
}
async function retire(row: Row) {
  try {
    await ElMessageBox.confirm(
      `停用“${row.profile_name}”上的这个副本？文件仍会保留，不释放磁盘空间。完成后可在此恢复。`,
      '停用副本',
      { confirmButtonText: '停用副本', cancelButtonText: '取消', type: 'warning' },
    );
  } catch {
    return;
  }
  await run(`/admin/storage/locations/${row.id}`, undefined, 'DELETE');
}
async function purge(row: Row) {
  try {
    await ElMessageBox.prompt(
      `对“${row.profile_name}”上的此副本提交物理删除。服务端会先验证另一完整副本，其他节点副本保留；云端历史版本是否释放空间取决于桶版本策略。此操作无法从本站恢复，请输入 DELETE 确认。`,
      '永久删除副本',
      {
        confirmButtonText: '永久删除',
        cancelButtonText: '取消',
        inputPattern: /^DELETE$/,
        inputErrorMessage: '请输入 DELETE',
        type: 'warning',
      },
    );
  } catch {
    return;
  }
  await run(`/admin/storage/locations/${row.id}/purge`, { confirm: 'DELETE' });
}
watch(videoId, load);
watch(
  () => route.query.video_id,
  (value) => {
    if (value) videoId.value = String(value);
  },
  { immediate: true },
);
onMounted(async () => {
  await searchVideos();
  try {
    profiles.value = (await api<Page<Row>>('/admin/storage?page_size=100')).items;
  } catch (e) {
    error.value = errorText(e);
  }
});
</script>
<template>
  <section class="admin-panel">
    <div class="section-heading">
      <h2>视频副本</h2>
      <el-button :loading="busy" :disabled="!videoId" @click="load">刷新副本状态</el-button>
    </div>
    <el-select
      v-model="videoId"
      filterable
      remote
      :remote-method="searchVideos"
      :loading="searching"
      placeholder="搜索并选择视频"
      class="replica-video-select"
      ><el-option v-for="video in videos" :key="video.id" :value="video.id" :label="video.title"
    /></el-select>
    <p v-if="selected" class="field-help">
      <RouterLink :to="`/videos/${selected.id}`">{{ selected.title }} →</RouterLink> ·
      {{ totalAssets }} 个资产 / {{ locations.length }} 个位置记录
    </p>
    <div v-if="selected" class="replica-actions">
      <el-select v-model="targetId" placeholder="选择同步目标节点"
        ><el-option
          v-for="profile in profiles.filter((value) => value.enabled)"
          :key="profile.id"
          :value="profile.id"
          :label="profile.name" /></el-select
      ><el-button
        :disabled="!targetId || !!actionBusy"
        :loading="actionBusy === `/admin/videos/${videoId}/sync`"
        @click="run(`/admin/videos/${videoId}/sync`, { target_profile_id: targetId })"
        >同步此视频到节点</el-button
      >
    </div>
    <p v-if="selected" class="field-help">
      同步会复制并校验视频、分片及关联资产；完成后节点才能用于播放。原节点文件保留。
    </p>
  </section>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="mb" /><el-alert
    v-if="queued"
    :title="'任务已排队：' + queued.id + '，请在任务中心确认执行结果，再刷新本页。'"
    type="info"
    :closable="false"
    class="mb"
  />
  <p v-if="queued"><RouterLink to="/admin/jobs" class="text-button">查看任务 →</RouterLink></p>
  <template v-if="selected"
    ><section class="admin-panel">
      <div class="section-heading">
        <h2>播放准备</h2>
        <span class="muted small">无重编码分片与音量分析</span>
      </div>
      <div v-for="part in selected.parts" :key="part.id" class="prepare-part">
        <p>P{{ part.position }} · {{ part.title }}</p>
        <div
          v-for="variant in part.variants.filter((value) => value.kind !== 'hls')"
          :key="variant.id"
          class="prepare-variant"
        >
          <span
            >{{ qualityLabel(variant) }} · {{ variant.video_codec || '原编码' }} /
            {{ variant.audio_codec || '原音轨' }} ·
            {{ variant.kind === 'archive' ? '原档' : '兼容副本' }}</span
          ><el-button
            size="small"
            :disabled="!!actionBusy"
            :loading="actionBusy === `/admin/variants/${variant.id}/prepare`"
            @click="run(`/admin/variants/${variant.id}/prepare`)"
            >准备分片与分析</el-button
          >
        </div>
      </div>
      <p class="field-help">
        只做封装和分析，不重编码原码流，也不承诺浏览器支持所有杜比或 HDR
        格式。分片按需读取，完整观看仍消耗原码率流量。
      </p>
    </section>
    <div class="section-heading">
      <h2>存储位置记录</h2>
      <el-select v-model="profileFilter" clearable placeholder="所有节点" style="width: 220px"
        ><el-option
          v-for="profile in profiles"
          :key="profile.id"
          :value="profile.id"
          :label="profile.name"
      /></el-select>
    </div>
    <el-table
      :data="locations.filter((value) => !profileFilter || value.profile_id === profileFilter)"
      v-loading="busy"
      empty-text="暂无副本记录"
      ><el-table-column label="资产" min-width="250"
        ><template #default="{ row }"
          >{{ kinds[row.kind] || row.kind
          }}<small class="table-subtitle">{{ row.asset_id }}</small></template
        ></el-table-column
      ><el-table-column prop="profile_name" label="节点" min-width="140" /><el-table-column
        label="大小"
        width="120"
        ><template #default="{ row }">{{ bytes(row.size) }}</template></el-table-column
      ><el-table-column label="状态" width="125"
        ><template #default="{ row }">{{
          states[row.state] || row.state
        }}</template></el-table-column
      ><el-table-column label="校验时间" min-width="170"
        ><template #default="{ row }">{{ date(row.verified_at) }}</template></el-table-column
      ><el-table-column label="操作" min-width="260"
        ><template #default="{ row }"
          ><el-button
            v-if="row.state === 'ready'"
            size="small"
            :disabled="!!actionBusy"
            @click="retire(row)"
            >停用副本</el-button
          ><template v-if="row.state === 'retired'"
            ><el-button
              size="small"
              :disabled="!!actionBusy"
              @click="run(`/admin/storage/locations/${row.id}/restore`)"
              >校验并恢复</el-button
            ><el-button
              type="danger"
              plain
              size="small"
              :disabled="!!actionBusy"
              @click="purge(row)"
              >永久删除</el-button
            ></template
          ></template
        ></el-table-column
      ></el-table
    >
    <p class="field-help">
      停用副本可恢复，保留物理文件；永久删除会提交物理删除，云端历史版本是否释放空间取决于桶版本策略。每项操作由后台校验后执行，其他节点副本保留。
    </p></template
  >
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import {
  ElAlert,
  ElButton,
  ElInput,
  ElPagination,
  ElCheckbox,
  ElCheckboxGroup,
  ElDialog,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElSelect,
  ElTable,
  ElTableColumn,
  vLoading,
  type TableInstance,
} from 'element-plus';
import { api, write, query, bytes, date, errorText } from '../../api';
import type { Row, Page, Video } from '../../types';
import { qualityLabel } from '../../utils/format';
import OperationResult from './OperationResult.vue';
const operationOpen = ref(false);
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
const selection = ref<Video[]>([]),
  searchText = ref(''),
  page = ref(1),
  videoTotal = ref(0),
  variantSelections = ref<Record<string, string[] | null>>({}),
  versionVideo = ref<Video | null>(null),
  versionOpen = ref(false),
  versionBusy = ref(''),
  allVersions = ref(true),
  versionIds = ref<string[]>([]),
  batchBusy = ref(false);
type BatchItem = { video_id: string; title?: string; status: string; job?: Row; error?: string };
const batchResult = ref<BatchItem[]>([]);
const videoTable = ref<TableInstance>();
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
async function searchVideos() {
  const key = ++searchRequest;
  searching.value = true;
  try {
    const data = await api<Page<Video>>(
      `/videos?${query({ q: searchText.value, page: page.value, page_size: 20 })}`,
    );
    if (key === searchRequest) {
      videos.value = data.items;
      videoTotal.value = data.total;
    }
  } catch (e) {
    if (key === searchRequest) error.value = errorText(e);
  } finally {
    if (key === searchRequest) searching.value = false;
  }
}
function search() {
  page.value = 1;
  void searchVideos();
}
async function chooseVersions(id: string) {
  versionBusy.value = id;
  error.value = '';
  try {
    versionVideo.value = await api<Video>(`/videos/${id}`);
    const saved = variantSelections.value[id];
    allVersions.value = !saved;
    versionIds.value = saved ? [...saved] : [];
    versionOpen.value = true;
  } catch (e) {
    error.value = errorText(e);
  } finally {
    versionBusy.value = '';
  }
}
function saveVersions() {
  if (!versionVideo.value || (!allVersions.value && !versionIds.value.length)) return;
  variantSelections.value[versionVideo.value.id] = allVersions.value ? null : [...versionIds.value];
  versionOpen.value = false;
}
async function syncSelected() {
  if (!selection.value.length || !targetId.value || batchBusy.value) return;
  const selectedRows = [...selection.value];
  batchBusy.value = true;
  error.value = '';
  batchResult.value = [];
  try {
    const result = await write<{
      items: BatchItem[];
      queued: number;
      existing: number;
      failed: number;
    }>('/admin/storage/sync-batch', {
      target_profile_id: targetId.value,
      items: selectedRows.map((video) => ({
        video_id: video.id,
        variant_ids: variantSelections.value[video.id] ?? null,
      })),
    });
    batchResult.value = result.items.map((item) => ({
      ...item,
      title: selectedRows.find((video) => video.id === item.video_id)?.title,
    }));
    ElMessage({
      type: result.failed ? 'warning' : 'success',
      message: `${result.queued} 项已排队 · ${result.existing} 项已有任务${result.failed ? ` · ${result.failed} 项未提交` : ''}`,
    });
  } catch (e) {
    error.value = errorText(e);
  } finally {
    batchBusy.value = false;
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
async function run(path: string, body?: unknown, method = 'POST', actionKey = path) {
  actionBusy.value = actionKey;
  error.value = '';
  try {
    queued.value = await write(path, body, method);
    operationOpen.value = true;
    ElMessage.success(
      queued.value?.status && queued.value.status !== 'queued'
        ? '已找到现有任务，请查看当前状态'
        : '任务已排队',
    );
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
    <div class="replica-search">
      <el-input
        v-model="searchText"
        placeholder="搜索视频标题、BV 号"
        clearable
        @keyup.enter="search"
        @clear="search"
      />
      <el-button :loading="searching" @click="search">搜索</el-button>
    </div>
    <el-table
      ref="videoTable"
      :data="videos"
      row-key="id"
      v-loading="searching"
      empty-text="暂无视频"
      @selection-change="(rows: Video[]) => (selection = rows)"
    >
      <el-table-column
        type="selection"
        reserve-selection
        width="44"
        :selectable="
          (row: Video) => selection.length < 50 || selection.some((item) => item.id === row.id)
        "
      />
      <el-table-column label="视频" min-width="260"
        ><template #default="{ row }">
          <button class="replica-title" @click="videoId = row.id">{{ row.title }}</button>
          <small class="table-subtitle">{{
            row.creators?.map((creator: { name: string }) => creator.name).join(' / ')
          }}</small>
        </template></el-table-column
      >
      <el-table-column label="同步版本" width="190"
        ><template #default="{ row }">
          <el-button
            text
            size="small"
            :loading="versionBusy === row.id"
            :disabled="!!versionBusy || batchBusy"
            @click="chooseVersions(row.id)"
            >{{
              variantSelections[row.id]
                ? `已选 ${variantSelections[row.id]?.length} 个版本`
                : '全部版本'
            }}
            · 选择</el-button
          >
        </template></el-table-column
      >
      <el-table-column label="副本" width="96"
        ><template #default="{ row }">
          <el-button text size="small" @click="videoId = row.id">查看</el-button>
        </template></el-table-column
      >
    </el-table>
    <el-pagination
      v-model:current-page="page"
      :page-size="20"
      :total="videoTotal"
      layout="prev, pager, next, total"
      @current-change="searchVideos"
    />
    <div class="replica-actions">
      <span class="muted small">已选 {{ selection.length }} 个视频</span>
      <el-button
        v-if="selection.length"
        text
        :disabled="batchBusy"
        @click="videoTable?.clearSelection()"
        >清空选择</el-button
      >
      <el-select v-model="targetId" placeholder="选择目标存储源" :disabled="batchBusy">
        <el-option
          v-for="profile in profiles.filter((value) => value.enabled)"
          :key="profile.id"
          :value="profile.id"
          :label="profile.name"
        />
      </el-select>
      <el-button
        type="primary"
        :disabled="!targetId || !selection.length || selection.length > 50"
        :loading="batchBusy"
        @click="syncSelected"
        >同步所选视频</el-button
      >
    </div>
    <p class="field-help">
      每次最多选择 50 个视频，可跨页勾选。分片、封面与关联资料一起复制；后台按队列处理，原文件保留。
    </p>
    <p v-if="selection.length > 50" class="result-error">
      一次最多同步 50 个视频，请减少勾选后提交。
    </p>
    <el-table v-if="batchResult.length" :data="batchResult" class="batch-results">
      <el-table-column prop="title" label="本次提交" min-width="220" />
      <el-table-column label="结果" min-width="190"
        ><template #default="{ row }">
          <span :class="{ 'result-error': row.status === 'failed' }">{{
            row.status === 'failed' ? row.error : row.status === 'existing' ? '已有任务' : '已排队'
          }}</span>
        </template></el-table-column
      >
      <el-table-column width="130"
        ><template #default="{ row }"
          ><el-button
            v-if="row.job"
            text
            @click="
              queued = row.job;
              operationOpen = true;
            "
            >进度与结果</el-button
          ></template
        ></el-table-column
      >
    </el-table>
    <p v-if="selected" class="field-help">
      <RouterLink :to="`/videos/${selected.id}`">{{ selected.title }} →</RouterLink> ·
      {{ totalAssets }} 个资产 / {{ locations.length }} 个位置记录
    </p>
    <p v-if="selected" class="field-help">
      同步会复制并校验视频、分片及关联资产；完成后节点才能用于播放。原节点文件保留。
    </p>
  </section>
  <el-dialog
    v-model="versionOpen"
    title="选择同步版本"
    width="min(620px, 94vw)"
    :close-on-click-modal="false"
  >
    <template v-if="versionVideo">
      <p class="version-title">{{ versionVideo.title }}</p>
      <el-checkbox v-model="allVersions">全部已保存版本</el-checkbox>
      <el-checkbox-group v-if="!allVersions" v-model="versionIds" class="version-options">
        <div v-for="part in versionVideo.parts" :key="part.id">
          <p class="muted small">P{{ part.position }} · {{ part.title }}</p>
          <el-checkbox
            v-for="variant in part.variants.filter((item) => item.kind !== 'hls')"
            :key="variant.id"
            :value="variant.id"
          >
            {{ qualityLabel(variant) }} · {{ variant.video_codec || '原编码' }} /
            {{ variant.audio_codec || '原音轨' }} ·
            {{ variant.kind === 'archive' ? '原档' : '兼容副本' }}
          </el-checkbox>
        </div>
      </el-checkbox-group>
      <p class="field-help">所选版本已有的播放分片会自动同步，封面、弹幕、字幕等资料一并保留。</p>
    </template>
    <template #footer
      ><el-button @click="versionOpen = false">取消</el-button
      ><el-button
        type="primary"
        :disabled="!allVersions && !versionIds.length"
        @click="saveVersions"
        >确定</el-button
      ></template
    >
  </el-dialog>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="mb" /><el-alert
    v-if="queued"
    title="可以查看后台任务的当前状态与执行结果。"
    type="info"
    :closable="false"
    class="mb"
  />
  <p v-if="queued"><el-button @click="operationOpen = true">查看进度与结果</el-button></p>
  <OperationResult v-model:open="operationOpen" :job="queued" @completed="load" />
  <template v-if="selected"
    ><section class="admin-panel">
      <div class="section-heading">
        <h2>播放准备</h2>
        <span class="muted small">分片、音量分析与独立兼容副本</span>
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
          >
          <div class="prepare-buttons">
            <el-button
              size="small"
              :disabled="!!actionBusy"
              :loading="actionBusy === `/admin/variants/${variant.id}/prepare:hls`"
              @click="
                run(
                  `/admin/variants/${variant.id}/prepare`,
                  { package: true, analyze_loudness: false },
                  'POST',
                  `/admin/variants/${variant.id}/prepare:hls`,
                )
              "
              >准备 HLS 分片</el-button
            ><el-button
              size="small"
              :disabled="!!actionBusy"
              :loading="actionBusy === `/admin/variants/${variant.id}/prepare:loudness`"
              @click="
                run(
                  `/admin/variants/${variant.id}/prepare`,
                  { package: false, analyze_loudness: true },
                  'POST',
                  `/admin/variants/${variant.id}/prepare:loudness`,
                )
              "
              >分析音量</el-button
            ><el-button
              v-if="variant.kind === 'archive'"
              size="small"
              :disabled="!!actionBusy"
              :loading="actionBusy === `/admin/variants/${variant.id}/compatible`"
              @click="run(`/admin/variants/${variant.id}/compatible`)"
              >生成兼容副本</el-button
            >
          </div>
        </div>
      </div>
      <p class="field-help">
        原档可直接播放，无需预先执行这两项操作。HLS
        分片不重编码原码流，但会额外保存接近原文件大小的数据；
        音量分析需读取并解码整条音轨，不生成新音频。两项可分别按需执行，也不承诺浏览器支持所有杜比或
        HDR 格式。
      </p>
      <p class="field-help">
        兼容副本会保留原档。EC-3 音轨优先保留原视频画面，仅将声音转换为 AAC 立体声，不含
        Atmos；其他格式按兼容规则处理，可能转换画面。HDR 转 SDR
        暂不支持。重复操作会显示已有任务，失败或暂停的任务需在任务中心继续处理。
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
<style scoped>
.replica-search {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
  max-width: 560px;
}
.replica-title {
  border: 0;
  background: transparent;
  padding: 0;
  text-align: left;
  cursor: pointer;
  color: inherit;
  line-height: 1.6;
}
.replica-title:hover {
  color: var(--el-color-primary);
}
.el-pagination {
  margin-top: 16px;
}
.batch-results {
  margin-top: 18px;
}
.result-error {
  color: var(--el-color-danger);
}
.version-title {
  margin-top: 0;
  font-weight: 600;
}
.version-options {
  max-height: 45vh;
  overflow: auto;
  margin-top: 14px;
}
.version-options .el-checkbox {
  display: flex;
  margin: 6px 0;
  height: auto;
  white-space: normal;
}
.prepare-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.prepare-buttons .el-button + .el-button {
  margin-left: 0;
}
</style>

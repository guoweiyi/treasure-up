<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import {
  ElAlert,
  ElButton,
  ElCheckbox,
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElOption,
  ElSelect,
  ElSwitch,
  vLoading,
} from 'element-plus';
import { api, write, errorText, loadDisplaySettings } from '../../api';
import type { Row, Page } from '../../types';
import IngestThrottleFields from './IngestThrottleFields.vue';
import CommentBudgetFields from './CommentBudgetFields.vue';
import { commentBudgetDefaults } from '../../utils/commentBudget';
const busy = ref(false),
  saving = ref(false),
  error = ref(''),
  actionError = ref(''),
  queued = ref<Row | null>(null),
  accounts = ref<Row[]>([]);
const form = reactive<Row>({
  display: { site_name: 'Treasure Up', default_danmaku: true },
  ingest: {
    ...commentBudgetDefaults,
    quality: 'best',
    create_compatible_copy: true,
    prefer_h264: false,
    prefer_dolby_vision: true,
    prefer_dolby_atmos: true,
    request_interval_seconds: 3,
    video_interval_seconds: 60,
    interval_jitter_seconds: 10,
    risk_cooldown_seconds: 900,
    download_rate_bytes: null,
    fragment_concurrency: 1,
    refresh_danmaku: false,
    request_budget: 100,
    download_media: true,
    fetch_comments: true,
    fetch_danmaku: true,
    fetch_subtitles: true,
    include_auto_subtitles: true,
    max_download_bytes: 30000000000,
    max_pages: 100,
  },
  statistics: { enabled: false, account_id: null, interval_hours: 6, refresh_danmaku: false },
  playback: {
    package_long_videos: true,
    min_duration_seconds: 300,
    segment_seconds: 6,
    analyze_loudness: true,
    probe_bytes: 65536,
  },
  backup: { destination: '', key_id: '', interval_hours: 12, retention_days: 30, enabled: false },
});
const qualities = [
  { value: 'best', label: '最高可见画质' },
  { value: '4320p', label: '最高 8K（4320P）' },
  { value: '2160p', label: '最高 4K（2160P）' },
  { value: '1440p', label: '最高 1440P' },
  { value: '1080p', label: '最高 1080P' },
  { value: '720p', label: '最高 720P' },
  { value: '480p', label: '最高 480P' },
  { value: '360p', label: '最高 360P' },
];
async function load() {
  busy.value = true;
  error.value = '';
  try {
    const [settings, choices] = await Promise.allSettled([
      api<Row>('/admin/settings'),
      api<Page<Row>>('/admin/accounts?page_size=100'),
    ]);
    if (settings.status === 'rejected') throw settings.reason;
    for (const key of ['display', 'ingest', 'statistics', 'backup', 'playback'])
      Object.assign(form[key], settings.value[key] || {});
    if (form.ingest.quality === '8k') form.ingest.quality = '4320p';
    if (form.ingest.quality === '4k') form.ingest.quality = '2160p';
    if (choices.status === 'fulfilled') accounts.value = choices.value.items;
    else actionError.value = '采集账号列表读取失败：' + errorText(choices.reason);
  } catch (e) {
    error.value = errorText(e);
  } finally {
    busy.value = false;
  }
}
async function save() {
  saving.value = true;
  actionError.value = '';
  try {
    await write(
      '/admin/settings',
      {
        display: { ...form.display },
        ingest: { ...form.ingest, download_rate_bytes: form.ingest.download_rate_bytes || null },
        statistics: { ...form.statistics, account_id: form.statistics.account_id || null },
        backup: { ...form.backup },
        playback: { ...form.playback },
      },
      'PATCH',
    );
    await loadDisplaySettings();
    ElMessage.success('配置已保存');
  } catch (e) {
    actionError.value = errorText(e);
  } finally {
    saving.value = false;
  }
}
async function refreshStatistics() {
  saving.value = true;
  actionError.value = '';
  try {
    queued.value = await write('/admin/statistics/refresh');
    ElMessage.success(
      `本批新增 ${queued.value?.queued ?? 0} 个统计更新任务${queued.value?.remaining_pending ? '，后续批次将继续处理' : ''}`,
    );
  } catch (e) {
    actionError.value = errorText(e);
  } finally {
    saving.value = false;
  }
}
onMounted(load);
</script>
<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" /><el-form
    v-else
    class="settings-form"
    label-position="top"
    novalidate
    v-loading="busy"
    @submit.prevent="save"
  >
    <section class="admin-panel">
      <h2>网站展示</h2>
      <el-form-item label="网站名称"
        ><el-input v-model="form.display.site_name" maxlength="100" /></el-form-item
      ><el-form-item label="默认显示弹幕"
        ><el-switch v-model="form.display.default_danmaku" />
        <p class="field-help">首次使用播放器时生效；已有浏览器偏好优先。</p></el-form-item
      >
    </section>
    <section class="admin-panel">
      <h2>采集画质与内容</h2>
      <div class="form-two-columns">
        <el-form-item label="目标画质"
          ><el-select v-model="form.ingest.quality"
            ><el-option
              v-for="option in qualities"
              :key="option.value"
              :value="option.value"
              :label="option.label" /></el-select></el-form-item
        ><el-form-item label="单轮请求预算"
          ><el-input-number
            v-model="form.ingest.request_budget"
            :min="1"
            :max="10000" /></el-form-item
        ><el-form-item label="单视频下载上限（字节）"
          ><el-input-number
            v-model="form.ingest.max_download_bytes"
            :min="1000000"
            :max="500000000000"
            :step="1"
            :controls="false" /></el-form-item
        ><el-form-item label="分页上限"
          ><el-input-number v-model="form.ingest.max_pages" :min="1" :max="10000"
        /></el-form-item>
      </div>
      <div class="settings-switches">
        <el-checkbox v-model="form.ingest.download_media">下载媒体</el-checkbox
        ><el-checkbox v-model="form.ingest.create_compatible_copy"
          >按需生成浏览器兼容副本</el-checkbox
        ><el-checkbox v-model="form.ingest.prefer_h264">优先 H.264</el-checkbox
        ><el-checkbox v-model="form.ingest.prefer_dolby_vision">优先杜比视界原档</el-checkbox
        ><el-checkbox v-model="form.ingest.prefer_dolby_atmos">优先杜比全景声原档</el-checkbox
        ><el-checkbox v-model="form.ingest.fetch_comments">采集评论</el-checkbox
        ><el-checkbox v-model="form.ingest.fetch_danmaku">采集弹幕</el-checkbox
        ><el-checkbox v-model="form.ingest.fetch_subtitles">采集字幕</el-checkbox
        ><el-checkbox v-model="form.ingest.include_auto_subtitles">包括自动字幕</el-checkbox>
      </div>
      <p class="field-help">
        以账号实际可见的源版本为准。杜比偏好用于保存对应原档，不代表当前浏览器具备解码能力。兼容副本保留原档并增加处理时间和空间占用。EC-3
        音轨优先保留原视频流（包括 HDR），仅将声音转换为 AAC 立体声，不含 Atmos；HDR 转 SDR
        暂不支持，会记录具体状态。
      </p>
      <CommentBudgetFields :value="form.ingest" />
    </section>
    <section class="admin-panel">
      <h2>采集节奏</h2>
      <IngestThrottleFields :value="form.ingest" />
    </section>
    <section class="admin-panel">
      <div class="section-heading">
        <h2>统计与弹幕更新</h2>
        <el-button :loading="saving" @click="refreshStatistics">立即刷新统计</el-button>
      </div>
      <el-form-item label="定时更新 B 站统计"
        ><el-switch v-model="form.statistics.enabled"
      /></el-form-item>
      <div class="form-two-columns">
        <el-form-item label="更新使用的账号"
          ><el-select v-model="form.statistics.account_id" clearable placeholder="选择已配置账号"
            ><el-option
              v-for="account in accounts"
              :key="account.id"
              :label="account.name"
              :value="account.id" /></el-select></el-form-item
        ><el-form-item label="更新间隔（小时）"
          ><el-input-number v-model="form.statistics.interval_hours" :min="1" :max="720"
        /></el-form-item>
      </div>
      <el-checkbox v-model="form.statistics.refresh_danmaku">更新统计时重新采集弹幕</el-checkbox>
      <p class="field-help">
        更新播放、点赞、投币、收藏等来源统计。此处展示采集时的快照，不会增加 B
        站播放或互动。定时更新使用已保存的配置；修改后请先保存。
      </p>
    </section>
    <section class="admin-panel">
      <h2>播放与分发</h2>
      <div class="settings-switches">
        <el-checkbox v-model="form.playback.package_long_videos">为长视频准备原码流分片</el-checkbox
        ><el-checkbox v-model="form.playback.analyze_loudness">分析音量平衡参数</el-checkbox>
      </div>
      <div class="form-two-columns">
        <el-form-item label="分片准备的视频时长下限（秒）"
          ><el-input-number
            v-model="form.playback.min_duration_seconds"
            :min="0"
            :max="86400" /></el-form-item
        ><el-form-item label="目标分片时长（秒）"
          ><el-input-number
            v-model="form.playback.segment_seconds"
            :min="2"
            :max="20" /></el-form-item
        ><el-form-item label="每节点测速读取上限（字节）"
          ><el-input-number
            v-model="form.playback.probe_bytes"
            :min="16384"
            :max="131072"
            :step="1"
        /></el-form-item>
      </div>
      <p class="field-help">
        HLS
        使用原码流分片，改善起播与跳转，不会自动降画质。音量分析不修改原始文件；播放器可选衰减，空间音频或多声道音频绕过。
      </p>
    </section>
    <section class="admin-panel">
      <h2>独立备份</h2>
      <el-form-item label="启用定期备份"><el-switch v-model="form.backup.enabled" /></el-form-item
      ><el-form-item label="独立备份目录"
        ><el-input
          v-model="form.backup.destination"
          placeholder="服务器上的独立挂载目录" /></el-form-item
      ><el-form-item label="密钥标识 key_id"
        ><el-input v-model="form.backup.key_id"
      /></el-form-item>
      <div class="form-two-columns">
        <el-form-item label="备份周期（小时）"
          ><el-input-number
            v-model="form.backup.interval_hours"
            :min="1"
            :max="720" /></el-form-item
        ><el-form-item label="保留天数（策略记录）"
          ><el-input-number v-model="form.backup.retention_days" :min="1" :max="3650"
        /></el-form-item>
      </div>
      <el-alert
        title="备份目录必须独立于主媒体目录，可使用 NAS。密钥通过 TREASURE_BACKUP_KEY 或 TREASURE_BACKUP_KEY_FILE 单独配置。当前版本不自动删除备份。"
        type="info"
        :closable="false"
      />
    </section>
    <el-alert v-if="actionError" :title="actionError" type="error" :closable="false" class="mb" />
    <p v-if="queued" class="field-help">
      本批新增 {{ queued.queued }} 个任务<span v-if="queued.queued_total != null"
        >，累计排队 {{ queued.queued_total }} 个</span
      >。<span v-if="queued.remaining_pending">后续批次仍会继续处理，无需重复点击。</span>
      <RouterLink to="/admin/jobs">查看执行状态 →</RouterLink>
    </p>
    <el-button type="primary" :loading="saving" :disabled="busy" native-type="submit"
      >保存配置</el-button
    ></el-form
  >
</template>

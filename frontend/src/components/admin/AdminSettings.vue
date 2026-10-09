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
  display: { site_name: 'Treasure Up', default_danmaku: true, allow_guest_access: false },
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
  statistics: {
    enabled: true,
    account_id: null,
    interval_hours: 6,
    refresh_danmaku: false,
    refresh_comments: true,
  },
  playback: {
    package_long_videos: false,
    min_duration_seconds: 300,
    min_size_mb: 64,
    segment_seconds: 6,
    analyze_loudness: false,
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
      <el-form-item label="允许未登录用户浏览与播放">
        <el-switch v-model="form.display.allow_guest_access" />
        <p class="field-help">
          默认关闭。开启后，任何可访问此站点的人都能浏览视频、UP
          主、收藏夹和评论，并播放已保存视频；采集和管理仍需登录。
        </p>
      </el-form-item>
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
        ><el-checkbox v-model="form.ingest.create_compatible_copy">按需生成兼容副本</el-checkbox
        ><el-checkbox v-model="form.ingest.prefer_h264">优先 H.264</el-checkbox
        ><el-checkbox v-model="form.ingest.prefer_dolby_vision">优先杜比视界原档</el-checkbox
        ><el-checkbox v-model="form.ingest.prefer_dolby_atmos">优先杜比全景声原档</el-checkbox
        ><el-checkbox v-model="form.ingest.fetch_comments">采集评论</el-checkbox
        ><el-checkbox v-model="form.ingest.fetch_danmaku">采集弹幕</el-checkbox
        ><el-checkbox v-model="form.ingest.fetch_subtitles">采集字幕</el-checkbox
        ><el-checkbox v-model="form.ingest.include_auto_subtitles">包括自动字幕</el-checkbox>
      </div>
      <p class="field-help">
        以账号实际可见的源版本为准。杜比偏好用于保存对应原档，不代表当前浏览器具备解码能力。普通
        MP4（H.264、HEVC 或 AV1 视频，AAC 单声道或立体声）不自动生成兼容副本。EC-3、FLAC
        等不兼容音频仅转为 AAC 立体声，保留原视频流；副本不含 Atmos，仍需设备支持原视频编码和
        HDR。旧设备可手动生成 H.264 兼容副本，HDR 转 SDR
        暂不支持。副本保留原档并增加处理时间和空间占用。 HLS
        分片独立按播放设置准备，不受是否生成兼容副本影响。
      </p>
      <CommentBudgetFields :value="form.ingest" />
    </section>
    <section class="admin-panel">
      <h2>采集节奏</h2>
      <IngestThrottleFields :value="form.ingest" />
    </section>
    <section class="admin-panel">
      <div class="section-heading">
        <h2>统计与互动数据更新</h2>
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
      <div class="settings-switches">
        <el-checkbox v-model="form.statistics.refresh_comments">同时更新热门与置顶评论</el-checkbox>
        <el-checkbox v-model="form.statistics.refresh_danmaku">同时更新弹幕</el-checkbox>
      </div>
      <p class="field-help">
        更新播放、点赞、投币、收藏等来源统计。此处展示采集时的快照，不会增加 B
        站播放或互动。评论沿用上方条数与素材预算。未指定账号时自动选择可用账号；修改后请先保存。
      </p>
    </section>
    <section class="admin-panel">
      <h2>播放与分发</h2>
      <div class="settings-switches">
        <el-checkbox v-model="form.playback.package_long_videos"
          >自动准备大视频分片（额外占用空间）</el-checkbox
        ><el-checkbox v-model="form.playback.analyze_loudness"
          >自动分析全片音量（耗时）</el-checkbox
        >
      </div>
      <div class="form-two-columns">
        <el-form-item label="分片准备的视频时长下限（秒）"
          ><el-input-number
            v-model="form.playback.min_duration_seconds"
            :min="0"
            :max="86400" /></el-form-item
        ><el-form-item label="或文件体积达到（MiB）"
          ><el-input-number
            v-model="form.playback.min_size_mb"
            :min="32"
            :max="4096" /></el-form-item
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
        两项默认关闭，原档可直接播放。只有开启自动分片后，达到时长或体积任一条件才会准备分片；
        分片不降画质，但会额外保存接近原文件大小的数据。音量分析需要读取并解码整条音轨，不修改原文件。
        可在“存储副本”中针对单个版本分别执行，建议仅在起播、跳转或音量需要改善时使用。
        关闭自动设置会阻止新的任务及尚未开始的自动处理；正在处理的任务可在任务中心暂停。
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

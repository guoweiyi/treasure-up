<script setup lang="ts">
import { ElFormItem, ElInputNumber, ElOption, ElSelect } from 'element-plus';
import type { Row } from '../../types';
import CommentBudgetFields from './CommentBudgetFields.vue';
defineProps<{ value: Row }>();
const booleans = [
  ['download_media', '下载视频原档'],
  ['fetch_comments', '保存评论'],
  ['fetch_danmaku', '保存弹幕'],
  ['fetch_subtitles', '保存字幕'],
  ['include_auto_subtitles', '包含自动字幕'],
  ['create_compatible_copy', '按需生成兼容副本'],
  ['prefer_h264', '优先 H.264'],
  ['prefer_dolby_vision', '优先杜比视界'],
  ['prefer_dolby_atmos', '优先杜比全景声'],
] as const;
const numbers = [
  { key: 'request_interval_seconds', label: 'API 请求间隔（秒）', min: 8, max: 120 },
  { key: 'video_interval_seconds', label: '下载结束后的间隔（秒）', min: 180, max: 3600 },
  { key: 'interval_jitter_seconds', label: '随机延迟上限（秒）', min: 0, max: 300 },
  { key: 'risk_cooldown_seconds', label: '风险冷却（秒）', min: 1800, max: 86400 },
  { key: 'request_budget', label: '每轮请求预算', min: 1, max: 10000 },
  { key: 'max_pages', label: '每轮分页上限', min: 1, max: 10000 },
  { key: 'max_download_bytes', label: '单次下载上限（字节）', min: 1000000, max: 500000000000 },
  { key: 'download_rate_bytes', label: '下载限速（字节/秒）', min: 10000, max: 1000000000 },
];
</script>
<template>
  <details class="policy-fields">
    <summary>本次采集策略（可选）</summary>
    <p class="field-help">
      未填写的项目沿用系统策略。普通 MP4（H.264、HEVC 或 AV1 视频，AAC
      单声道或立体声）不自动生成兼容副本。 EC-3、FLAC 等不兼容音频仅转为
      AAC，保留原视频和原档，仍需设备支持视频编码。 旧设备可手动生成 H.264 兼容副本；HLS
      分片仍按播放设置准备。
    </p>
    <el-form-item label="画质上限"
      ><el-select v-model="value.quality" clearable placeholder="使用系统设置"
        ><el-option value="best" label="最高可用画质" /><el-option
          v-for="quality in ['4320p', '2160p', '1440p', '1080p', '720p', '480p', '360p']"
          :key="quality"
          :value="quality"
          :label="quality.toUpperCase()" /></el-select
    ></el-form-item>
    <div class="form-two-columns">
      <el-form-item v-for="[key, label] in booleans" :key="key" :label="label"
        ><el-select v-model="value[key]" clearable placeholder="使用系统设置"
          ><el-option :value="true" label="启用" /><el-option
            :value="false"
            label="关闭" /></el-select
      ></el-form-item>
    </div>
    <CommentBudgetFields :value="value" inherit />
    <details>
      <summary>请求与下载限制</summary>
      <p class="field-help">视频与分片均串行下载；实际间隔采用本次策略与系统设置中较长的值。</p>
      <div class="form-two-columns">
        <el-form-item v-for="item in numbers" :key="item.key" :label="item.label"
          ><el-input-number
            v-model="value[item.key]"
            :min="item.min"
            :max="item.max"
            :controls="false"
            placeholder="使用系统设置"
        /></el-form-item>
      </div>
    </details>
  </details>
</template>
<style scoped>
.policy-fields {
  margin: 14px 0;
}
.policy-fields summary {
  cursor: pointer;
  margin-bottom: 12px;
}
</style>

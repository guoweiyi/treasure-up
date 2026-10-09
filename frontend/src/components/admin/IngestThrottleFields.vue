<script setup lang="ts">
import { ElFormItem, ElInputNumber } from 'element-plus';
import type { Row } from '../../types';
defineProps<{ value: Row }>();
</script>
<template>
  <div class="form-two-columns">
    <el-form-item label="同账号 API 请求间隔（秒）"
      ><el-input-number
        v-model="value.request_interval_seconds"
        :min="8"
        :max="120"
        :step="1" /></el-form-item
    ><el-form-item label="下载结束后的间隔（秒）"
      ><el-input-number
        v-model="value.video_interval_seconds"
        :min="180"
        :max="3600"
        :step="0.1" /></el-form-item
    ><el-form-item label="随机延迟上限（秒）"
      ><el-input-number
        v-model="value.interval_jitter_seconds"
        :min="0"
        :max="300"
        :step="0.1" /></el-form-item
    ><el-form-item label="风险响应冷却（秒）"
      ><el-input-number
        v-model="value.risk_cooldown_seconds"
        :min="1800"
        :max="86400"
        :step="60" /></el-form-item
    ><el-form-item label="下载限速（字节 / 秒，可留空）"
      ><el-input-number
        v-model="value.download_rate_bytes"
        :min="10000"
        :max="1000000000"
        :step="1"
        :controls="false"
      />
      <p class="field-help">留空使用未设限速策略。1,000,000 字节 / 秒约为 1 MB/s。</p></el-form-item
    >
  </div>
  <p class="field-help">
    所有账号的视频下载共用一个串行队列，分片也逐个下载；每个视频或分 P 结束后至少等待 180 秒。
    同账号 API 请求至少间隔 8 秒；更长的系统间隔也会应用于已排队任务。 遇到限流至少冷却 30
    分钟，连续受限会延长等待，并遵守源站要求的等待时间。
  </p>
</template>

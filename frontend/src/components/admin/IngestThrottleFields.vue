<script setup lang="ts">
import { ElFormItem, ElInputNumber } from 'element-plus';
import type { Row } from '../../types';
defineProps<{ value: Row }>();
</script>
<template>
  <div class="form-two-columns">
    <el-form-item label="请求最小间隔（秒）"
      ><el-input-number
        v-model="value.request_interval_seconds"
        :min="1"
        :max="120"
        :step="0.1" /></el-form-item
    ><el-form-item label="视频之间的间隔（秒）"
      ><el-input-number
        v-model="value.video_interval_seconds"
        :min="10"
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
        :min="60"
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
    ><el-form-item label="媒体分片并发"
      ><el-input-number v-model="value.fragment_concurrency" :min="1" :max="3"
    /></el-form-item>
  </div>
  <p class="field-help">
    间隔与冷却按账号调度；遇到风险响应会暂停该账号的请求。新配置用于之后创建的任务。
  </p>
</template>
